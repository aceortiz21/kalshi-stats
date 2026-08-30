from __future__ import annotations

from collections import defaultdict
from random import Random
from statistics import mean

from .analytics import (
    _build_series_map,
    _settled_markets_with_data,
    build_strategy_entries,
)
from .database import (
    connect,
    init_db,
)
from .strategies import (
    DEFAULT_EXIT_STRATEGIES,
    simulate_strategy_entries,
    summarize_strategy,
)


BASE_PRICE_BUCKET = "60-69c"
BASE_TIME_BUCKET = "5-10m left"
BASE_STRATEGY_ID = "tp15_sl5"

DISCOVERY_FRACTION = 0.60
VALIDATION_END_FRACTION = 0.80

MIN_FILTER_N = 100
BOOTSTRAP_REPS = 2000
BOOTSTRAP_SEED = 20260830


def _strategy():
    for strategy in DEFAULT_EXIT_STRATEGIES:
        if strategy.id == BASE_STRATEGY_ID:
            return strategy

    raise RuntimeError(
        f"Strategy not found: {BASE_STRATEGY_ID}"
    )


def _chunks(values, size=500):
    for index in range(
        0,
        len(values),
        size,
    ):
        yield values[
            index:index + size
        ]


def chronological_three_way_split(
    markets,
):
    ordered = sorted(
        markets,
        key=lambda market: (
            market["close_time"]
        ),
    )

    total = len(ordered)

    discovery_end = int(
        total
        * DISCOVERY_FRACTION
    )

    validation_end = int(
        total
        * VALIDATION_END_FRACTION
    )

    return (
        ordered[:discovery_end],
        ordered[
            discovery_end:
            validation_end
        ],
        ordered[validation_end:],
    )


def load_feature_map(
    connection,
    market_tickers,
):
    feature_map = {}

    for chunk in _chunks(
        list(market_tickers)
    ):
        placeholders = ",".join(
            "?"
            for _ in chunk
        )

        rows = connection.execute(
            f"""
            SELECT
                market_ticker,
                observed_ts,

                threshold_distance_bps,
                threshold_distance_vol60,

                return_300s,

                ema_5m,
                ema_9m,
                ema_21m,

                vwap_distance_300s_bps

            FROM historical_market_features

            WHERE market_ticker
                IN ({placeholders})
            """,
            chunk,
        ).fetchall()

        for row in rows:
            feature_map[
                (
                    str(
                        row[
                            "market_ticker"
                        ]
                    ),
                    int(
                        row[
                            "observed_ts"
                        ]
                    ),
                )
            ] = row

    return feature_map


def _aligned(
    value,
    side: str,
):
    if value is None:
        return None

    number = float(
        value
    )

    return (
        number
        if side == "yes"
        else -number
    )


def target_side(
    row,
    side,
) -> bool:
    value = _aligned(
        row[
            "threshold_distance_bps"
        ],
        side,
    )

    return (
        value is not None
        and value > 0
    )


def target_one_vol(
    row,
    side,
) -> bool:
    value = _aligned(
        row[
            "threshold_distance_vol60"
        ],
        side,
    )

    return (
        value is not None
        and value >= 1.0
    )


def ema_stack(
    row,
    side,
) -> bool:
    ema5 = row[
        "ema_5m"
    ]

    ema9 = row[
        "ema_9m"
    ]

    ema21 = row[
        "ema_21m"
    ]

    if (
        ema5 is None
        or ema9 is None
        or ema21 is None
    ):
        return False

    ema5 = float(ema5)
    ema9 = float(ema9)
    ema21 = float(ema21)

    if side == "yes":
        return (
            ema5
            > ema9
            > ema21
        )

    return (
        ema5
        < ema9
        < ema21
    )


def vwap_five_minute(
    row,
    side,
) -> bool:
    value = _aligned(
        row[
            "vwap_distance_300s_bps"
        ],
        side,
    )

    return (
        value is not None
        and value > 0
    )


