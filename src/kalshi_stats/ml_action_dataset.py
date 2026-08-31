from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import sqlite3
import statistics
from typing import Iterable, Iterator, Mapping, Sequence

from .historical_replay import side_price
from .ml_dataset import FEATURE_COLUMNS as STATE_FEATURE_COLUMNS
from .paper_broker import floor_contract_count, taker_fee_estimate


EVIDENCE_TYPE = "HISTORICAL_COUNTERFACTUAL_ACTION_PATH_V1"
TRADE_NOTIONAL = 1.0
EXCLUDED_LEAKAGE_FIELDS = (
    "result",
    "settlement_ts",
    "status",
    "close_time",
)
OUTCOME_CLASSES = (
    "TP",
    "SL",
    "SETTLEMENT_WIN",
    "SETTLEMENT_LOSS",
    "AMBIGUOUS",
    "UNRESOLVED",
)

EXIT_PROFILES = (
    ("tp05_sl05", 0.05, 0.05, False),
    ("tp10_sl05", 0.10, 0.05, False),
    ("tp15_sl05", 0.15, 0.05, False),
    ("tp20_sl10", 0.20, 0.10, False),
    ("tp25_sl10", 0.25, 0.10, False),
    ("settle", None, None, True),
)

HISTORICAL_SAMPLING_LIMITATIONS = (
    "Kalshi paths are one-minute OHLC candles, not tick-by-tick order-book events.",
    "When both barriers occur in one candle, their ordering is unknowable and the label is AMBIGUOUS.",
    "Entries use the sampled side ask at the decision candle close; historical IOC latency, depth, queue priority, and partial fills are unavailable.",
    "YES exits use sampled YES bid OHLC. NO exits use reciprocal YES ask OHLC because historical candles do not store a separate NO book.",
    "A stop crossed only by an intra-candle low has a reliable SL class but no reliable execution price or P&L.",
    "Take-profit execution at the target approximates a resting limit and does not model queue position.",
    "Fees reuse the repository taker-fee estimate and remain approximate; ordinary settlement has no modeled exit fee.",
    "Many rows share a market, timestamp, and future path; later evaluation must split by market and must not treat action rows as independent evidence.",
)


@dataclass(frozen=True)
class ActionDefinition:
    action_id: str
    side: str
    exit_profile: str
    take_profit_delta: float | None
    stop_loss_delta: float | None
    settlement_hold: bool


def _canonical_actions() -> tuple[ActionDefinition, ...]:
    actions = []
    for side in ("YES", "NO"):
        for profile, tp_delta, sl_delta, settlement_hold in EXIT_PROFILES:
            actions.append(
                ActionDefinition(
                    action_id=f"action:v1:{side.lower()}:{profile}",
                    side=side,
                    exit_profile=profile,
                    take_profit_delta=tp_delta,
                    stop_loss_delta=sl_delta,
                    settlement_hold=settlement_hold,
                )
            )
    return tuple(actions)


CANONICAL_ACTIONS = _canonical_actions()


@dataclass(frozen=True)
class PathObservation:
    observed_ts: int
    yes_bid_close: float
    yes_bid_low: float
    yes_bid_high: float
    yes_ask_close: float
    yes_ask_low: float
    yes_ask_high: float


@dataclass(frozen=True)
class Settlement:
    result: str | None
    settlement_ts: int | None


@dataclass(frozen=True)
class ActionOutcome:
    outcome_class: str
    exit_price: float | None
    exit_ts: int | None
    holding_seconds: int | None
    gross_pnl: float | None
    entry_fee: float
    exit_fee: float | None
    net_pnl: float | None
    net_return_on_cost: float | None
    maximum_favorable_excursion: float | None
    maximum_adverse_excursion: float | None
    replay_compatible_outcome: str
    resolution_detail: str


