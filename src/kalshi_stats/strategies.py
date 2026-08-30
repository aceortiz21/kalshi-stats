"""Historical exit-strategy simulation.

This module applies mechanical exit rules to an already-known historical
entry. It intentionally keeps strategy simulation separate from scenario
discovery so entries and exits can be tested independently.

For 1-minute OHLC candles, the order of the candle high and low is unknown.
If both a take-profit and stop-loss level are touched inside the same candle,
the outcome is marked ambiguous. The ambiguity_mode controls how that candle
is resolved.
"""

from __future__ import annotations

from math import sqrt

from statistics import mean, median

from .models import ExitStrategy, Observation, StrategyOutcome, StrategySummary


DEFAULT_EXIT_STRATEGIES = [
    ExitStrategy(
        id="tp10_settle",
        name="TP +10c / otherwise settle",
        take_profit_cents=10,
    ),
    ExitStrategy(
        id="tp10_sl5",
        name="TP +10c / SL -5c",
        take_profit_cents=10,
        stop_loss_cents=5,
    ),
    ExitStrategy(
        id="tp15_sl5",
        name="TP +15c / SL -5c",
        take_profit_cents=15,
        stop_loss_cents=5,
    ),
    ExitStrategy(
        id="tp10_sl10",
        name="TP +10c / SL -10c",
        take_profit_cents=10,
        stop_loss_cents=10,
    ),
    ExitStrategy(
        id="exit_60s",
        name="Exit after 60s",
        time_exit_seconds=60,
        hold_to_settlement=False,
    ),
    ExitStrategy(
        id="exit_120s",
        name="Exit after 120s",
        time_exit_seconds=120,
        hold_to_settlement=False,
    ),
    ExitStrategy(
        id="exit_180s",
        name="Exit after 180s",
        time_exit_seconds=180,
        hold_to_settlement=False,
    ),
    ExitStrategy(
        id="tp10_exit120",
        name="TP +10c / otherwise exit after 120s",
        take_profit_cents=10,
        time_exit_seconds=120,
        hold_to_settlement=False,
    ),
    ExitStrategy(
        id="settlement",
        name="Hold to settlement",
    ),
]


def _side_prices(
    observation: Observation,
    side: str,
) -> tuple[float, float, float]:
    """Return close, low, high from the perspective of the traded side."""

    if side == "yes":
        return (
            observation.yes_close,
            observation.yes_low,
            observation.yes_high,
        )

    if side == "no":
        return (
            1.0 - observation.yes_close,
            1.0 - observation.yes_high,
            1.0 - observation.yes_low,
        )

    raise ValueError(f"Unknown side: {side}")


def _settlement_price(eventual_win: bool) -> float:
    return 1.0 if eventual_win else 0.0


