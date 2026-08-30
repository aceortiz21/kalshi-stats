from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from statistics import mean, median

from .models import (
    ActiveMarketSideView,
    LiveScenarioMatch,
    MatrixCell,
    Observation,
    ScenarioDefinition,
    ScenarioOccurrence,
    ScenarioSummary,
)


PRICE_BUCKETS = [
    (0.00, 0.09, "0-9c"),
    (0.10, 0.19, "10-19c"),
    (0.20, 0.29, "20-29c"),
    (0.30, 0.39, "30-39c"),
    (0.40, 0.49, "40-49c"),
    (0.50, 0.59, "50-59c"),
    (0.60, 0.69, "60-69c"),
    (0.70, 0.79, "70-79c"),
    (0.80, 0.89, "80-89c"),
    (0.90, 1.00, "90-100c"),
]

TIME_BUCKETS = [
    (600, 900, "10m+"),
    (300, 599, "5-10m"),
    (180, 299, "3-5m"),
    (120, 179, "2-3m"),
    (60, 119, "1-2m"),
    (0, 59, "<1m"),
]

MATRIX_TIME_BUCKETS = [
    (600, 900, "10-15m left"),
    (300, 599, "5-10m left"),
    (180, 299, "3-5m left"),
    (120, 179, "2-3m left"),
    (60, 119, "1-2m left"),
    (0, 59, "<1m left"),
]

SCENARIO_TARGETS = [0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]
PRICE_AFTER_SECONDS = [30, 60, 120, 180, 300]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _opposite(side: str) -> str:
    return "no" if side == "yes" else "yes"


def _iso_to_ts(value: str) -> int:
    if value is None:
        return 0

    value = value.replace("Z", "+00:00")

    if "." in value:
        base, remainder = value.split(".", 1)

        if "+" in remainder:
            fraction, tz = remainder.split("+", 1)
            fraction = fraction[:6].ljust(6, "0")
            value = f"{base}.{fraction}+{tz}"

        elif "-" in remainder:
            fraction, tz = remainder.split("-", 1)
            fraction = fraction[:6].ljust(6, "0")
            value = f"{base}.{fraction}-{tz}"

    timestamp = int(__import__("datetime").datetime.fromisoformat(value).timestamp())
    return timestamp


def _mid(bid: float, ask: float) -> float:
    if bid > 0 and ask > 0:
        return (bid + ask) / 2
    if ask > 0:
        return ask
    return bid


def _side_close(observation: Observation, side: str) -> float:
    return observation.yes_close if side == "yes" else 1.0 - observation.yes_close


def _side_low_high(observation: Observation, side: str) -> tuple[float, float]:
    if side == "yes":
        return observation.yes_low, observation.yes_high
    return 1.0 - observation.yes_high, 1.0 - observation.yes_low


def _eventual_win(result: str | None, side: str) -> bool:
    return result == side


def _bucket_label(value: int, buckets: list[tuple[int, int, str]]) -> str:
    return next((label for low, high, label in buckets if low <= value <= high), "unknown")


def _wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if total == 0:
        return None, None
    p = successes / total
    denom = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    low = max(0.0, (centre - margin) / denom)
    high = min(1.0, (centre + margin) / denom)
    return low, high


def _chunked(items: list[str], size: int = 500) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _build_series_from_rows(
    market_ticker: str,
    open_time: str | None,
    close_time: str | None,
    candle_rows: list[sqlite3.Row],
    snapshot_rows: list[sqlite3.Row],
    trade_rows: list[sqlite3.Row],
) -> list[Observation]:
    if close_time is None:
        return []
    close_ts = _iso_to_ts(close_time)
    if candle_rows:
        if open_time is None:
            return []

        open_ts = _iso_to_ts(open_time)

        # One-minute candles are timestamped by the end of their interval.
        # Keep only candles belonging to the actual 15-minute market window.
        valid_candles = [
            row
            for row in candle_rows
            if open_ts < int(row["end_period_ts"]) <= close_ts
        ]

        if not valid_candles:
            return []

        # Exclude obviously incomplete histories so partial API responses do
        # not silently become historical examples.
        first_ts = int(valid_candles[0]["end_period_ts"])
        last_ts = int(valid_candles[-1]["end_period_ts"])

        if first_ts > open_ts + 120 or last_ts < close_ts - 60:
            return []

        return [
            Observation(
                observed_ts=int(row["end_period_ts"]),
                seconds_remaining=close_ts - int(row["end_period_ts"]),
                elapsed_seconds=900 - (close_ts - int(row["end_period_ts"])),
                yes_close=float(row["price_close"]),
                yes_low=float(row["price_low"]),
                yes_high=float(row["price_high"]),
                source="candle",
            )
            for row in valid_candles
        ]

    if snapshot_rows:
        observations: list[Observation] = []
        for row in snapshot_rows:
            observed_ts = _iso_to_ts(row["collected_at"])
            yes_close = _mid(float(row["yes_bid"]), float(row["yes_ask"]))
            seconds_remaining = max(0, close_ts - observed_ts)
            observations.append(
                Observation(
                    observed_ts=observed_ts,
                    seconds_remaining=seconds_remaining,
                    elapsed_seconds=900 - seconds_remaining,
                    yes_close=yes_close,
                    yes_low=yes_close,
                    yes_high=yes_close,
                    source="snapshot",
                )
            )
        return observations

    if not trade_rows or open_time is None:
        return []
    open_ts = _iso_to_ts(open_time)
    first_trade_ts = _iso_to_ts(trade_rows[0]["created_time"])
    last_trade_ts = _iso_to_ts(trade_rows[-1]["created_time"])
    if first_trade_ts > open_ts + 120 or last_trade_ts < close_ts - 60:
        return []
    return [
        Observation(
            observed_ts=_iso_to_ts(row["created_time"]),
            seconds_remaining=max(0, close_ts - _iso_to_ts(row["created_time"])),
            elapsed_seconds=900 - max(0, close_ts - _iso_to_ts(row["created_time"])),
            yes_close=float(row["yes_price"]),
            yes_low=float(row["yes_price"]),
            yes_high=float(row["yes_price"]),
            source="trade",
        )
        for row in trade_rows
    ]


def chronological_market_split(
    markets: list[sqlite3.Row],
    discovery_fraction: float = 0.80,
) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    """Split markets chronologically into discovery and holdout samples.

    Markets are ordered by close_time. The earlier portion is used for
    candidate discovery; the later portion is reserved for validation.
    """
    if not 0.0 < discovery_fraction < 1.0:
        raise ValueError("discovery_fraction must be between 0 and 1")

    ordered = sorted(markets, key=lambda market: market["close_time"])

    if len(ordered) < 2:
        return ordered, []

    split_index = int(len(ordered) * discovery_fraction)
    split_index = max(1, min(split_index, len(ordered) - 1))

    return ordered[:split_index], ordered[split_index:]