@dataclass(frozen=True)
class ActionRow:
    market_ticker: str
    observed_ts: int
    side: str
    action_id: str
    exit_profile: str
    take_profit_delta: float | None
    stop_loss_delta: float | None
    settlement_hold: bool
    state_features: tuple[float | None, ...]
    entry_price: float
    contract_count: float
    entry_notional: float
    outcome: ActionOutcome


def _iso_timestamp(value: object) -> int | None:
    if value is None or not str(value).strip():
        return None
    return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())


def _side_bid_bar(observation: PathObservation, side: str) -> tuple[float, float, float]:
    if side == "YES":
        return (
            observation.yes_bid_close,
            observation.yes_bid_low,
            observation.yes_bid_high,
        )
    if side == "NO":
        return (
            1.0 - observation.yes_ask_close,
            1.0 - observation.yes_ask_high,
            1.0 - observation.yes_ask_low,
        )
    raise ValueError(f"unknown side: {side}")


def _settlement_outcome(side: str, settlement: Settlement) -> str:
    if settlement.result not in {"yes", "no"}:
        return "UNRESOLVED"
    return "SETTLEMENT_WIN" if settlement.result == side.lower() else "SETTLEMENT_LOSS"


def _replay_compatible_label(
    *,
    action: ActionDefinition,
    entry_price: float,
    future: Sequence[PathObservation],
    settlement: Settlement,
) -> str:
    if action.settlement_hold:
        return _settlement_outcome(action.side, settlement)
    tp_price = entry_price + float(action.take_profit_delta)
    sl_price = entry_price - float(action.stop_loss_delta)
    for observation in future:
        close, _, _ = _side_bid_bar(observation, action.side)
        if close >= tp_price:
            return "TP"
        if close <= sl_price:
            return "SL"
    return _settlement_outcome(action.side, settlement)


def _finalize_outcome(
    *,
    outcome_class: str,
    resolution_detail: str,
    entry_ts: int,
    entry_price: float,
    count: float,
    entry_fee: float,
    exit_price: float | None,
    exit_ts: int | None,
    exit_fee: float | None,
    mfe: float | None,
    mae: float | None,
    replay_compatible_outcome: str,
) -> ActionOutcome:
    if exit_price is None:
        gross_pnl = None
        net_pnl = None
        net_return = None
    else:
        gross_pnl = count * (exit_price - entry_price)
        net_pnl = gross_pnl - entry_fee - float(exit_fee or 0.0)
        capital = count * entry_price + entry_fee
        net_return = None if capital <= 0 else net_pnl / capital
    return ActionOutcome(
        outcome_class=outcome_class,
        exit_price=exit_price,
        exit_ts=exit_ts,
        holding_seconds=None if exit_ts is None else max(0, exit_ts - entry_ts),
        gross_pnl=gross_pnl,
        entry_fee=entry_fee,
        exit_fee=exit_fee,
        net_pnl=net_pnl,
        net_return_on_cost=net_return,
        maximum_favorable_excursion=mfe,
        maximum_adverse_excursion=mae,
        replay_compatible_outcome=replay_compatible_outcome,
        resolution_detail=resolution_detail,
    )