def momentum_five_minute(
    row,
    side,
) -> bool:
    value = _aligned(
        row[
            "return_300s"
        ],
        side,
    )

    return (
        value is not None
        and value > 0
    )


def confluence(
    row,
    side,
) -> bool:
    return (
        target_side(
            row,
            side,
        )
        and ema_stack(
            row,
            side,
        )
        and vwap_five_minute(
            row,
            side,
        )
    )


FILTERS = (
    (
        "TARGET_SIDE",
        target_side,
    ),
    (
        "TARGET_1VOL",
        target_one_vol,
    ),
    (
        "EMA_STACK",
        ema_stack,
    ),
    (
        "VWAP_5M",
        vwap_five_minute,
    ),
    (
        "MOMENTUM_5M",
        momentum_five_minute,
    ),
    (
        "CONFLUENCE",
        confluence,
    ),
)


def _usable(
    outcomes,
):
    return [
        outcome
        for outcome in outcomes
        if outcome.exit_reason
        not in {
            "AMBIGUOUS",
            "INELIGIBLE",
        }
    ]


def _percentile(
    values,
    probability: float,
):
    if not values:
        return None

    ordered = sorted(
        values
    )

    if len(ordered) == 1:
        return ordered[0]

    position = (
        (len(ordered) - 1)
        * probability
    )

    low = int(
        position
    )

    high = min(
        low + 1,
        len(ordered) - 1,
    )

    fraction = (
        position - low
    )

    return (
        ordered[low]
        * (1.0 - fraction)
        + ordered[high]
        * fraction
    )


def cluster_bootstrap_uplift(
    baseline_outcomes,
    filtered_outcomes,
    *,
    reps: int = BOOTSTRAP_REPS,
    seed: int = BOOTSTRAP_SEED,
):
    baseline = (
        _usable(
            baseline_outcomes
        )
    )

    filtered = (
        _usable(
            filtered_outcomes
        )
    )

    baseline_by_market = (
        defaultdict(list)
    )

    filtered_by_market = (
        defaultdict(list)
    )

    for outcome in baseline:
        baseline_by_market[
            outcome.market_ticker
        ].append(
            outcome.profit
        )

    for outcome in filtered:
        filtered_by_market[
            outcome.market_ticker
        ].append(
            outcome.profit
        )

    markets = sorted(
        baseline_by_market
    )

    if (
        not markets
        or not filtered
    ):
        return (
            None,
            None,
        )

    rng = Random(
        seed
    )

    uplifts = []

    for _ in range(
        reps
    ):
        baseline_profits = []
        filtered_profits = []

        for _ in markets:
            ticker = rng.choice(
                markets
            )

            baseline_profits.extend(
                baseline_by_market[
                    ticker
                ]
            )

            filtered_profits.extend(
                filtered_by_market.get(
                    ticker,
                    [],
                )
            )

        if not filtered_profits:
            continue

        uplifts.append(
            mean(
                filtered_profits
            )
            - mean(
                baseline_profits
            )
        )

    return (
        _percentile(
            uplifts,
            0.025,
        ),
        _percentile(
            uplifts,
            0.975,
        ),
    )


def _entry_key(
    entry,
):
    return (
        entry.market_ticker,
        entry.side,
        entry.entry_ts,
    )