def simulate_exit_strategy(
    *,
    strategy: ExitStrategy,
    market_ticker: str,
    traded_side: str,
    entry_ts: int,
    entry_price: float,
    eventual_win: bool,
    future: list[Observation],
    ambiguity_mode: str = "conservative",
) -> StrategyOutcome:
    """Apply one exit rule to one historical entry.

    ambiguity_mode:
      conservative -> stop-loss wins same-candle TP/SL ties
      optimistic   -> take-profit wins same-candle TP/SL ties
      exclude      -> return an AMBIGUOUS outcome
    """

    if ambiguity_mode not in {"conservative", "optimistic", "exclude"}:
        raise ValueError(
            "ambiguity_mode must be conservative, optimistic, or exclude"
        )

    tp_price = (
        None
        if strategy.take_profit_cents is None
        else entry_price + strategy.take_profit_cents / 100.0
    )
    sl_price = (
        None
        if strategy.stop_loss_cents is None
        else entry_price - strategy.stop_loss_cents / 100.0
    )

    # If the exact strategy cannot be executed from this entry price,
    # exclude the entry rather than silently changing the strategy.
    if tp_price is not None and tp_price > 1.0:
        return StrategyOutcome(
            strategy_id=strategy.id,
            market_ticker=market_ticker,
            traded_side=traded_side,
            entry_ts=entry_ts,
            entry_price=entry_price,
            exit_reason="INELIGIBLE",
            exit_price=entry_price,
            profit=0.0,
            holding_seconds=None,
            take_profit_hit=False,
            stop_loss_hit=False,
            ambiguous=False,
        )

    if sl_price is not None and sl_price < 0.0:
        return StrategyOutcome(
            strategy_id=strategy.id,
            market_ticker=market_ticker,
            traded_side=traded_side,
            entry_ts=entry_ts,
            entry_price=entry_price,
            exit_reason="INELIGIBLE",
            exit_price=entry_price,
            profit=0.0,
            holding_seconds=None,
            take_profit_hit=False,
            stop_loss_hit=False,
            ambiguous=False,
        )

    for observation in future:
        elapsed = observation.observed_ts - entry_ts

        close_price, low_price, high_price = _side_prices(
            observation,
            traded_side,
        )

        tp_hit = tp_price is not None and high_price >= tp_price
        sl_hit = sl_price is not None and low_price <= sl_price

        if tp_hit and sl_hit:
            if ambiguity_mode == "exclude":
                return StrategyOutcome(
                    strategy_id=strategy.id,
                    market_ticker=market_ticker,
                    traded_side=traded_side,
                    entry_ts=entry_ts,
                    entry_price=entry_price,
                    exit_reason="AMBIGUOUS",
                    exit_price=entry_price,
                    profit=0.0,
                    holding_seconds=elapsed,
                    take_profit_hit=True,
                    stop_loss_hit=True,
                    ambiguous=True,
                )

            if ambiguity_mode == "conservative":
                return StrategyOutcome(
                    strategy_id=strategy.id,
                    market_ticker=market_ticker,
                    traded_side=traded_side,
                    entry_ts=entry_ts,
                    entry_price=entry_price,
                    exit_reason="STOP_LOSS",
                    exit_price=sl_price,
                    profit=sl_price - entry_price,
                    holding_seconds=elapsed,
                    take_profit_hit=True,
                    stop_loss_hit=True,
                    ambiguous=True,
                )

            return StrategyOutcome(
                strategy_id=strategy.id,
                market_ticker=market_ticker,
                traded_side=traded_side,
                entry_ts=entry_ts,
                entry_price=entry_price,
                exit_reason="TAKE_PROFIT",
                exit_price=tp_price,
                profit=tp_price - entry_price,
                holding_seconds=elapsed,
                take_profit_hit=True,
                stop_loss_hit=True,
                ambiguous=True,
            )

        if tp_hit:
            return StrategyOutcome(
                strategy_id=strategy.id,
                market_ticker=market_ticker,
                traded_side=traded_side,
                entry_ts=entry_ts,
                entry_price=entry_price,
                exit_reason="TAKE_PROFIT",
                exit_price=tp_price,
                profit=tp_price - entry_price,
                holding_seconds=elapsed,
                take_profit_hit=True,
                stop_loss_hit=False,
                ambiguous=False,
            )

        if sl_hit:
            return StrategyOutcome(
                strategy_id=strategy.id,
                market_ticker=market_ticker,
                traded_side=traded_side,
                entry_ts=entry_ts,
                entry_price=entry_price,
                exit_reason="STOP_LOSS",
                exit_price=sl_price,
                profit=sl_price - entry_price,
                holding_seconds=elapsed,
                take_profit_hit=False,
                stop_loss_hit=True,
                ambiguous=False,
            )

        if (
            strategy.time_exit_seconds is not None
            and elapsed >= strategy.time_exit_seconds
        ):
            return StrategyOutcome(
                strategy_id=strategy.id,
                market_ticker=market_ticker,
                traded_side=traded_side,
                entry_ts=entry_ts,
                entry_price=entry_price,
                exit_reason="TIME_EXIT",
                exit_price=close_price,
                profit=close_price - entry_price,
                holding_seconds=elapsed,
                take_profit_hit=False,
                stop_loss_hit=False,
                ambiguous=False,
            )

    settlement = _settlement_price(eventual_win)

    return StrategyOutcome(
        strategy_id=strategy.id,
        market_ticker=market_ticker,
        traded_side=traded_side,
        entry_ts=entry_ts,
        entry_price=entry_price,
        exit_reason="SETTLEMENT",
        exit_price=settlement,
        profit=settlement - entry_price,
        holding_seconds=None,
        take_profit_hit=False,
        stop_loss_hit=False,
        ambiguous=False,
    )