def label_action(
    *,
    action: ActionDefinition,
    entry_ts: int,
    entry_price: float,
    future: Sequence[PathObservation],
    settlement: Settlement,
) -> ActionOutcome:
    """Label one action using observations strictly after ``entry_ts``."""
    if any(observation.observed_ts <= entry_ts for observation in future):
        raise ValueError("future path contains a non-future observation")

    count = floor_contract_count(TRADE_NOTIONAL, entry_price)
    entry_fee = taker_fee_estimate(count, entry_price)
    replay_label = _replay_compatible_label(
        action=action,
        entry_price=entry_price,
        future=future,
        settlement=settlement,
    )
    observed_highs: list[float] = []
    observed_lows: list[float] = []

    if not action.settlement_hold:
        tp_price = entry_price + float(action.take_profit_delta)
        sl_price = entry_price - float(action.stop_loss_delta)
        for observation in future:
            close, low, high = _side_bid_bar(observation, action.side)
            observed_highs.append(high)
            observed_lows.append(low)
            mfe = max(0.0, max(observed_highs) - entry_price)
            mae = min(0.0, min(observed_lows) - entry_price)
            tp_hit = high >= tp_price
            sl_hit = low <= sl_price
            if tp_hit and sl_hit:
                return _finalize_outcome(
                    outcome_class="AMBIGUOUS",
                    resolution_detail="TP_AND_SL_IN_SAME_ONE_MINUTE_CANDLE",
                    entry_ts=entry_ts,
                    entry_price=entry_price,
                    count=count,
                    entry_fee=entry_fee,
                    exit_price=None,
                    exit_ts=observation.observed_ts,
                    exit_fee=None,
                    mfe=mfe,
                    mae=mae,
                    replay_compatible_outcome=replay_label,
                )
            if tp_hit:
                exit_fee = taker_fee_estimate(count, tp_price)
                return _finalize_outcome(
                    outcome_class="TP",
                    resolution_detail="TP_ONLY_IN_FIRST_BARRIER_CANDLE",
                    entry_ts=entry_ts,
                    entry_price=entry_price,
                    count=count,
                    entry_fee=entry_fee,
                    exit_price=tp_price,
                    exit_ts=observation.observed_ts,
                    exit_fee=exit_fee,
                    mfe=mfe,
                    mae=mae,
                    replay_compatible_outcome=replay_label,
                )
            if sl_hit:
                reliable_exit = close if close <= sl_price else None
                exit_fee = (
                    None if reliable_exit is None else taker_fee_estimate(count, reliable_exit)
                )
                return _finalize_outcome(
                    outcome_class="SL",
                    resolution_detail=(
                        "SL_WITH_OBSERVED_CLOSE_EXECUTION"
                        if reliable_exit is not None
                        else "SL_INTRACANDLE_EXECUTION_PRICE_UNOBSERVED"
                    ),
                    entry_ts=entry_ts,
                    entry_price=entry_price,
                    count=count,
                    entry_fee=entry_fee,
                    exit_price=reliable_exit,
                    exit_ts=observation.observed_ts,
                    exit_fee=exit_fee,
                    mfe=mfe,
                    mae=mae,
                    replay_compatible_outcome=replay_label,
                )
    else:
        for observation in future:
            _, low, high = _side_bid_bar(observation, action.side)
            observed_highs.append(high)
            observed_lows.append(low)

    outcome_class = _settlement_outcome(action.side, settlement)
    if outcome_class == "UNRESOLVED" or settlement.settlement_ts is None:
        return _finalize_outcome(
            outcome_class="UNRESOLVED",
            resolution_detail="MISSING_COMPLETED_SETTLEMENT",
            entry_ts=entry_ts,
            entry_price=entry_price,
            count=count,
            entry_fee=entry_fee,
            exit_price=None,
            exit_ts=None,
            exit_fee=None,
            mfe=(None if not observed_highs else max(0.0, max(observed_highs) - entry_price)),
            mae=(None if not observed_lows else min(0.0, min(observed_lows) - entry_price)),
            replay_compatible_outcome=replay_label,
        )
    settlement_price = 1.0 if outcome_class == "SETTLEMENT_WIN" else 0.0
    return _finalize_outcome(
        outcome_class=outcome_class,
        resolution_detail="ACTUAL_COMPLETED_MARKET_SETTLEMENT",
        entry_ts=entry_ts,
        entry_price=entry_price,
        count=count,
        entry_fee=entry_fee,
        exit_price=settlement_price,
        exit_ts=settlement.settlement_ts,
        exit_fee=0.0,
        mfe=(None if not observed_highs else max(0.0, max(observed_highs) - entry_price)),
        mae=(None if not observed_lows else min(0.0, min(observed_lows) - entry_price)),
        replay_compatible_outcome=replay_label,
    )


