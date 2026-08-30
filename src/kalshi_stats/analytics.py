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
        return [
            Observation(
                observed_ts=int(row["end_period_ts"]),
                seconds_remaining=max(0, close_ts - int(row["end_period_ts"])),
                elapsed_seconds=900 - max(0, close_ts - int(row["end_period_ts"])),
                yes_close=float(row["price_close"]),
                yes_low=float(row["price_low"]),
                yes_high=float(row["price_high"]),
                source="candle",
            )
            for row in candle_rows
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


def _price_at_or_after(future: list[Observation], side: str, offset_seconds: int) -> float | None:
    if not future:
        return None
    entry_ts = future[0].observed_ts
    entry_remaining = future[0].seconds_remaining
    if entry_remaining < offset_seconds:
        return None
    for observation in future:
        if observation.observed_ts - entry_ts >= offset_seconds:
            return _side_close(observation, side)
    return _side_close(future[-1], side) if future[-1].observed_ts - entry_ts >= offset_seconds else None


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
    future = series[matched_index:]
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
    price_after_seconds = {
        offset: _price_at_or_after(future, traded_side, offset) for offset in PRICE_AFTER_SECONDS
    }
    target_hit_seconds: dict[float, int | None] = {}
    target_profit: dict[float, float] = {}
    eventual_win = _eventual_win(market_result, traded_side)
    settlement_price = 1.0 if eventual_win else 0.0
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
        max_favorable_excursion_pct=None if entry_price == 0 else max_favorable_excursion / entry_price,
        max_adverse_excursion_pct=None if entry_price == 0 else max_adverse_excursion / entry_price,
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
            "median_max_price": _safe_median([item.best_subsequent_price for item in items]),
        }
    return result