def summarize_strategy(
    strategy: ExitStrategy,
    outcomes: list[StrategyOutcome],
) -> StrategySummary:
    """Aggregate individual simulated outcomes."""

    usable = [
        outcome
        for outcome in outcomes
        if outcome.exit_reason not in {"AMBIGUOUS", "INELIGIBLE"}
    ]

    wins = sum(outcome.profit > 0 for outcome in usable)
    losses = sum(outcome.profit < 0 for outcome in usable)
    breakevens = sum(outcome.profit == 0 for outcome in usable)

    profits = [outcome.profit for outcome in usable]

    avg_profit = mean(profits) if profits else None
    median_profit = median(profits) if profits else None

    if len(profits) >= 2:
        variance = sum(
            (profit - avg_profit) ** 2
            for profit in profits
        ) / (len(profits) - 1)

        profit_stddev = sqrt(variance)
        standard_error = profit_stddev / sqrt(len(profits))

        profit_ci_low = avg_profit - 1.96 * standard_error
        profit_ci_high = avg_profit + 1.96 * standard_error
    else:
        profit_stddev = None
        profit_ci_low = None
        profit_ci_high = None

    total = len(usable)

    return StrategySummary(
        strategy=strategy,
        observations=total,
        wins=wins,
        losses=losses,
        breakevens=breakevens,
        ambiguous=sum(outcome.ambiguous for outcome in outcomes),
        win_rate=(wins / total if total else None),
        avg_profit=(mean(profits) if profits else None),
        median_profit=(median(profits) if profits else None),
        profit_stddev=profit_stddev,
        profit_ci_low=profit_ci_low,
        profit_ci_high=profit_ci_high,
        take_profit_rate=(
            sum(o.exit_reason == "TAKE_PROFIT" for o in usable) / total
            if total else None
        ),
        stop_loss_rate=(
            sum(o.exit_reason == "STOP_LOSS" for o in usable) / total
            if total else None
        ),
        settlement_exit_rate=(
            sum(o.exit_reason == "SETTLEMENT" for o in usable) / total
            if total else None
        ),
        time_exit_rate=(
            sum(o.exit_reason == "TIME_EXIT" for o in usable) / total
            if total else None
        ),
        ambiguous_rate=(
            sum(o.ambiguous for o in outcomes) / len(outcomes)
            if outcomes else None
        ),
    )


def simulate_strategy_entries(
    *,
    strategy: ExitStrategy,
    entries,
    series_map: dict[str, list[Observation]],
    price_bucket: str | None = None,
    time_bucket: str | None = None,
    ambiguity_mode: str = "conservative",
) -> list[StrategyOutcome]:
    """Simulate one strategy across selected historical state entries."""

    outcomes: list[StrategyOutcome] = []

    for entry in entries:
        if price_bucket is not None and entry.price_bucket != price_bucket:
            continue

        if time_bucket is not None and entry.time_bucket != time_bucket:
            continue

        series = series_map.get(entry.market_ticker)

        if not series:
            continue

        entry_observation = series[entry.entry_index]

        # Preserve the exact same look-ahead rule used elsewhere:
        # candle entry -> begin with next candle
        # exact observation -> current observation is safe
        if entry_observation.source == "candle":
            future = series[entry.entry_index + 1:]
        else:
            future = series[entry.entry_index:]

        outcomes.append(
            simulate_exit_strategy(
                strategy=strategy,
                market_ticker=entry.market_ticker,
                traded_side=entry.side,
                entry_ts=entry.entry_ts,
                entry_price=entry.entry_price,
                eventual_win=entry.eventual_win,
                future=future,
                ambiguity_mode=ambiguity_mode,
            )
        )

    return outcomes