def _validate_schema(connection: sqlite3.Connection) -> None:
    feature_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(historical_market_features)")
    }
    candle_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(candles)")}
    required_features = {
        "market_ticker",
        "observed_ts",
        "candle_source",
        "yes_bid_close",
        "yes_ask_close",
        *STATE_FEATURE_COLUMNS,
    }
    required_candles = {
        "market_ticker",
        "end_period_ts",
        "period_interval",
        "source",
        "yes_bid_low",
        "yes_bid_high",
        "yes_ask_low",
        "yes_ask_high",
    }
    if missing := required_features - feature_columns:
        raise ValueError(f"historical feature schema is missing: {sorted(missing)}")
    if missing := required_candles - candle_columns:
        raise ValueError(f"historical candle schema is missing: {sorted(missing)}")
    if set(STATE_FEATURE_COLUMNS) & set(EXCLUDED_LEAKAGE_FIELDS):
        raise AssertionError("leakage field entered the state feature whitelist")


def _load_settlements(connection: sqlite3.Connection) -> dict[str, Settlement]:
    # Settlement data is intentionally loaded separately from state X.
    rows = connection.execute(
        "SELECT ticker, result, settlement_ts FROM markets WHERE result IN ('yes', 'no')"
    ).fetchall()
    return {
        str(row["ticker"]): Settlement(
            result=str(row["result"]).lower(),
            settlement_ts=_iso_timestamp(row["settlement_ts"]),
        )
        for row in rows
    }


def _load_state_paths(
    connection: sqlite3.Connection,
) -> dict[str, list[tuple[sqlite3.Row, PathObservation]]]:
    # This SELECT is the complete, explicit X whitelist plus quote/path fields.
    # It deliberately contains no settlement result or settlement timestamp.
    sql = f"""
        SELECT
            h.market_ticker,
            h.observed_ts,
            {", ".join(f"h.{name}" for name in STATE_FEATURE_COLUMNS)},
            c.yes_bid_low,
            c.yes_bid_high,
            c.yes_ask_low,
            c.yes_ask_high
        FROM historical_market_features h
        JOIN candles c
          ON c.market_ticker = h.market_ticker
         AND c.end_period_ts = h.observed_ts
         AND c.period_interval = 1
         AND c.source = h.candle_source
        WHERE h.yes_bid_close IS NOT NULL
          AND h.yes_ask_close IS NOT NULL
          AND c.yes_bid_low IS NOT NULL
          AND c.yes_bid_high IS NOT NULL
          AND c.yes_ask_low IS NOT NULL
          AND c.yes_ask_high IS NOT NULL
        ORDER BY h.market_ticker, h.observed_ts
    """
    paths: dict[str, list[tuple[sqlite3.Row, PathObservation]]] = defaultdict(list)
    for row in connection.execute(sql):
        ticker = str(row["market_ticker"])
        paths[ticker].append(
            (
                row,
                PathObservation(
                    observed_ts=int(row["observed_ts"]),
                    yes_bid_close=float(row["yes_bid_close"]),
                    yes_bid_low=float(row["yes_bid_low"]),
                    yes_bid_high=float(row["yes_bid_high"]),
                    yes_ask_close=float(row["yes_ask_close"]),
                    yes_ask_low=float(row["yes_ask_low"]),
                    yes_ask_high=float(row["yes_ask_high"]),
                ),
            )
        )
    return paths