def _summarize(definition: ScenarioDefinition, occurrences: list[ScenarioOccurrence]) -> ScenarioSummary:
    if not occurrences:
        return ScenarioSummary(
            definition=definition,
            occurrences=0,
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
            target_hit_rates={target: 0.0 for target in SCENARIO_TARGETS},
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
    target_hit_rates: dict[float, float] = {}
    median_time_to_targets: dict[float, float | None] = {}
    for target in SCENARIO_TARGETS:
        hit_times = [
            item.target_hit_seconds[target]
            for item in occurrences
            if item.target_hit_seconds[target] is not None
        ]
        target_touch_counts[target] = len(hit_times)
        target_hit_rates[target] = len(hit_times) / len(occurrences)
        median_time_to_targets[target] = _safe_median([float(value) for value in hit_times])

    return ScenarioSummary(
        definition=definition,
        occurrences=len(occurrences),
        unique_markets=len({item.market_ticker for item in occurrences}),
        win_rate=wins / len(occurrences),
        win_rate_ci_low=ci_low,
        win_rate_ci_high=ci_high,
        avg_entry_price=_safe_mean([item.entry_price for item in occurrences]),
        median_entry_price=_safe_median([item.entry_price for item in occurrences]),
        avg_best_subsequent_price=_safe_mean([item.best_subsequent_price for item in occurrences]),
        median_best_subsequent_price=_safe_median([item.best_subsequent_price for item in occurrences]),
        avg_worst_subsequent_price=_safe_mean([item.worst_subsequent_price for item in occurrences]),
        median_worst_subsequent_price=_safe_median([item.worst_subsequent_price for item in occurrences]),
        avg_max_favorable_excursion=_safe_mean([item.max_favorable_excursion for item in occurrences]),
        median_max_favorable_excursion=_safe_median([item.max_favorable_excursion for item in occurrences]),
        avg_max_adverse_excursion=_safe_mean([item.max_adverse_excursion for item in occurrences]),
        median_max_adverse_excursion=_safe_median([item.max_adverse_excursion for item in occurrences]),
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


def build_probability_matrix(
    connection: sqlite3.Connection,
    settled_markets: list[sqlite3.Row] | None = None,
    series_map: dict[str, list[Observation]] | None = None,
) -> list[MatrixCell]:
    settled_markets = settled_markets or _settled_markets_with_data(connection)
    series_map = series_map or _build_series_map(connection, settled_markets)
    buckets: dict[tuple[str, str], list[dict[str, float | bool | str]]] = defaultdict(list)
    for market in settled_markets:
        series = series_map.get(market["ticker"])
        if not series:
            continue

        yes_future_best = [0.0] * len(series)
        no_future_best = [0.0] * len(series)

        running_yes_best = 0.0
        running_no_best = 0.0

        for index in range(len(series) - 1, -1, -1):
            observation = series[index]

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
                    [
                        (int(low * 100), int(high * 100), label)
                        for low, high, label in PRICE_BUCKETS
                    ],
                )

                time_label = _bucket_label(
                    observation.seconds_remaining,
                    MATRIX_TIME_BUCKETS,
                )

                future_best = (
                    yes_future_best[index]
                    if side == "yes"
                    else no_future_best[index]
                )

                buckets[(price_label, time_label)].append(
                    {
                        "won": _eventual_win(market["result"], side),
                        "touch_30": future_best >= 0.30,
                        "touch_35": future_best >= 0.35,
                        "touch_40": future_best >= 0.40,
                        "touch_50": future_best >= 0.50,
                        "plus_5": future_best >= min(1.0, current_price + 0.05),
                        "plus_10": future_best >= min(1.0, current_price + 0.10),
                        "plus_15": future_best >= min(1.0, current_price + 0.15),
                        "plus_20": future_best >= min(1.0, current_price + 0.20),
                        "best_price": future_best,
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
                        unique_markets=0,
                        win_rate=None,
                        touch_30_rate=None,
                        touch_35_rate=None,
                        touch_40_rate=None,
                        touch_50_rate=None,
                        plus_5c_rate=None,
                        plus_10c_rate=None,
                        plus_15c_rate=None,
                        plus_20c_rate=None,
                        avg_best_subsequent_price=None,
                        median_best_subsequent_price=None,
                    )
                )
                continue
            best_prices = [float(item["best_price"]) for item in items]
            cells.append(
                MatrixCell(
                    price_bucket=price_label,
                    time_bucket=time_label,
                    observations=len(items),
                    unique_markets=len({str(item["market_ticker"]) for item in items}),
                    win_rate=sum(bool(item["won"]) for item in items) / len(items),
                    touch_30_rate=sum(bool(item["touch_30"]) for item in items) / len(items),
                    touch_35_rate=sum(bool(item["touch_35"]) for item in items) / len(items),
                    touch_40_rate=sum(bool(item["touch_40"]) for item in items) / len(items),
                    touch_50_rate=sum(bool(item["touch_50"]) for item in items) / len(items),
                    plus_5c_rate=sum(bool(item["plus_5"]) for item in items) / len(items),
                    plus_10c_rate=sum(bool(item["plus_10"]) for item in items) / len(items),
                    plus_15c_rate=sum(bool(item["plus_15"]) for item in items) / len(items),
                    plus_20c_rate=sum(bool(item["plus_20"]) for item in items) / len(items),
                    avg_best_subsequent_price=mean(best_prices),
                    median_best_subsequent_price=median(best_prices),
                )
            )
    return cells


def build_live_scenario_board(
    connection: sqlite3.Connection, definitions: list[ScenarioDefinition], summaries: list[ScenarioSummary]
) -> list[LiveScenarioMatch]:
    latest_rows = connection.execute(
        """
        SELECT m.ticker, m.status, m.close_time, s.yes_bid, s.yes_ask, s.collected_at
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
        close_ts = _iso_to_ts(row["close_time"])
        seconds_remaining = max(0, close_ts - observed_ts)
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
        SELECT m.ticker, m.status, m.close_time, s.yes_bid, s.yes_ask, s.collected_at
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
        close_ts = _iso_to_ts(row["close_time"])
        seconds_remaining = max(0, close_ts - observed_ts)
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