def _settled_markets_with_data(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT ticker, result, open_time, close_time
        FROM markets
        WHERE result IN ('yes', 'no')
          AND (
              EXISTS (SELECT 1 FROM candles c WHERE c.market_ticker = markets.ticker)
              OR EXISTS (SELECT 1 FROM trades t WHERE t.market_ticker = markets.ticker)
              OR EXISTS (SELECT 1 FROM quote_snapshots s WHERE s.market_ticker = markets.ticker)
          )
        ORDER BY close_time
        """
    ).fetchall()


def _build_series(
    connection: sqlite3.Connection,
    market_ticker: str,
    open_time: str | None = None,
    close_time: str | None = None,
) -> list[Observation]:
    candle_rows = connection.execute(
        """
        SELECT end_period_ts, price_close, price_low, price_high
        FROM candles
        WHERE market_ticker = ?
        ORDER BY end_period_ts
        """,
        (market_ticker,),
    ).fetchall()

    snapshot_rows = connection.execute(
        """
        SELECT collected_at, yes_bid, yes_ask
        FROM quote_snapshots
        WHERE market_ticker = ?
        ORDER BY collected_at
        """,
        (market_ticker,),
    ).fetchall()

    trade_rows = connection.execute(
        """
        SELECT created_time, yes_price
        FROM trades
        WHERE market_ticker = ?
        ORDER BY created_time
        """,
        (market_ticker,),
    ).fetchall()
    if open_time is None or close_time is None:
        if open_time is None or close_time is None:
            market_row = connection.execute(
                "SELECT open_time, close_time FROM markets WHERE ticker = ?",
                (market_ticker,),
            ).fetchone()
            open_time = market_row["open_time"]
            close_time = market_row["close_time"]
    return _build_series_from_rows(market_ticker, open_time, close_time, candle_rows, snapshot_rows, trade_rows)


def _build_series_map(
    connection: sqlite3.Connection,
    settled_markets: list[sqlite3.Row] | None = None,
) -> dict[str, list[Observation]]:
    settled_markets = settled_markets or _settled_markets_with_data(connection)
    if not settled_markets:
        return {}
    market_rows = {
        market["ticker"]: (market["open_time"], market["close_time"])
        for market in settled_markets
    }
    candle_map: dict[str, list[sqlite3.Row]] = defaultdict(list)
    snapshot_map: dict[str, list[sqlite3.Row]] = defaultdict(list)
    trade_map: dict[str, list[sqlite3.Row]] = defaultdict(list)
    tickers = list(market_rows)
    for ticker_chunk in _chunked(tickers):
        placeholders = ",".join("?" for _ in ticker_chunk)
        for row in connection.execute(
            f"""
            SELECT market_ticker, end_period_ts, price_close, price_low, price_high
            FROM candles
            WHERE market_ticker IN ({placeholders})
            ORDER BY market_ticker, end_period_ts
            """,
            ticker_chunk,
        ):
            candle_map[row["market_ticker"]].append(row)
        for row in connection.execute(
            f"""
            SELECT market_ticker, collected_at, yes_bid, yes_ask
            FROM quote_snapshots
            WHERE market_ticker IN ({placeholders})
            ORDER BY market_ticker, collected_at
            """,
            ticker_chunk,
        ):
            snapshot_map[row["market_ticker"]].append(row)
        for row in connection.execute(
            f"""
            SELECT market_ticker, created_time, yes_price
            FROM trades
            WHERE market_ticker IN ({placeholders})
            ORDER BY market_ticker, created_time
            """,
            ticker_chunk,
        ):
            trade_map[row["market_ticker"]].append(row)
    series_map: dict[str, list[Observation]] = {}
    for ticker, (open_time, close_time) in market_rows.items():
        series = _build_series_from_rows(
            ticker,
            open_time,
            close_time,
            candle_map.get(ticker, []),
            snapshot_map.get(ticker, []),
            trade_map.get(ticker, []),
        )
        if series:
            series_map[ticker] = series
    return series_map


def _price_at_or_after(
    future: list[Observation],
    side: str,
    offset_seconds: int,
    *,
    entry_ts: int,
    entry_remaining: int,
) -> float | None:
    if not future or entry_remaining < offset_seconds:
        return None

    for observation in future:
        if observation.observed_ts - entry_ts >= offset_seconds:
            return _side_close(observation, side)

    return None


def _match_trigger(definition: ScenarioDefinition, observation: Observation, trigger_side: str) -> tuple[bool, float]:
    close_price = _side_close(observation, trigger_side)
    low_price, high_price = _side_low_high(observation, trigger_side)
    touches_band = not (
        high_price < definition.trigger_price_min or low_price > definition.trigger_price_max
    )
    if not touches_band:
        return False, close_price
    if not (
        definition.elapsed_seconds_min <= observation.elapsed_seconds <= definition.elapsed_seconds_max
    ):
        return False, close_price
    if not (
        definition.seconds_remaining_min <= observation.seconds_remaining <= definition.seconds_remaining_max
    ):
        return False, close_price
    return True, _clamp(close_price, definition.trigger_price_min, definition.trigger_price_max)


def _find_occurrence_indices(
    definition: ScenarioDefinition, series: list[Observation], trigger_side: str
) -> list[tuple[int, float]]:
    matches: list[tuple[int, float]] = []
    in_range = False
    last_exit_ts = -10**18
    for index, observation in enumerate(series):
        matched, trigger_price = _match_trigger(definition, observation, trigger_side)
        if matched:
            if definition.occurrence_mode == "first_per_market":
                return [(index, trigger_price)]
            if not in_range and observation.observed_ts - last_exit_ts >= definition.cooldown_seconds:
                matches.append((index, trigger_price))
                in_range = True
            continue
        if in_range:
            in_range = False
            last_exit_ts = observation.observed_ts
    return matches


def _build_occurrence(
    definition: ScenarioDefinition,
    market_result: str | None,
    market_ticker: str,
    series: list[Observation],
    trigger_side: str,
    matched_index: int,
    trigger_price: float,
) -> ScenarioOccurrence:
    traded_side = trigger_side if definition.trade_side == "same" else _opposite(trigger_side)
    entry_price = trigger_price if definition.trade_side == "same" else 1.0 - trigger_price
    entry_observation = series[matched_index]

    # A trade observation is an exact point-in-time price, so including that
    # observation in the path is safe. A 1-minute OHLC candle is different:
    # its high/low may have occurred before its close. Because the trigger
    # entry is represented by the candle close, using that same candle's
    # high/low as a subsequent move would introduce intrabar look-ahead.
    if entry_observation.source == "candle":
        future = series[matched_index + 1:]
    else:
        future = series[matched_index:]

    eventual_win = _eventual_win(market_result, traded_side)
    settlement_price = 1.0 if eventual_win else 0.0

    if future:
        future_highs = [_side_low_high(observation, traded_side)[1] for observation in future]
        future_lows = [_side_low_high(observation, traded_side)[0] for observation in future]

        best_price = max(future_highs)
        worst_price = min(future_lows)

        best_index = future_highs.index(best_price)
        worst_index = future_lows.index(worst_price)

        time_to_best = future[best_index].observed_ts - entry_observation.observed_ts
        time_to_worst = future[worst_index].observed_ts - entry_observation.observed_ts

        max_favorable_excursion = best_price - entry_price
        max_adverse_excursion = entry_price - worst_price

        max_favorable_excursion_pct = (
            None if entry_price == 0 else max_favorable_excursion / entry_price
        )
        max_adverse_excursion_pct = (
            None if entry_price == 0 else max_adverse_excursion / entry_price
        )
    else:
        best_price = None
        worst_price = None
        time_to_best = None
        time_to_worst = None
        max_favorable_excursion = None
        max_adverse_excursion = None
        max_favorable_excursion_pct = None
        max_adverse_excursion_pct = None

    price_after_seconds = {
        offset: _price_at_or_after(
            future,
            traded_side,
            offset,
            entry_ts=entry_observation.observed_ts,
            entry_remaining=entry_observation.seconds_remaining,
        )
        for offset in PRICE_AFTER_SECONDS
    }

    target_hit_seconds: dict[float, int | None] = {}
    target_profit: dict[float, float] = {}

    for target in SCENARIO_TARGETS:
        if target <= entry_price:
            target_hit_seconds[target] = None
            target_profit[target] = settlement_price - entry_price
            continue

        hit_seconds = None

        for observation in future:
            _, future_high = _side_low_high(observation, traded_side)
            if future_high >= target:
                hit_seconds = observation.observed_ts - entry_observation.observed_ts
                break

        target_hit_seconds[target] = hit_seconds
        exit_price = target if hit_seconds is not None else settlement_price
        target_profit[target] = exit_price - entry_price

    return ScenarioOccurrence(
        scenario_id=definition.id,
        market_ticker=market_ticker,
        trigger_side=trigger_side,
        traded_side=traded_side,
        trigger_ts=entry_observation.observed_ts,
        entry_price=entry_price,
        seconds_remaining=entry_observation.seconds_remaining,
        elapsed_seconds=entry_observation.elapsed_seconds,
        eventual_win=eventual_win,
        best_subsequent_price=best_price,
        worst_subsequent_price=worst_price,
        max_favorable_excursion=max_favorable_excursion,
        max_adverse_excursion=max_adverse_excursion,
        max_favorable_excursion_pct=max_favorable_excursion_pct,
        max_adverse_excursion_pct=max_adverse_excursion_pct,
        time_to_best_price=time_to_best,
        time_to_worst_price=time_to_worst,
        price_after_seconds=price_after_seconds,
        target_hit_seconds=target_hit_seconds,
        target_profit=target_profit,
    )


def _safe_median(values: list[float]) -> float | None:
    return median(values) if values else None


def _safe_mean(values: list[float]) -> float | None:
    return mean(values) if values else None


def _time_breakdown(occurrences: list[ScenarioOccurrence]) -> dict[str, dict[str, float | int | None]]:
    groups: dict[str, list[ScenarioOccurrence]] = defaultdict(list)
    for occurrence in occurrences:
        groups[_bucket_label(occurrence.seconds_remaining, TIME_BUCKETS)].append(occurrence)
    result: dict[str, dict[str, float | int | None]] = {}
    for _, _, label in TIME_BUCKETS:
        items = groups.get(label, [])
        if not items:
            result[label] = {"n": 0, "win_rate": None}
            continue
        result[label] = {
            "n": len(items),
            "win_rate": sum(item.eventual_win for item in items) / len(items),
            "median_max_price": _safe_median(
                [
                    item.best_subsequent_price
                    for item in items
                    if item.best_subsequent_price is not None
                ]
            ),
        }
    return result


def _summarize(definition: ScenarioDefinition, occurrences: list[ScenarioOccurrence]) -> ScenarioSummary:
    if not occurrences:
        return ScenarioSummary(
            definition=definition,
            occurrences=0,
            path_observations=0,
            unique_markets=0,
            win_rate=None,
            win_rate_ci_low=None,
            win_rate_ci_high=None,
            avg_entry_price=None,
            median_entry_price=None,
            avg_best_subsequent_price=None,
            median_best_subsequent_price=None,
            avg_worst_subsequent_price=None,
            median_worst_subsequent_price=None,
            avg_max_favorable_excursion=None,
            median_max_favorable_excursion=None,
            avg_max_adverse_excursion=None,
            median_max_adverse_excursion=None,
            avg_max_favorable_excursion_pct=None,
            avg_max_adverse_excursion_pct=None,
            avg_time_to_best_price=None,
            avg_time_to_worst_price=None,
            median_time_to_best_price=None,
            median_time_to_worst_price=None,
            avg_price_after_seconds={offset: None for offset in PRICE_AFTER_SECONDS},
            target_touch_counts={target: 0 for target in SCENARIO_TARGETS},
            target_eligible_counts={target: 0 for target in SCENARIO_TARGETS},
            target_hit_rates={target: None for target in SCENARIO_TARGETS},
            median_time_to_targets={target: None for target in SCENARIO_TARGETS},
            time_breakdown=_time_breakdown([]),
            low_sample_warning=True,
        )

    wins = sum(item.eventual_win for item in occurrences)
    ci_low, ci_high = _wilson_interval(wins, len(occurrences))
    avg_price_after_seconds = {
        offset: _safe_mean(
            [value for value in (item.price_after_seconds[offset] for item in occurrences) if value is not None]
        )
        for offset in PRICE_AFTER_SECONDS
    }
    target_touch_counts: dict[float, int] = {}
    target_eligible_counts: dict[float, int] = {}
    target_hit_rates: dict[float, float | None] = {}
    median_time_to_targets: dict[float, float | None] = {}

    for target in SCENARIO_TARGETS:
        # A target is eligible only when:
        #   1. valid subsequent path data exists, and
        #   2. the target is actually above the entry price.
        #
        # This prevents missing future paths from being counted as misses
        # and prevents already-cleared prices from being treated as targets.
        eligible = [
            item
            for item in occurrences
            if item.best_subsequent_price is not None
            and target > item.entry_price
        ]

        hit_times = [
            item.target_hit_seconds[target]
            for item in eligible
            if item.target_hit_seconds[target] is not None
        ]

        target_touch_counts[target] = len(hit_times)
        target_eligible_counts[target] = len(eligible)
        target_hit_rates[target] = (
            len(hit_times) / len(eligible)
            if eligible
            else None
        )
        median_time_to_targets[target] = _safe_median(
            [float(value) for value in hit_times]
        )

    return ScenarioSummary(
        definition=definition,
        occurrences=len(occurrences),
        path_observations=sum(
            item.best_subsequent_price is not None
            for item in occurrences
        ),
        unique_markets=len({item.market_ticker for item in occurrences}),
        win_rate=wins / len(occurrences),
        win_rate_ci_low=ci_low,
        win_rate_ci_high=ci_high,
        avg_entry_price=_safe_mean([item.entry_price for item in occurrences]),
        median_entry_price=_safe_median([item.entry_price for item in occurrences]),
        avg_best_subsequent_price=_safe_mean(
            [
                item.best_subsequent_price
                for item in occurrences
                if item.best_subsequent_price is not None
            ]
        ),
        median_best_subsequent_price=_safe_median(
            [
                item.best_subsequent_price
                for item in occurrences
                if item.best_subsequent_price is not None
            ]
        ),
        avg_worst_subsequent_price=_safe_mean(
            [
                item.worst_subsequent_price
                for item in occurrences
                if item.worst_subsequent_price is not None
            ]
        ),
        median_worst_subsequent_price=_safe_median(
            [
                item.worst_subsequent_price
                for item in occurrences
                if item.worst_subsequent_price is not None
            ]
        ),
        avg_max_favorable_excursion=_safe_mean(
            [
                item.max_favorable_excursion
                for item in occurrences
                if item.max_favorable_excursion is not None
            ]
        ),
        median_max_favorable_excursion=_safe_median(
            [
                item.max_favorable_excursion
                for item in occurrences
                if item.max_favorable_excursion is not None
            ]
        ),
        avg_max_adverse_excursion=_safe_mean(
            [
                item.max_adverse_excursion
                for item in occurrences
                if item.max_adverse_excursion is not None
            ]
        ),
        median_max_adverse_excursion=_safe_median(
            [
                item.max_adverse_excursion
                for item in occurrences
                if item.max_adverse_excursion is not None
            ]
        ),
        avg_max_favorable_excursion_pct=_safe_mean(
            [item.max_favorable_excursion_pct for item in occurrences if item.max_favorable_excursion_pct is not None]
        ),
        avg_max_adverse_excursion_pct=_safe_mean(
            [item.max_adverse_excursion_pct for item in occurrences if item.max_adverse_excursion_pct is not None]
        ),
        avg_time_to_best_price=_safe_mean([float(item.time_to_best_price) for item in occurrences if item.time_to_best_price is not None]),
        avg_time_to_worst_price=_safe_mean([float(item.time_to_worst_price) for item in occurrences if item.time_to_worst_price is not None]),
        median_time_to_best_price=_safe_median([float(item.time_to_best_price) for item in occurrences if item.time_to_best_price is not None]),
        median_time_to_worst_price=_safe_median([float(item.time_to_worst_price) for item in occurrences if item.time_to_worst_price is not None]),
        avg_price_after_seconds=avg_price_after_seconds,
        target_touch_counts=target_touch_counts,
        target_eligible_counts=target_eligible_counts,
        target_hit_rates=target_hit_rates,
        median_time_to_targets=median_time_to_targets,
        time_breakdown=_time_breakdown(occurrences),
        low_sample_warning=len(occurrences) < 100,
    )


def analyze_scenarios(
    connection: sqlite3.Connection,
    definitions: list[ScenarioDefinition],
    settled_markets: list[sqlite3.Row] | None = None,
    series_map: dict[str, list[Observation]] | None = None,
) -> tuple[list[ScenarioSummary], list[ScenarioOccurrence]]:
    settled_markets = settled_markets or _settled_markets_with_data(connection)
    series_map = series_map or _build_series_map(connection, settled_markets)
    occurrences: list[ScenarioOccurrence] = []
    scenario_market_sets: dict[str, set[str]] = defaultdict(set)

    for market in settled_markets:
        series = series_map.get(market["ticker"])
        if not series:
            continue
        for definition in definitions:
            for trigger_side in ("yes", "no"):
                for matched_index, trigger_price in _find_occurrence_indices(definition, series, trigger_side):
                    occurrence = _build_occurrence(
                        definition,
                        market["result"],
                        market["ticker"],
                        series,
                        trigger_side,
                        matched_index,
                        trigger_price,
                    )
                    occurrences.append(occurrence)
                    scenario_market_sets[definition.id].add(market["ticker"])

    by_scenario: dict[str, list[ScenarioOccurrence]] = defaultdict(list)
    for occurrence in occurrences:
        by_scenario[occurrence.scenario_id].append(occurrence)

    summaries = [_summarize(definition, by_scenario[definition.id]) for definition in definitions]
    for summary in summaries:
        overlaps: dict[str, int] = {}
        current_markets = scenario_market_sets.get(summary.definition.id, set())
        for other in summaries:
            if other.definition.id == summary.definition.id:
                continue
            overlap = len(current_markets & scenario_market_sets.get(other.definition.id, set()))
            if overlap:
                overlaps[other.definition.id] = overlap
        summary.overlap_market_counts.update(dict(sorted(overlaps.items(), key=lambda item: item[1], reverse=True)[:5]))

    return summaries, occurrences



def _relative_target_hit(
    current_price: float,
    future_best: float,
    delta: float,
    has_subsequent_path: bool,
) -> bool | None:
    """Return whether a relative target was hit, or None when ineligible."""
    if not has_subsequent_path:
        return None

    target = current_price + delta

    if target > 1.0:
        return None

    return future_best >= target



def build_probability_matrix(
    connection: sqlite3.Connection,
    settled_markets: list[sqlite3.Row] | None = None,
    series_map: dict[str, list[Observation]] | None = None,
) -> list[MatrixCell]:
    settled_markets = settled_markets or _settled_markets_with_data(connection)
    series_map = series_map or _build_series_map(connection, settled_markets)
    buckets: dict[tuple[str, str], list[dict[str, float | bool | str]]] = defaultdict(list)
    price_buckets_cents = [
        (int(low * 100), int(high * 100), label)
        for low, high, label in PRICE_BUCKETS
    ]

    for market in settled_markets:
        series = series_map.get(market["ticker"])
        if not series:
            continue

        # Do not let a market that trades heavily in one state contribute
        # thousands of correlated observations to the historical matrix.
        # Each side contributes at most once to a given price/time state.
        seen_states: set[tuple[str, str, str]] = set()

        yes_future_best = [0.0] * len(series)
        no_future_best = [0.0] * len(series)

        running_yes_best = 0.0
        running_no_best = 0.0

        for index in range(len(series) - 1, -1, -1):
            observation = series[index]

            # For candle observations, the current candle's high/low is not
            # known to occur after the close used as the state price. Store
            # the best price strictly after this candle. Exact trade/snapshot
            # observations can safely include their current observation.
            if observation.source == "candle":
                yes_future_best[index] = running_yes_best
                no_future_best[index] = running_no_best

                yes_high = _side_low_high(observation, "yes")[1]
                no_high = _side_low_high(observation, "no")[1]

                running_yes_best = max(running_yes_best, yes_high)
                running_no_best = max(running_no_best, no_high)
            else:
                yes_high = _side_low_high(observation, "yes")[1]
                no_high = _side_low_high(observation, "no")[1]

                running_yes_best = max(running_yes_best, yes_high)
                running_no_best = max(running_no_best, no_high)

                yes_future_best[index] = running_yes_best
                no_future_best[index] = running_no_best

        for index, observation in enumerate(series):
            for side in ("yes", "no"):
                current_price = _side_close(observation, side)

                price_label = _bucket_label(
                    int(round(current_price * 100)),
                    price_buckets_cents,
                )

                time_label = _bucket_label(
                    observation.seconds_remaining,
                    MATRIX_TIME_BUCKETS,
                )

                if price_label == "unknown" or time_label == "unknown":
                    continue

                state_key = (side, price_label, time_label)
                if state_key in seen_states:
                    continue
                seen_states.add(state_key)

                future_best = (
                    yes_future_best[index]
                    if side == "yes"
                    else no_future_best[index]
                )

                has_subsequent_path = not (
                    observation.source == "candle"
                    and index == len(series) - 1
                )

                buckets[(price_label, time_label)].append(
                    {
                        "won": _eventual_win(market["result"], side),
                        "touch_30": future_best >= 0.30 if has_subsequent_path else None,
                        "touch_35": future_best >= 0.35 if has_subsequent_path else None,
                        "touch_40": future_best >= 0.40 if has_subsequent_path else None,
                        "touch_50": future_best >= 0.50 if has_subsequent_path else None,
                        "plus_5": _relative_target_hit(
                            current_price, future_best, 0.05, has_subsequent_path
                        ),
                        "plus_10": _relative_target_hit(
                            current_price, future_best, 0.10, has_subsequent_path
                        ),
                        "plus_15": _relative_target_hit(
                            current_price, future_best, 0.15, has_subsequent_path
                        ),
                        "plus_20": _relative_target_hit(
                            current_price, future_best, 0.20, has_subsequent_path
                        ),
                        "best_price": future_best if has_subsequent_path else None,
                        "market_ticker": market["ticker"],
                    }
                )

    cells: list[MatrixCell] = []
    for _, _, price_label in PRICE_BUCKETS:
        for _, _, time_label in MATRIX_TIME_BUCKETS:
            items = buckets.get((price_label, time_label), [])
            if not items:
                cells.append(
                    MatrixCell(
                        price_bucket=price_label,
                        time_bucket=time_label,
                        observations=0,
                        path_observations=0,
                        unique_markets=0,
                        win_rate=None,
                        touch_30_rate=None,
                        touch_35_rate=None,
                        touch_40_rate=None,
                        touch_50_rate=None,
                        plus_5c_rate=None,
                        plus_5c_eligible_n=0,
                        plus_10c_rate=None,
                        plus_10c_eligible_n=0,
                        plus_15c_rate=None,
                        plus_15c_eligible_n=0,
                        plus_20c_rate=None,
                        plus_20c_eligible_n=0,
                        avg_best_subsequent_price=None,
                        median_best_subsequent_price=None,
                    )
                )
                continue
            best_prices = [
                float(item["best_price"])
                for item in items
                if item["best_price"] is not None
            ]

            def path_rate(key: str) -> float | None:
                values = [
                    bool(item[key])
                    for item in items
                    if item[key] is not None
                ]
                return sum(values) / len(values) if values else None

            cells.append(
                MatrixCell(
                    price_bucket=price_label,
                    time_bucket=time_label,
                    observations=len(items),
                    path_observations=sum(
                        item["best_price"] is not None
                        for item in items
                    ),
                    unique_markets=len({str(item["market_ticker"]) for item in items}),
                    win_rate=sum(bool(item["won"]) for item in items) / len(items),
                    touch_30_rate=path_rate("touch_30"),
                    touch_35_rate=path_rate("touch_35"),
                    touch_40_rate=path_rate("touch_40"),
                    touch_50_rate=path_rate("touch_50"),
                    plus_5c_rate=path_rate("plus_5"),
                    plus_5c_eligible_n=sum(
                        item["plus_5"] is not None for item in items
                    ),
                    plus_10c_rate=path_rate("plus_10"),
                    plus_10c_eligible_n=sum(
                        item["plus_10"] is not None for item in items
                    ),
                    plus_15c_rate=path_rate("plus_15"),
                    plus_15c_eligible_n=sum(
                        item["plus_15"] is not None for item in items
                    ),
                    plus_20c_rate=path_rate("plus_20"),
                    plus_20c_eligible_n=sum(
                        item["plus_20"] is not None for item in items
                    ),
                    avg_best_subsequent_price=mean(best_prices) if best_prices else None,
                    median_best_subsequent_price=median(best_prices) if best_prices else None,
                )
            )
    return cells


def build_live_scenario_board(
    connection: sqlite3.Connection, definitions: list[ScenarioDefinition], summaries: list[ScenarioSummary]
) -> list[LiveScenarioMatch]:
    latest_rows = connection.execute(
        """
        SELECT m.ticker, m.status, m.open_time, m.close_time, s.yes_bid, s.yes_ask, s.collected_at
        FROM markets m
        JOIN (
            SELECT market_ticker, MAX(collected_at) AS max_collected_at
            FROM quote_snapshots
            GROUP BY market_ticker
        ) latest ON latest.market_ticker = m.ticker
        JOIN quote_snapshots s
          ON s.market_ticker = latest.market_ticker
         AND s.collected_at = latest.max_collected_at
        WHERE m.status NOT IN ('finalized', 'settled')
        ORDER BY m.close_time
        """
    ).fetchall()
    summary_map = {summary.definition.id: summary for summary in summaries}
    matches: list[LiveScenarioMatch] = []
    for row in latest_rows:
        observed_ts = _iso_to_ts(row["collected_at"])
        open_ts = _iso_to_ts(row["open_time"])
        close_ts = _iso_to_ts(row["close_time"])

        # Kalshi may return scheduled future markets with an active/open-like
        # status. A market belongs on the live board only while its actual
        # 15-minute trading window is in progress.
        if not (open_ts <= observed_ts < close_ts):
            continue

        seconds_remaining = close_ts - observed_ts
        elapsed_seconds = 900 - seconds_remaining
        yes_price = _mid(float(row["yes_bid"]), float(row["yes_ask"]))
        side_prices = {"yes": yes_price, "no": 1.0 - yes_price}
        for side, price in side_prices.items():
            for definition in definitions:
                if not (
                    definition.trigger_price_min <= price <= definition.trigger_price_max
                    and definition.elapsed_seconds_min <= elapsed_seconds <= definition.elapsed_seconds_max
                    and definition.seconds_remaining_min <= seconds_remaining <= definition.seconds_remaining_max
                ):
                    continue
                summary = summary_map[definition.id]
                matches.append(
                    LiveScenarioMatch(
                        market_ticker=row["ticker"],
                        market_status=row["status"],
                        side=side,
                        current_price=price,
                        seconds_remaining=seconds_remaining,
                        scenario_id=definition.id,
                        scenario_name=definition.name,
                        historical_occurrences=summary.occurrences,
                        historical_win_rate=summary.win_rate,
                        target_hit_rates=summary.target_hit_rates,
                        avg_target_profit={},
                    )
                )
    return matches


def build_active_market_side_views(
    connection: sqlite3.Connection, definitions: list[ScenarioDefinition], matrix: list[MatrixCell]
) -> list[ActiveMarketSideView]:
    latest_rows = connection.execute(
        """
        SELECT m.ticker, m.status, m.open_time, m.close_time, s.yes_bid, s.yes_ask, s.collected_at
        FROM markets m
        JOIN (
            SELECT market_ticker, MAX(collected_at) AS max_collected_at
            FROM quote_snapshots
            GROUP BY market_ticker
        ) latest ON latest.market_ticker = m.ticker
        JOIN quote_snapshots s
          ON s.market_ticker = latest.market_ticker
         AND s.collected_at = latest.max_collected_at
        WHERE m.status NOT IN ('finalized', 'settled')
        ORDER BY m.close_time
        """
    ).fetchall()
    matrix_map = {(cell.price_bucket, cell.time_bucket): cell for cell in matrix}
    views: list[ActiveMarketSideView] = []
    for row in latest_rows:
        observed_ts = _iso_to_ts(row["collected_at"])
        open_ts = _iso_to_ts(row["open_time"])
        close_ts = _iso_to_ts(row["close_time"])

        # Kalshi may return scheduled future markets with an active/open-like
        # status. A market belongs on the live board only while its actual
        # 15-minute trading window is in progress.
        if not (open_ts <= observed_ts < close_ts):
            continue

        seconds_remaining = close_ts - observed_ts
        elapsed_seconds = 900 - seconds_remaining
        yes_price = _mid(float(row["yes_bid"]), float(row["yes_ask"]))
        for side, current_price in (("yes", yes_price), ("no", 1.0 - yes_price)):
            price_bucket = next((label for low, high, label in PRICE_BUCKETS if low <= current_price <= high), "unknown")
            time_bucket = _bucket_label(seconds_remaining, MATRIX_TIME_BUCKETS)
            cell = matrix_map.get((price_bucket, time_bucket))
            matched = [
                definition.name
                for definition in definitions
                if definition.trigger_price_min <= current_price <= definition.trigger_price_max
                and definition.elapsed_seconds_min <= elapsed_seconds <= definition.elapsed_seconds_max
                and definition.seconds_remaining_min <= seconds_remaining <= definition.seconds_remaining_max
            ]
            views.append(
                ActiveMarketSideView(
                    market_ticker=row["ticker"],
                    market_status=row["status"],
                    side=side,
                    current_price=current_price,
                    seconds_remaining=seconds_remaining,
                    price_bucket=price_bucket,
                    time_bucket=time_bucket,
                    observations=0 if cell is None else cell.observations,
                    win_rate=None if cell is None else cell.win_rate,
                    plus_5c_rate=None if cell is None else cell.plus_5c_rate,
                    plus_10c_rate=None if cell is None else cell.plus_10c_rate,
                    plus_15c_rate=None if cell is None else cell.plus_15c_rate,
                    plus_20c_rate=None if cell is None else cell.plus_20c_rate,
                    touch_30_rate=None if cell is None else cell.touch_30_rate,
                    touch_35_rate=None if cell is None else cell.touch_35_rate,
                    touch_40_rate=None if cell is None else cell.touch_40_rate,
                    touch_50_rate=None if cell is None else cell.touch_50_rate,
                    avg_best_subsequent_price=None if cell is None else cell.avg_best_subsequent_price,
                    median_best_subsequent_price=None if cell is None else cell.median_best_subsequent_price,
                    matched_scenarios=matched,
                )
            )
    return views


def database_overview(connection: sqlite3.Connection) -> dict[str, int | str]:
    row = connection.execute(
        """
        SELECT
            COUNT(*) AS market_count,
            SUM(CASE WHEN result IN ('yes', 'no') THEN 1 ELSE 0 END) AS settled_market_count
        FROM markets
        """
    ).fetchone()
    candle_count = connection.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
    trade_count = connection.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    snapshot_count = connection.execute("SELECT COUNT(*) FROM quote_snapshots").fetchone()[0]
    btc_rows = connection.execute("SELECT COUNT(*) FROM btc_1s").fetchone()[0]
    last_snapshot = connection.execute("SELECT MAX(collected_at) FROM quote_snapshots").fetchone()[0]
    settled_with_trades = connection.execute(
        """
        SELECT COUNT(DISTINCT m.ticker)
        FROM markets m
        JOIN trades t ON t.market_ticker = m.ticker
        WHERE m.result IN ('yes', 'no')
        """
    ).fetchone()[0]
    settled_with_candles = connection.execute(
        """
        SELECT COUNT(DISTINCT m.ticker)
        FROM markets m
        JOIN candles c ON c.market_ticker = m.ticker
        WHERE m.result IN ('yes', 'no')
        """
    ).fetchone()[0]
    btc_covered_markets = connection.execute(
        """
        SELECT COUNT(DISTINCT m.ticker)
        FROM markets m
        WHERE m.result IN ('yes', 'no')
          AND EXISTS (
              SELECT 1
              FROM btc_1s b
              WHERE b.ts BETWEEN CAST(strftime('%s', m.open_time) AS INTEGER) * 1000
                             AND CAST(strftime('%s', m.close_time) AS INTEGER) * 1000
          )
        """
    ).fetchone()[0]
    return {
        "market_count": int(row["market_count"] or 0),
        "settled_market_count": int(row["settled_market_count"] or 0),
        "settled_with_trade_history": int(settled_with_trades or 0),
        "settled_with_candle_history": int(settled_with_candles or 0),
        "candle_count": int(candle_count or 0),
        "trade_count": int(trade_count or 0),
        "snapshot_count": int(snapshot_count or 0),
        "btc_row_count": int(btc_rows or 0),
        "btc_covered_markets": int(btc_covered_markets or 0),
        "last_snapshot": last_snapshot or "",
    }



def build_validated_setups(
    connection: sqlite3.Connection,
    *,
    settled_markets: list[sqlite3.Row] | None = None,
    series_map: dict[str, list[Observation]] | None = None,
    discovery_fraction: float = 0.80,
    min_discovery_path_n: int = 500,
    min_holdout_path_n: int = 100,
    persistence_threshold: float = 0.02,
    limit: int = 12,
):
    """Discover price/time states on early data and validate on later data.

    Candidate selection uses discovery data only. Each state is compared with
    the same price bucket at all OTHER time buckets, so the candidate does not
    contribute to its own baseline.

    Holdout data never influences candidate selection.
    """
    from .models import ValidatedSetup

    markets = (
        settled_markets
        if settled_markets is not None
        else _settled_markets_with_data(connection)
    )
    discovery_markets, holdout_markets = chronological_market_split(
        markets,
        discovery_fraction=discovery_fraction,
    )

    # Build all observation series once and reuse them in the two
    # chronologically independent matrices.
    full_series_map = (
        series_map
        if series_map is not None
        else _build_series_map(connection, markets)
    )

    discovery_series = {
        market["ticker"]: full_series_map[market["ticker"]]
        for market in discovery_markets
        if market["ticker"] in full_series_map
    }
    holdout_series = {
        market["ticker"]: full_series_map[market["ticker"]]
        for market in holdout_markets
        if market["ticker"] in full_series_map
    }

    discovery_matrix = build_probability_matrix(
        connection,
        settled_markets=discovery_markets,
        series_map=discovery_series,
    )
    holdout_matrix = build_probability_matrix(
        connection,
        settled_markets=holdout_markets,
        series_map=holdout_series,
    )

    def leave_one_time_bucket_out_baseline(
        matrix: list[MatrixCell],
        target_cell: MatrixCell,
    ) -> float | None:
        """Weighted +10c baseline for the same price at other time buckets."""
        comparison_cells = [
            cell
            for cell in matrix
            if (
                cell.price_bucket == target_cell.price_bucket
                and cell.time_bucket != target_cell.time_bucket
                and cell.plus_10c_rate is not None
                and cell.plus_10c_eligible_n > 0
            )
        ]

        total_n = sum(
            cell.plus_10c_eligible_n
            for cell in comparison_cells
        )

        if total_n == 0:
            return None

        weighted_hits = sum(
            cell.plus_10c_rate * cell.plus_10c_eligible_n
            for cell in comparison_cells
        )

        return weighted_hits / total_n

    # Candidate selection occurs ONLY on discovery data.
    candidates = []

    for cell in discovery_matrix:
        if (
            cell.plus_10c_eligible_n < min_discovery_path_n
            or cell.plus_10c_rate is None
        ):
            continue

        # A relative +10c target must actually be possible for every entry
        # represented by this bucket. Exclude 90c+ buckets from +10c screening.
        try:
            bucket_high = int(
                cell.price_bucket
                .replace("c", "")
                .split("-")[1]
            )
        except (IndexError, ValueError):
            continue

        if bucket_high + 10 > 100:
            continue

        baseline = leave_one_time_bucket_out_baseline(
            discovery_matrix,
            cell,
        )

        if baseline is None:
            continue

        uplift = cell.plus_10c_rate - baseline

        candidates.append(
            (uplift, cell.plus_10c_eligible_n, cell, baseline)
        )

    candidates.sort(
        key=lambda item: (item[0], item[1]),
        reverse=True,
    )
    candidates = candidates[:limit]

    holdout_map = {
        (cell.price_bucket, cell.time_bucket): cell
        for cell in holdout_matrix
    }

    results = []

    for (
        discovery_uplift,
        _,
        discovery_cell,
        discovery_baseline,
    ) in candidates:
        key = (
            discovery_cell.price_bucket,
            discovery_cell.time_bucket,
        )

        holdout_cell = holdout_map.get(key)

        if holdout_cell is None:
            holdout_path_n = 0
            holdout_rate = None
            holdout_baseline = None
            holdout_uplift = None
            validation_status = "INSUFFICIENT"
        else:
            holdout_path_n = holdout_cell.plus_10c_eligible_n
            holdout_rate = holdout_cell.plus_10c_rate

            holdout_baseline = (
                leave_one_time_bucket_out_baseline(
                    holdout_matrix,
                    holdout_cell,
                )
            )

            if (
                holdout_rate is None
                or holdout_baseline is None
            ):
                holdout_uplift = None
                validation_status = "INSUFFICIENT"
            else:
                holdout_uplift = (
                    holdout_rate - holdout_baseline
                )

                if holdout_path_n < min_holdout_path_n:
                    validation_status = "INSUFFICIENT"
                elif holdout_uplift >= persistence_threshold:
                    validation_status = "PERSISTED"
                elif holdout_uplift > 0.0:
                    validation_status = "WEAK"
                else:
                    validation_status = "FAILED"

        results.append(
            ValidatedSetup(
                price_bucket=discovery_cell.price_bucket,
                time_bucket=discovery_cell.time_bucket,
                discovery_path_n=discovery_cell.plus_10c_eligible_n,
                discovery_plus_10c_rate=discovery_cell.plus_10c_rate,
                discovery_baseline_rate=discovery_baseline,
                discovery_uplift=discovery_uplift,
                holdout_path_n=holdout_path_n,
                holdout_plus_10c_rate=holdout_rate,
                holdout_baseline_rate=holdout_baseline,
                holdout_uplift=holdout_uplift,
                validation_status=validation_status,
            )
        )

    return results


def build_strategy_entries(
    connection: sqlite3.Connection,
    settled_markets: list[sqlite3.Row] | None = None,
    series_map: dict[str, list[Observation]] | None = None,
) -> tuple[list[StrategyEntry], dict[str, list[Observation]]]:
    """Build deduplicated historical entries using matrix semantics.

    Each side of each market contributes at most once to a given
    price/time state. This intentionally matches build_probability_matrix.

    The returned series_map is reused by the strategy simulator so it does
    not need to reload historical observations from SQLite.
    """

    from .models import StrategyEntry

    settled_markets = settled_markets or _settled_markets_with_data(connection)
    series_map = series_map or _build_series_map(connection, settled_markets)

    price_buckets_cents = [
        (int(low * 100), int(high * 100), label)
        for low, high, label in PRICE_BUCKETS
    ]

    entries: list[StrategyEntry] = []

    for market in settled_markets:
        series = series_map.get(market["ticker"])
        if not series:
            continue

        seen_states: set[tuple[str, str, str]] = set()

        for index, observation in enumerate(series):
            for side in ("yes", "no"):
                current_price = _side_close(observation, side)

                price_label = _bucket_label(
                    int(round(current_price * 100)),
                    price_buckets_cents,
                )

                time_label = _bucket_label(
                    observation.seconds_remaining,
                    MATRIX_TIME_BUCKETS,
                )

                if price_label == "unknown" or time_label == "unknown":
                    continue

                state_key = (side, price_label, time_label)

                if state_key in seen_states:
                    continue

                seen_states.add(state_key)

                # A final candle has no observable post-entry path because
                # using that candle's own high/low would introduce intrabar
                # look-ahead.
                if (
                    observation.source == "candle"
                    and index == len(series) - 1
                ):
                    continue

                entries.append(
                    StrategyEntry(
                        market_ticker=market["ticker"],
                        side=side,
                        entry_index=index,
                        entry_ts=observation.observed_ts,
                        entry_price=current_price,
                        price_bucket=price_label,
                        time_bucket=time_label,
                        seconds_remaining=observation.seconds_remaining,
                        eventual_win=_eventual_win(
                            market["result"],
                            side,
                        ),
                    )
                )

    return entries, series_map

def classify_validated_strategy(
    holdout_summary,
    *,
    min_holdout_n: int = 100,
) -> str:
    """Classify an already discovery-qualified strategy on unseen holdout data."""

    if holdout_summary.observations < min_holdout_n:
        return "INSUFFICIENT"

    if (
        holdout_summary.profit_ci_low is not None
        and holdout_summary.profit_ci_low > 0
    ):
        return "STRONG"

    if (
        holdout_summary.avg_profit is not None
        and holdout_summary.avg_profit > 0
    ):
        return "PROMISING"

    return "FAILED"


def build_validated_strategies(
    connection: sqlite3.Connection,
    *,
    discovery_fraction: float = 0.80,
    min_discovery_n: int = 500,
    min_holdout_n: int = 100,
    ambiguity_mode: str = "conservative",
    strategies=None,
    settled_markets: list[sqlite3.Row] | None = None,
    series_map: dict[str, list[Observation]] | None = None,
    strategy_entries=None,
    include_insufficient: bool = False,
    limit: int | None = None,
):
    """Discover historical strategies on early data and validate on later data.

    Candidate selection is discovery-only.

    A candidate must have:
        discovery observations >= min_discovery_n
        discovery profit_ci_low > 0

    Holdout classification:
        STRONG:
            holdout 95% CI lower bound > 0

        PROMISING:
            holdout average profit > 0, but CI includes zero

        FAILED:
            holdout average profit <= 0

        INSUFFICIENT:
            holdout observations < min_holdout_n

    By default insufficient results are omitted, matching the terminal CI
    scanner used to produce the current validated shortlist.

    Historical observations and strategy entries are built only once and
    reused across every state/strategy combination.
    """
    from .models import ValidatedStrategyResult
    from .strategies import (
        DEFAULT_EXIT_STRATEGIES,
        simulate_strategy_entries,
        summarize_strategy,
    )

    if ambiguity_mode not in {"conservative", "optimistic", "exclude"}:
        raise ValueError(
            "ambiguity_mode must be conservative, optimistic, or exclude"
        )

    strategies = (
        list(strategies)
        if strategies is not None
        else list(DEFAULT_EXIT_STRATEGIES)
    )

    markets = (
        settled_markets
        if settled_markets is not None
        else _settled_markets_with_data(connection)
    )

    discovery_markets, holdout_markets = chronological_market_split(
        markets,
        discovery_fraction=discovery_fraction,
    )

    full_series_map = (
        series_map
        if series_map is not None
        else _build_series_map(connection, markets)
    )

    # Build historical entries exactly once unless the
    # caller already constructed them for another validation pass.
    if strategy_entries is None:
        all_entries, full_series_map = build_strategy_entries(
            connection,
            settled_markets=markets,
            series_map=full_series_map,
        )
    else:
        all_entries = list(strategy_entries)

    discovery_tickers = {
        market["ticker"]
        for market in discovery_markets
    }

    holdout_tickers = {
        market["ticker"]
        for market in holdout_markets
    }

    # Group entries once so each simulation does not scan the full
    # historical entry collection.
    discovery_by_state = {}
    holdout_by_state = {}

    for entry in all_entries:
        state = (entry.price_bucket, entry.time_bucket)

        if entry.market_ticker in discovery_tickers:
            discovery_by_state.setdefault(state, []).append(entry)

        elif entry.market_ticker in holdout_tickers:
            holdout_by_state.setdefault(state, []).append(entry)

    price_order = {
        label: index
        for index, (_, _, label) in enumerate(PRICE_BUCKETS)
    }

    time_order = {
        label: index
        for index, (_, _, label) in enumerate(MATRIX_TIME_BUCKETS)
    }

    states = sorted(
        discovery_by_state,
        key=lambda state: (
            price_order.get(state[0], 999),
            time_order.get(state[1], 999),
        ),
    )

    results = []

    for price_bucket, time_bucket in states:
        discovery_entries = discovery_by_state.get(
            (price_bucket, time_bucket),
            [],
        )

        holdout_entries = holdout_by_state.get(
            (price_bucket, time_bucket),
            [],
        )

        for strategy in strategies:
            discovery_outcomes = simulate_strategy_entries(
                strategy=strategy,
                entries=discovery_entries,
                series_map=full_series_map,
                ambiguity_mode=ambiguity_mode,
            )

            discovery_summary = summarize_strategy(
                strategy,
                discovery_outcomes,
            )

            # IMPORTANT:
            # Candidate selection happens ONLY on discovery.
            if discovery_summary.observations < min_discovery_n:
                continue

            if (
                discovery_summary.profit_ci_low is None
                or discovery_summary.profit_ci_low <= 0
            ):
                continue

            holdout_outcomes = simulate_strategy_entries(
                strategy=strategy,
                entries=holdout_entries,
                series_map=full_series_map,
                ambiguity_mode=ambiguity_mode,
            )

            holdout_summary = summarize_strategy(
                strategy,
                holdout_outcomes,
            )

            validation_status = classify_validated_strategy(
                holdout_summary,
                min_holdout_n=min_holdout_n,
            )

            if (
                validation_status == "INSUFFICIENT"
                and not include_insufficient
            ):
                continue

            results.append(
                ValidatedStrategyResult(
                    price_bucket=price_bucket,
                    time_bucket=time_bucket,
                    strategy=strategy,
                    discovery_summary=discovery_summary,
                    holdout_summary=holdout_summary,
                    validation_status=validation_status,
                    ambiguity_mode=ambiguity_mode,
                )
            )

    # Rank ONLY using discovery data.
    # Holdout must never determine which strategies are selected/ranked.
    results.sort(
        key=lambda result: (
            result.discovery_summary.profit_ci_low
            if result.discovery_summary.profit_ci_low is not None
            else float("-inf")
        ),
        reverse=True,
    )

    if limit is not None:
        return results[:limit]

    return results



def expanding_walk_forward_splits(
    markets,
    *,
    fold_count: int = 5,
    initial_train_fraction: float = 0.50,
):
    """
    Expanding-window chronological splits with non-overlapping
    future test windows.

    With five folds and a 50% initial training period:

        F1 train 0-50%   test 50-60%
        F2 train 0-60%   test 60-70%
        F3 train 0-70%   test 70-80%
        F4 train 0-80%   test 80-90%
        F5 train 0-90%   test 90-100%

    Every test market is therefore unseen when its fold's
    strategy qualification occurs.
    """

    if fold_count < 2:
        raise ValueError(
            "fold_count must be at least 2"
        )

    if not (
        0 < initial_train_fraction < 1
    ):
        raise ValueError(
            "initial_train_fraction must be between 0 and 1"
        )

    ordered = sorted(
        markets,
        key=lambda market: (
            str(
                market["close_time"]
                or ""
            ),
            str(
                market["ticker"]
            ),
        ),
    )

    market_count = len(ordered)

    if market_count < 3:
        return []

    initial_train_count = max(
        1,
        int(
            market_count
            * initial_train_fraction
        ),
    )

    remaining = (
        market_count
        - initial_train_count
    )

    if remaining <= 0:
        return []

    actual_fold_count = min(
        fold_count,
        remaining,
    )

    base_test_size = (
        remaining
        // actual_fold_count
    )

    remainder = (
        remaining
        % actual_fold_count
    )

    cursor = initial_train_count
    splits = []

    for index in range(
        actual_fold_count
    ):
        test_size = (
            base_test_size
            + (
                1
                if index < remainder
                else 0
            )
        )

        test_end = (
            cursor + test_size
        )

        train = ordered[:cursor]
        test = ordered[
            cursor:test_end
        ]

        if not test:
            break

        splits.append(
            (
                train,
                test,
            )
        )

        cursor = test_end

    return splits


def classify_walk_forward_persistence(
    *,
    total_folds: int,
    qualified_folds: int,
    evaluated_folds: int,
    positive_folds: int,
    aggregate_summary,
    min_evaluated_folds: int = 3,
) -> str:
    """
    Persistence classification independent of the existing
    80/20 STRONG/PROMISING/FAILED classification.

    ROBUST:
        >=3 adequately-sized unseen folds,
        >=80% positive unseen folds,
        aggregate unseen 95% CI entirely above zero.

    MIXED:
        >=3 adequately-sized unseen folds,
        >=60% positive unseen folds,
        aggregate unseen average above zero.

    UNSTABLE:
        enough folds exist but the above conditions fail.
    """

    if (
        evaluated_folds
        < min_evaluated_folds
    ):
        return "INSUFFICIENT"

    if (
        aggregate_summary.observations
        <= 0
        or aggregate_summary.avg_profit
        is None
    ):
        return "INSUFFICIENT"

    positive_rate = (
        positive_folds
        / evaluated_folds
    )

    qualification_rate = (
        qualified_folds
        / total_folds
        if total_folds
        else 0.0
    )

    if (
        aggregate_summary.profit_ci_low
        is not None
        and aggregate_summary.profit_ci_low
        > 0
        and positive_rate >= 0.80
        and qualification_rate >= 0.80
    ):
        return "ROBUST"

    if (
        aggregate_summary.avg_profit
        > 0
        and positive_rate >= 0.60
    ):
        return "MIXED"

    return "UNSTABLE"


def build_walk_forward_strategies(
    connection: sqlite3.Connection,
    *,
    fold_count: int = 5,
    initial_train_fraction: float = 0.50,
    min_train_n: int = 500,
    min_test_n: int = 50,
    ambiguity_mode: str = "conservative",
    strategies=None,
    settled_markets: list[sqlite3.Row] | None = None,
    series_map: dict[str, list[Observation]] | None = None,
    strategy_entries=None,
):
    """
    Re-discover strategies using only information available before
    each test period, then evaluate them in the immediately following
    unseen market window.

    Test windows never overlap. A strategy contributes out-of-sample
    results for a fold only if it independently qualified using that
    fold's training data.
    """

    from .models import (
        WalkForwardFoldResult,
        WalkForwardStrategyResult,
    )

    from .strategies import (
        DEFAULT_EXIT_STRATEGIES,
        simulate_strategy_entries,
        summarize_strategy,
    )

    if ambiguity_mode not in {
        "conservative",
        "optimistic",
        "exclude",
    }:
        raise ValueError(
            "ambiguity_mode must be conservative, "
            "optimistic, or exclude"
        )

    strategies = (
        list(strategies)
        if strategies is not None
        else list(
            DEFAULT_EXIT_STRATEGIES
        )
    )

    markets = (
        settled_markets
        if settled_markets is not None
        else _settled_markets_with_data(
            connection
        )
    )

    full_series_map = (
        series_map
        if series_map is not None
        else _build_series_map(
            connection,
            markets,
        )
    )

    if strategy_entries is None:
        all_entries, full_series_map = (
            build_strategy_entries(
                connection,
                settled_markets=markets,
                series_map=full_series_map,
            )
        )
    else:
        all_entries = list(
            strategy_entries
        )

    splits = (
        expanding_walk_forward_splits(
            markets,
            fold_count=fold_count,
            initial_train_fraction=(
                initial_train_fraction
            ),
        )
    )

    fold_records = {}
    aggregate_outcomes = {}
    strategy_lookup = {}

    total_folds = len(splits)

    for fold_offset, (
        train_markets,
        test_markets,
    ) in enumerate(
        splits,
        start=1,
    ):
        train_tickers = {
            market["ticker"]
            for market in train_markets
        }

        test_tickers = {
            market["ticker"]
            for market in test_markets
        }

        train_by_state = {}
        test_by_state = {}

        for entry in all_entries:
            state = (
                entry.price_bucket,
                entry.time_bucket,
            )

            if (
                entry.market_ticker
                in train_tickers
            ):
                train_by_state.setdefault(
                    state,
                    [],
                ).append(entry)

            elif (
                entry.market_ticker
                in test_tickers
            ):
                test_by_state.setdefault(
                    state,
                    [],
                ).append(entry)

        train_end = str(
            train_markets[-1][
                "close_time"
            ]
            or ""
        )

        test_start = str(
            test_markets[0][
                "close_time"
            ]
            or ""
        )

        test_end = str(
            test_markets[-1][
                "close_time"
            ]
            or ""
        )

        for (
            price_bucket,
            time_bucket,
        ), train_entries in (
            train_by_state.items()
        ):
            test_entries = (
                test_by_state.get(
                    (
                        price_bucket,
                        time_bucket,
                    ),
                    [],
                )
            )

            for strategy in strategies:
                train_outcomes = (
                    simulate_strategy_entries(
                        strategy=strategy,
                        entries=train_entries,
                        series_map=(
                            full_series_map
                        ),
                        ambiguity_mode=(
                            ambiguity_mode
                        ),
                    )
                )

                train_summary = (
                    summarize_strategy(
                        strategy,
                        train_outcomes,
                    )
                )

                # Fold candidate selection is entirely historical:
                # the future test window cannot influence this.
                if (
                    train_summary.observations
                    < min_train_n
                ):
                    continue

                if (
                    train_summary.profit_ci_low
                    is None
                    or
                    train_summary.profit_ci_low
                    <= 0
                ):
                    continue

                test_outcomes = (
                    simulate_strategy_entries(
                        strategy=strategy,
                        entries=test_entries,
                        series_map=(
                            full_series_map
                        ),
                        ambiguity_mode=(
                            ambiguity_mode
                        ),
                    )
                )

                test_summary = (
                    summarize_strategy(
                        strategy,
                        test_outcomes,
                    )
                )

                test_status = (
                    classify_validated_strategy(
                        test_summary,
                        min_holdout_n=(
                            min_test_n
                        ),
                    )
                )

                strategy_id = str(
                    strategy.id
                )

                key = (
                    price_bucket,
                    time_bucket,
                    strategy_id,
                )

                strategy_lookup[
                    key
                ] = strategy

                fold_records.setdefault(
                    key,
                    [],
                ).append(
                    WalkForwardFoldResult(
                        fold_index=(
                            fold_offset
                        ),
                        train_market_count=(
                            len(
                                train_markets
                            )
                        ),
                        test_market_count=(
                            len(
                                test_markets
                            )
                        ),
                        train_end=(
                            train_end
                        ),
                        test_start=(
                            test_start
                        ),
                        test_end=(
                            test_end
                        ),
                        train_summary=(
                            train_summary
                        ),
                        test_summary=(
                            test_summary
                        ),
                        test_status=(
                            test_status
                        ),
                    )
                )

                aggregate_outcomes.setdefault(
                    key,
                    [],
                ).extend(
                    test_outcomes
                )

    results = []

    for key, folds in (
        fold_records.items()
    ):
        (
            price_bucket,
            time_bucket,
            _strategy_id,
        ) = key

        strategy = (
            strategy_lookup[key]
        )

        aggregate_summary = (
            summarize_strategy(
                strategy,
                aggregate_outcomes.get(
                    key,
                    [],
                ),
            )
        )

        evaluated = [
            fold
            for fold in folds
            if (
                fold.test_summary
                .observations
                >= min_test_n
            )
        ]

        positive_folds = sum(
            1
            for fold in evaluated
            if (
                fold.test_summary.avg_profit
                is not None
                and
                fold.test_summary.avg_profit
                > 0
            )
        )

        strong_folds = sum(
            1
            for fold in evaluated
            if (
                fold.test_status
                == "STRONG"
            )
        )

        fold_averages = [
            fold.test_summary.avg_profit
            for fold in evaluated
            if (
                fold.test_summary.avg_profit
                is not None
            )
        ]

        worst_fold_avg_profit = (
            min(fold_averages)
            if fold_averages
            else None
        )

        persistence_status = (
            classify_walk_forward_persistence(
                total_folds=(
                    total_folds
                ),
                qualified_folds=(
                    len(folds)
                ),
                evaluated_folds=(
                    len(evaluated)
                ),
                positive_folds=(
                    positive_folds
                ),
                aggregate_summary=(
                    aggregate_summary
                ),
            )
        )

        results.append(
            WalkForwardStrategyResult(
                price_bucket=(
                    price_bucket
                ),
                time_bucket=(
                    time_bucket
                ),
                strategy=strategy,
                folds=sorted(
                    folds,
                    key=lambda fold: (
                        fold.fold_index
                    ),
                ),
                total_folds=(
                    total_folds
                ),
                qualified_folds=(
                    len(folds)
                ),
                evaluated_folds=(
                    len(evaluated)
                ),
                positive_folds=(
                    positive_folds
                ),
                strong_folds=(
                    strong_folds
                ),
                aggregate_oos_summary=(
                    aggregate_summary
                ),
                worst_fold_avg_profit=(
                    worst_fold_avg_profit
                ),
                persistence_status=(
                    persistence_status
                ),
                ambiguity_mode=(
                    ambiguity_mode
                ),
            )
        )

    # This ranking is for analysis/display only.
    # It does NOT alter discovery candidate selection.
    status_order = {
        "ROBUST": 3,
        "MIXED": 2,
        "UNSTABLE": 1,
        "INSUFFICIENT": 0,
    }

    results.sort(
        key=lambda result: (
            status_order.get(
                result.persistence_status,
                -1,
            ),
            (
                result.aggregate_oos_summary
                .profit_ci_low
                if (
                    result.aggregate_oos_summary
                    .profit_ci_low
                    is not None
                )
                else float("-inf")
            ),
        ),
        reverse=True,
    )

    return results