def iter_action_rows(connection: sqlite3.Connection) -> Iterator[ActionRow]:
    """Stream eligible historical state×action rows without storing a huge artifact."""
    connection.row_factory = sqlite3.Row
    _validate_schema(connection)
    settlements = _load_settlements(connection)
    paths = _load_state_paths(connection)
    for ticker, path in sorted(
        paths.items(), key=lambda item: (item[1][0][1].observed_ts, item[0])
    ):
        settlement = settlements.get(ticker, Settlement(None, None))
        observations = tuple(item[1] for item in path)
        for index, (state, _) in enumerate(path):
            entry_ts = int(state["observed_ts"])
            features = tuple(
                None if state[name] is None else float(state[name])
                for name in STATE_FEATURE_COLUMNS
            )
            for action in CANONICAL_ACTIONS:
                entry_price = side_price(state, action.side, "ask")
                if entry_price is None or not 0.0 < entry_price < 1.0:
                    continue
                if not action.settlement_hold:
                    tp_price = entry_price + float(action.take_profit_delta)
                    sl_price = entry_price - float(action.stop_loss_delta)
                    # Preserve historical replay eligibility semantics at binary bounds.
                    if tp_price > 0.99 or sl_price < 0.01:
                        continue
                count = floor_contract_count(TRADE_NOTIONAL, entry_price)
                if count < 0.01:
                    continue
                yield ActionRow(
                    market_ticker=ticker,
                    observed_ts=entry_ts,
                    side=action.side,
                    action_id=action.action_id,
                    exit_profile=action.exit_profile,
                    take_profit_delta=action.take_profit_delta,
                    stop_loss_delta=action.stop_loss_delta,
                    settlement_hold=action.settlement_hold,
                    state_features=features,
                    entry_price=entry_price,
                    contract_count=count,
                    entry_notional=count * entry_price,
                    outcome=label_action(
                        action=action,
                        entry_ts=entry_ts,
                        entry_price=entry_price,
                        future=observations[index + 1 :],
                        settlement=settlement,
                    ),
                )


def _descriptive(values: Iterable[float]) -> dict[str, float | int | None]:
    data = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not data:
        return {"n": 0, "mean": None, "median": None, "min": None, "max": None}
    return {
        "n": len(data),
        "mean": statistics.fmean(data),
        "median": statistics.median(data),
        "min": data[0],
        "max": data[-1],
    }