def evaluate_split(
    *,
    entries,
    series_map,
    feature_map,
):
    strategy = _strategy()

    base_entries = [
        entry
        for entry in entries
        if (
            entry.price_bucket
            == BASE_PRICE_BUCKET
            and entry.time_bucket
            == BASE_TIME_BUCKET
            and (
                entry.market_ticker,
                entry.entry_ts,
            )
            in feature_map
        )
    ]

    baseline_outcomes = (
        simulate_strategy_entries(
            strategy=strategy,
            entries=base_entries,
            series_map=series_map,
            ambiguity_mode=(
                "conservative"
            ),
        )
    )

    outcome_map = {
        (
            outcome.market_ticker,
            outcome.traded_side,
            outcome.entry_ts,
        ): outcome
        for outcome
        in baseline_outcomes
    }

    baseline_summary = (
        summarize_strategy(
            strategy,
            baseline_outcomes,
        )
    )

    results = []

    for (
        filter_name,
        predicate,
    ) in FILTERS:

        selected_entries = []

        for entry in base_entries:
            feature = feature_map[
                (
                    entry.market_ticker,
                    entry.entry_ts,
                )
            ]

            if predicate(
                feature,
                entry.side,
            ):
                selected_entries.append(
                    entry
                )

        filtered_outcomes = [
            outcome_map[
                _entry_key(
                    entry
                )
            ]
            for entry
            in selected_entries
            if _entry_key(
                entry
            )
            in outcome_map
        ]

        summary = (
            summarize_strategy(
                strategy,
                filtered_outcomes,
            )
        )

        uplift_low, uplift_high = (
            cluster_bootstrap_uplift(
                baseline_outcomes,
                filtered_outcomes,
            )
        )

        uplift = (
            None
            if (
                summary.avg_profit
                is None
                or baseline_summary.avg_profit
                is None
            )
            else (
                summary.avg_profit
                - baseline_summary.avg_profit
            )
        )

        results.append(
            {
                "name": filter_name,
                "summary": summary,
                "uplift": uplift,
                "uplift_low": (
                    uplift_low
                ),
                "uplift_high": (
                    uplift_high
                ),
            }
        )

    return (
        baseline_summary,
        results,
    )


def _cents(
    value,
):
    if value is None:
        return "-"

    return (
        f"{value * 100:+.2f}c"
    )


def _rate(
    value,
):
    if value is None:
        return "-"

    return (
        f"{value * 100:.1f}%"
    )


def _ci(
    low,
    high,
):
    if (
        low is None
        or high is None
    ):
        return "-"

    return (
        f"[{low * 100:+.2f},"
        f"{high * 100:+.2f}]c"
    )


def _print_baseline(
    label,
    summary,
):
    print(
        f"{label:<10} "
        f"N={summary.observations:<5} "
        f"avg={_cents(summary.avg_profit):>8} "
        f"win={_rate(summary.win_rate):>6} "
        f"95%={_ci(summary.profit_ci_low, summary.profit_ci_high)}"
    )


