from __future__ import annotations

import argparse

from .analytics import (
    _build_series_map,
    _settled_markets_with_data,
    build_strategy_entries,
    build_validated_strategies,
    chronological_market_split,
    classify_validated_strategy,
)
from .database import (
    connect,
    init_db,
)
from .robustness import (
    cluster_bootstrap_mean_ci,
    stable_seed,
)
from .strategies import (
    simulate_strategy_entries,
    summarize_strategy,
)


MIN_HOLDOUT_N = 100
EFFECT_FLOOR = 0.005

AMBIGUITY_MODES = (
    "conservative",
    "exclude",
    "optimistic",
)


def _cents(value):
    if value is None:
        return "-"

    return (
        f"{value * 100:+.2f}c"
    )


def _ci(low, high):
    if (
        low is None
        or high is None
    ):
        return "[-, -]"

    return (
        f"[{low * 100:+.2f}, "
        f"{high * 100:+.2f}]"
    )


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--db",
        required=True,
    )

    parser.add_argument(
        "--bootstrap",
        type=int,
        default=3000,
    )

    args = parser.parse_args()

    connection = connect(
        args.db
    )

    try:
        init_db(connection)

        markets = (
            _settled_markets_with_data(
                connection
            )
        )

        (
            discovery_markets,
            holdout_markets,
        ) = chronological_market_split(
            markets,
            discovery_fraction=0.80,
        )

        series_map = (
            _build_series_map(
                connection,
                markets,
            )
        )

        entries, series_map = (
            build_strategy_entries(
                connection,
                settled_markets=markets,
                series_map=series_map,
            )
        )

        current = (
            build_validated_strategies(
                connection,
                settled_markets=markets,
                series_map=series_map,
                strategy_entries=entries,
                discovery_fraction=0.80,
                min_discovery_n=500,
                min_holdout_n=100,
                ambiguity_mode=(
                    "conservative"
                ),
            )
        )

        strong = [
            result
            for result in current
            if (
                result.validation_status
                == "STRONG"
            )
        ]

        discovery_tickers = {
            market["ticker"]
            for market
            in discovery_markets
        }

        holdout_tickers = {
            market["ticker"]
            for market
            in holdout_markets
        }

        discovery_by_state = {}
        holdout_by_state = {}

        for entry in entries:
            state = (
                entry.price_bucket,
                entry.time_bucket,
            )

            if (
                entry.market_ticker
                in discovery_tickers
            ):
                discovery_by_state.setdefault(
                    state,
                    [],
                ).append(
                    entry
                )

            elif (
                entry.market_ticker
                in holdout_tickers
            ):
                holdout_by_state.setdefault(
                    state,
                    [],
                ).append(
                    entry
                )

        bootstrap_positive = 0
        bootstrap_floor = 0
        ambiguity_stable = 0
        strongest = []

        print()
        print(
            "CLUSTER BOOTSTRAP + "
            "AMBIGUITY SENSITIVITY"
        )
        print("=" * 122)

        print(
            "Bootstrap unit: market ticker"
        )

        print(
            "Bootstrap iterations: "
            f"{args.bootstrap}"
        )

        print(
            "Gross effect reference: "
            f"{EFFECT_FLOOR * 100:.2f}c"
        )

        for index, result in enumerate(
            strong,
            start=1,
        ):
            state = (
                result.price_bucket,
                result.time_bucket,
            )

            discovery_entries = (
                discovery_by_state.get(
                    state,
                    [],
                )
            )

            holdout_entries = (
                holdout_by_state.get(
                    state,
                    [],
                )
            )

            mode_results = {}

            conservative_outcomes = None

            for mode in (
                AMBIGUITY_MODES
            ):
                discovery_outcomes = (
                    simulate_strategy_entries(
                        strategy=(
                            result.strategy
                        ),
                        entries=(
                            discovery_entries
                        ),
                        series_map=(
                            series_map
                        ),
                        ambiguity_mode=mode,
                    )
                )

                holdout_outcomes = (
                    simulate_strategy_entries(
                        strategy=(
                            result.strategy
                        ),
                        entries=(
                            holdout_entries
                        ),
                        series_map=(
                            series_map
                        ),
                        ambiguity_mode=mode,
                    )
                )

                discovery_summary = (
                    summarize_strategy(
                        result.strategy,
                        discovery_outcomes,
                    )
                )

                holdout_summary = (
                    summarize_strategy(
                        result.strategy,
                        holdout_outcomes,
                    )
                )

                status = (
                    classify_validated_strategy(
                        holdout_summary,
                        min_holdout_n=(
                            MIN_HOLDOUT_N
                        ),
                    )
                )

                mode_results[
                    mode
                ] = {
                    "discovery": (
                        discovery_summary
                    ),
                    "holdout": (
                        holdout_summary
                    ),
                    "status": status,
                }

                if (
                    mode
                    == "conservative"
                ):
                    conservative_outcomes = (
                        holdout_outcomes
                    )

            key = (
                f"{result.price_bucket}|"
                f"{result.time_bucket}|"
                f"{result.strategy.id}"
            )

            bootstrap = (
                cluster_bootstrap_mean_ci(
                    conservative_outcomes
                    or [],
                    iterations=(
                        args.bootstrap
                    ),
                    seed=stable_seed(
                        key
                    ),
                )
            )

            boot_low = (
                bootstrap[
                    "ci_low"
                ]
            )

            boot_is_positive = bool(
                boot_low is not None
                and boot_low > 0
            )

            boot_above_floor = bool(
                boot_low is not None
                and boot_low
                > EFFECT_FLOOR
            )

            if boot_is_positive:
                bootstrap_positive += 1

            if boot_above_floor:
                bootstrap_floor += 1

            # Conservative and exclude are the key ambiguity
            # checks. Optimistic is displayed but not required.
            conservative = (
                mode_results[
                    "conservative"
                ][
                    "holdout"
                ]
            )

            exclude = (
                mode_results[
                    "exclude"
                ][
                    "holdout"
                ]
            )

            stable = bool(
                conservative.observations
                    >= MIN_HOLDOUT_N
                and exclude.observations
                    >= MIN_HOLDOUT_N
                and conservative.avg_profit
                    is not None
                and conservative.avg_profit
                    > 0
                and exclude.avg_profit
                    is not None
                and exclude.avg_profit
                    > 0
            )

            if stable:
                ambiguity_stable += 1

            if (
                stable
                and boot_above_floor
            ):
                strongest.append(
                    key
                )

            print()
            print(
                f"{index:2}. "
                f"{result.price_bucket:<8} "
                f"{result.time_bucket:<13} "
                f"{result.strategy.name}"
            )

            print(
                "    Cluster bootstrap | "
                f"markets="
                f"{bootstrap['cluster_count']:<5} "
                f"N="
                f"{bootstrap['observations']:<5} "
                f"avg="
                f"{_cents(bootstrap['mean'])} "
                f"95%="
                f"{_ci(boot_low, bootstrap['ci_high'])}"
            )

            print(
                "    Bootstrap verdict | "
                f"positive="
                f"{'YES' if boot_is_positive else 'NO'} | "
                f">+0.5c="
                f"{'YES' if boot_above_floor else 'NO'}"
            )

            for mode in (
                AMBIGUITY_MODES
            ):
                data = (
                    mode_results[
                        mode
                    ]
                )

                discovery_summary = (
                    data[
                        "discovery"
                    ]
                )

                holdout_summary = (
                    data[
                        "holdout"
                    ]
                )

                print(
                    f"    {mode:<12} | "
                    f"D="
                    f"{_cents(discovery_summary.avg_profit):>7} "
                    f"N={discovery_summary.observations:<5} | "
                    f"H="
                    f"{_cents(holdout_summary.avg_profit):>7} "
                    f"N={holdout_summary.observations:<5} | "
                    f"{data['status']}"
                )

            print(
                "    Ambiguity stable  | "
                f"{'YES' if stable else 'NO'}"
            )

        print()
        print("=" * 122)

        print(
            f"Current STRONG: "
            f"{len(strong)}"
        )

        print(
            "Cluster-bootstrap CI > 0: "
            f"{bootstrap_positive}"
        )

        print(
            "Cluster-bootstrap CI > +0.5c: "
            f"{bootstrap_floor}"
        )

        print(
            "Ambiguity-stable positive: "
            f"{ambiguity_stable}"
        )

        print(
            "Bootstrap >+0.5c AND "
            "ambiguity-stable: "
            f"{len(strongest)}"
        )

        if strongest:
            print()

            print(
                "STRONGEST SENSITIVITY "
                "SURVIVORS"
            )

            for key in strongest:
                print(
                    "  "
                    + key.replace(
                        "|",
                        " / ",
                    )
                )

    finally:
        connection.close()


if __name__ == "__main__":
    main()