def build_summary(
    connection: sqlite3.Connection,
    *,
    source_db: str,
) -> dict[str, object]:
    action_counts: Counter[str] = Counter()
    profile_counts: Counter[str] = Counter()
    action_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    profile_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    gross_by_action: dict[str, list[float]] = defaultdict(list)
    net_by_action: dict[str, list[float]] = defaultdict(list)
    return_by_action: dict[str, list[float]] = defaultdict(list)
    all_gross: list[float] = []
    all_net: list[float] = []
    all_returns: list[float] = []
    markets: set[str] = set()
    timestamps: set[tuple[str, int]] = set()
    sl_unpriced = 0
    for row in iter_action_rows(connection):
        action_counts[row.action_id] += 1
        profile_counts[row.exit_profile] += 1
        outcome_class = row.outcome.outcome_class
        action_outcomes[row.action_id][outcome_class] += 1
        profile_outcomes[row.exit_profile][outcome_class] += 1
        markets.add(row.market_ticker)
        timestamps.add((row.market_ticker, row.observed_ts))
        if row.outcome.gross_pnl is not None:
            all_gross.append(row.outcome.gross_pnl)
            gross_by_action[row.action_id].append(row.outcome.gross_pnl)
        if row.outcome.net_pnl is not None:
            all_net.append(row.outcome.net_pnl)
            net_by_action[row.action_id].append(row.outcome.net_pnl)
        if row.outcome.net_return_on_cost is not None:
            all_returns.append(row.outcome.net_return_on_cost)
            return_by_action[row.action_id].append(row.outcome.net_return_on_cost)
        if (
            outcome_class == "SL"
            and row.outcome.resolution_detail == "SL_INTRACANDLE_EXECUTION_PRICE_UNOBSERVED"
        ):
            sl_unpriced += 1

    def detail_for(key: str, *, by_action: bool) -> dict[str, object]:
        counts = action_counts if by_action else profile_counts
        outcomes = action_outcomes if by_action else profile_outcomes
        total = counts[key]
        ambiguous = outcomes[key]["AMBIGUOUS"]
        unresolved = outcomes[key]["UNRESOLVED"]
        result: dict[str, object] = {
            "rows": total,
            "outcome_counts": {name: outcomes[key][name] for name in OUTCOME_CLASSES},
            "unambiguous_rows": total - ambiguous - unresolved,
            "ambiguous_rows": ambiguous,
            "ambiguity_rate": None if total == 0 else ambiguous / total,
            "unresolved_rows": unresolved,
            "unresolved_rate": None if total == 0 else unresolved / total,
        }
        if by_action:
            result["gross_pnl"] = _descriptive(gross_by_action[key])
            result["estimated_net_pnl"] = _descriptive(net_by_action[key])
            result["estimated_net_return_on_cost"] = _descriptive(
                return_by_action[key]
            )
        return result

    total = sum(action_counts.values())
    ambiguous_total = sum(counts["AMBIGUOUS"] for counts in action_outcomes.values())
    unresolved_total = sum(counts["UNRESOLVED"] for counts in action_outcomes.values())
    warnings = [
        "DATASET VALIDATION ONLY: these retrospective counterfactual labels are not an edge or profitability claim.",
        "Action rows are correlated; future folds must split by market, never by action row.",
        "Ineligible TP/SL actions whose barriers cross binary bounds are omitted, matching replay eligibility semantics.",
    ]
    if sl_unpriced:
        warnings.append(
            f"{sl_unpriced} SL rows lack exact P&L because only an intra-candle low established the stop crossing."
        )
    return {
        "evidence_type": EVIDENCE_TYPE,
        "source_db": str(Path(source_db).resolve()),
        "action_definitions": [asdict(action) for action in CANONICAL_ACTIONS],
        "feature_whitelist": list(STATE_FEATURE_COLUMNS),
        "excluded_leakage_fields": list(EXCLUDED_LEAKAGE_FIELDS),
        "outcome_classes": list(OUTCOME_CLASSES),
        "number_of_markets": len(markets),
        "number_of_decision_timestamps": len(timestamps),
        "total_action_rows": total,
        "unambiguous_rows": total - ambiguous_total - unresolved_total,
        "ambiguous_rows": ambiguous_total,
        "ambiguity_rate": None if total == 0 else ambiguous_total / total,
        "unresolved_rows": unresolved_total,
        "unresolved_rate": None if total == 0 else unresolved_total / total,
        "action_rows_by_profile": dict(sorted(profile_counts.items())),
        "by_profile": {
            key: detail_for(key, by_action=False) for key in sorted(profile_counts)
        },
        "by_action": {
            action.action_id: detail_for(action.action_id, by_action=True)
            for action in CANONICAL_ACTIONS
        },
        "gross_pnl": _descriptive(all_gross),
        "estimated_net_pnl": _descriptive(all_net),
        "estimated_net_return_on_cost": _descriptive(all_returns),
        "historical_sampling_limitations": list(HISTORICAL_SAMPLING_LIMITATIONS),
        "warnings": warnings,
    }


def write_summary(summary: Mapping[str, object], path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the historical action/path V1 summary")
    parser.add_argument("--db", required=True, help="Path to a frozen SQLite backup")
    parser.add_argument(
        "--summary",
        default="reports/ml_action_dataset_summary.json",
        help="Generated summary output path",
    )
    args = parser.parse_args()
    uri = f"file:{Path(args.db).resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        summary = build_summary(connection, source_db=args.db)
    finally:
        connection.close()
    write_summary(summary, args.summary)
    print(json.dumps({
        "summary": str(Path(args.summary).resolve()),
        "total_action_rows": summary["total_action_rows"],
        "number_of_markets": summary["number_of_markets"],
        "number_of_decision_timestamps": summary["number_of_decision_timestamps"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