def main():
    parser = __import__(
        "argparse"
    ).ArgumentParser()

    parser.add_argument(
        "--db",
        required=True,
    )

    args = parser.parse_args()

    connection = connect(
        args.db
    )

    try:
        init_db(
            connection
        )

        covered = {
            str(
                row[0]
            )
            for row
            in connection.execute(
                """
                SELECT DISTINCT
                    market_ticker
                FROM historical_market_features
                """
            )
        }

        markets = [
            market
            for market
            in _settled_markets_with_data(
                connection
            )
            if str(
                market["ticker"]
            )
            in covered
        ]

        (
            discovery_markets,
            validation_markets,
            locked_markets,
        ) = (
            chronological_three_way_split(
                markets
            )
        )

        working_markets = (
            discovery_markets
            + validation_markets
        )

        working_tickers = {
            str(
                market["ticker"]
            )
            for market
            in working_markets
        }

        feature_map = (
            load_feature_map(
                connection,
                working_tickers,
            )
        )

        series_map = (
            _build_series_map(
                connection,
                working_markets,
            )
        )

        all_entries, _ = (
            build_strategy_entries(
                connection,
                settled_markets=(
                    working_markets
                ),
                series_map=(
                    series_map
                ),
            )
        )

        discovery_tickers = {
            str(
                market["ticker"]
            )
            for market
            in discovery_markets
        }

        validation_tickers = {
            str(
                market["ticker"]
            )
            for market
            in validation_markets
        }

        discovery_entries = [
            entry
            for entry
            in all_entries
            if entry.market_ticker
            in discovery_tickers
        ]

        validation_entries = [
            entry
            for entry
            in all_entries
            if entry.market_ticker
            in validation_tickers
        ]

        (
            discovery_base,
            discovery_results,
        ) = evaluate_split(
            entries=discovery_entries,
            series_map=series_map,
            feature_map=feature_map,
        )

        (
            validation_base,
            validation_results,
        ) = evaluate_split(
            entries=validation_entries,
            series_map=series_map,
            feature_map=feature_map,
        )

        print()
        print(
            "=" * 110
        )
        print(
            "FINANCIAL CONFIRMATION SCREEN"
        )
        print(
            "=" * 110
        )

        print(
            "Base:",
            BASE_PRICE_BUCKET,
            "/",
            BASE_TIME_BUCKET,
            "/ TP +15c / SL -5c",
        )

        print()
        print(
            "Feature-covered markets:",
            f"{len(markets):,}",
        )

        print(
            "Discovery markets:",
            f"{len(discovery_markets):,}",
        )

        print(
            "Validation markets:",
            f"{len(validation_markets):,}",
        )

        print(
            "LOCKED test markets:",
            f"{len(locked_markets):,}",
            "(NOT EVALUATED)",
        )

        if locked_markets:
            print(
                "Locked period:",
                locked_markets[0][
                    "close_time"
                ],
                "->",
                locked_markets[-1][
                    "close_time"
                ],
            )

        print()
        print(
            "BASELINE"
        )

        _print_baseline(
            "Discovery",
            discovery_base,
        )

        _print_baseline(
            "Validation",
            validation_base,
        )

        print()
        print(
            "FILTER        "
            "D_N    D_AVG     D_UPLIFT CI             "
            "V_N    V_AVG     V_UPLIFT CI             STATUS"
        )

        print(
            "-" * 110
        )

        validation_map = {
            row["name"]: row
            for row
            in validation_results
        }

        for discovery in discovery_results:
            validation = (
                validation_map[
                    discovery[
                        "name"
                    ]
                ]
            )

            d_summary = discovery[
                "summary"
            ]

            v_summary = validation[
                "summary"
            ]

            status = "NO_ADD"

            if (
                d_summary.observations
                >= MIN_FILTER_N
                and v_summary.observations
                >= MIN_FILTER_N
                and discovery[
                    "uplift_low"
                ] is not None
                and discovery[
                    "uplift_low"
                ] > 0
                and validation[
                    "uplift_low"
                ] is not None
                and validation[
                    "uplift_low"
                ] > 0
                and v_summary.avg_profit
                is not None
                and v_summary.avg_profit
                > 0
            ):
                status = (
                    "CONFIRMED"
                )

            elif (
                d_summary.observations
                >= MIN_FILTER_N
                and v_summary.observations
                >= MIN_FILTER_N
                and discovery[
                    "uplift"
                ] is not None
                and discovery[
                    "uplift"
                ] > 0
                and validation[
                    "uplift"
                ] is not None
                and validation[
                    "uplift"
                ] > 0
                and v_summary.avg_profit
                is not None
                and v_summary.avg_profit
                > 0
            ):
                status = (
                    "PROMISING"
                )

            print(
                f"{discovery['name']:<13}"
                f"{d_summary.observations:<7}"
                f"{_cents(d_summary.avg_profit):<10}"
                f"{_ci(discovery['uplift_low'], discovery['uplift_high']):<25}"
                f"{v_summary.observations:<7}"
                f"{_cents(v_summary.avg_profit):<10}"
                f"{_ci(validation['uplift_low'], validation['uplift_high']):<25}"
                f"{status}"
            )

        print()
        print(
            "The final 20% remains locked. "
            "Do not evaluate it until the financial "
            "candidate family is frozen."
        )

        print(
            "Fees/slippage remain omitted."
        )

    finally:
        connection.close()


if __name__ == "__main__":
    main()
