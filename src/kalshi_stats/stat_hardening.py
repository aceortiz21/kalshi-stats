from __future__ import annotations

import argparse

from .analytics import (
    _build_series_map,
    _settled_markets_with_data,
    build_strategy_entries,
    build_validated_strategies,
    chronological_market_split,
    holm_adjust_pvalues,
    one_sided_mean_p_value,
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


ALPHA = 0.05

# 0.005 dollars = 0.5 cents per contract.
MIN_GROSS_EFFECT = 0.005

MIN_DISCOVERY_N = 500
MIN_HOLDOUT_N = 100


def _key(
    price_bucket,
    time_bucket,
    strategy,
):
    return (
        price_bucket,
        time_bucket,
        strategy.id,
    )


def _cents(value) -> str:
    if value is None:
        return "-"

    return (
        f"{value * 100:+.2f}c"
    )


def _p(value) -> str:
    if value is None:
        return "-"

    if value < 0.0001:
        return "<0.0001"

    return f"{value:.4f}"


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--db",
        required=True,
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

        discovery_markets, holdout_markets = (
            chronological_market_split(
                markets,
                discovery_fraction=0.80,
            )
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

        discovery_tickers = {
            market["ticker"]
            for market in discovery_markets
        }

        holdout_tickers = {
            market["ticker"]
            for market in holdout_markets
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
                ).append(entry)

            elif (
                entry.market_ticker
                in holdout_tickers
            ):
                holdout_by_state.setdefault(
                    state,
                    [],
                ).append(entry)

        # ----------------------------------------------------
        # Discovery: test the entire adequately-sized family.
        # H0 is not merely zero profit:
        #
        #     H0: true mean <= +0.5c
        #
        # So significance and minimum effect are tested
        # together.
        # ----------------------------------------------------

        discovery_rows = {}

        for (
            price_bucket,
            time_bucket,
        ), state_entries in (
            discovery_by_state.items()
        ):
            for strategy in (
                DEFAULT_EXIT_STRATEGIES
            ):
                outcomes = (
                    simulate_strategy_entries(
                        strategy=strategy,
                        entries=state_entries,
                        series_map=series_map,
                        ambiguity_mode=(
                            "conservative"
                        ),
                    )
                )

                summary = (
                    summarize_strategy(
                        strategy,
                        outcomes,
                    )
                )

                if (
                    summary.observations
                    < MIN_DISCOVERY_N
                ):
                    continue

                key = _key(
                    price_bucket,
                    time_bucket,
                    strategy,
                )

                raw_p = (
                    one_sided_mean_p_value(
                        summary,
                        null_mean=(
                            MIN_GROSS_EFFECT
                        ),
                    )
                )

                discovery_rows[key] = {
                    "summary": summary,
                    "raw_p": raw_p,
                    "strategy": strategy,
                    "price_bucket": (
                        price_bucket
                    ),
                    "time_bucket": (
                        time_bucket
                    ),
                }

        discovery_adjusted = (
            holm_adjust_pvalues(
                (
                    key,
                    row["raw_p"],
                )
                for key, row
                in discovery_rows.items()
            )
        )

        discovery_survivors = {}

        for key, row in (
            discovery_rows.items()
        ):
            adjusted_p = (
                discovery_adjusted.get(
                    key
                )
            )

            row[
                "adjusted_p"
            ] = adjusted_p

            summary = row["summary"]

            if (
                adjusted_p is not None
                and adjusted_p <= ALPHA
                and summary.avg_profit
                    is not None
                and summary.avg_profit
                    >= MIN_GROSS_EFFECT
            ):
                discovery_survivors[
                    key
                ] = row

        # ----------------------------------------------------
        # Holdout: only discovery survivors are tested.
        # Holm correction is applied across that confirmation
        # family as well.
        # ----------------------------------------------------

        holdout_rows = {}

        for key, row in (
            discovery_survivors.items()
        ):
            price_bucket = (
                row["price_bucket"]
            )

            time_bucket = (
                row["time_bucket"]
            )

            strategy = (
                row["strategy"]
            )

            outcomes = (
                simulate_strategy_entries(
                    strategy=strategy,
                    entries=(
                        holdout_by_state.get(
                            (
                                price_bucket,
                                time_bucket,
                            ),
                            [],
                        )
                    ),
                    series_map=series_map,
                    ambiguity_mode=(
                        "conservative"
                    ),
                )
            )

            summary = (
                summarize_strategy(
                    strategy,
                    outcomes,
                )
            )

            raw_p = None

            if (
                summary.observations
                >= MIN_HOLDOUT_N
            ):
                raw_p = (
                    one_sided_mean_p_value(
                        summary,
                        null_mean=(
                            MIN_GROSS_EFFECT
                        ),
                    )
                )

            holdout_rows[key] = {
                "summary": summary,
                "raw_p": raw_p,
            }

        holdout_adjusted = (
            holm_adjust_pvalues(
                (
                    key,
                    row["raw_p"],
                )
                for key, row
                in holdout_rows.items()
                if row["raw_p"] is not None
            )
        )

        for key, row in (
            holdout_rows.items()
        ):
            row["adjusted_p"] = (
                holdout_adjusted.get(
                    key
                )
            )

        # Current production STRONG set for comparison.
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

        hardened = 0
        discovery_rejected = 0
        holdout_rejected = 0
        insufficient = 0

        print()
        print(
            "MULTIPLE-COMPARISON + "
            "MINIMUM-EFFECT HARDENING"
        )
        print("=" * 118)

        print(
            "Family-wise alpha: "
            f"{ALPHA:.2f} (Holm)"
        )

        print(
            "Minimum gross effect: "
            f"{MIN_GROSS_EFFECT * 100:.2f}c "
            "per contract"
        )

        print(
            "Adequately-sized discovery tests: "
            f"{len(discovery_rows)}"
        )

        print(
            "Holm discovery survivors: "
            f"{len(discovery_survivors)}"
        )

        print()

        for index, result in enumerate(
            strong,
            start=1,
        ):
            key = _key(
                result.price_bucket,
                result.time_bucket,
                result.strategy,
            )

            discovery = (
                discovery_rows.get(
                    key
                )
            )

            holdout = (
                holdout_rows.get(
                    key
                )
            )

            if (
                discovery is None
                or key
                not in discovery_survivors
            ):
                verdict = (
                    "DISCOVERY REJECTED"
                )

                discovery_rejected += 1

            elif (
                holdout is None
                or
                holdout[
                    "summary"
                ].observations
                < MIN_HOLDOUT_N
            ):
                verdict = "INSUFFICIENT"
                insufficient += 1

            elif (
                holdout.get(
                    "adjusted_p"
                )
                is not None
                and
                holdout[
                    "adjusted_p"
                ]
                <= ALPHA
                and
                holdout[
                    "summary"
                ].avg_profit
                is not None
                and
                holdout[
                    "summary"
                ].avg_profit
                >= MIN_GROSS_EFFECT
            ):
                verdict = "HARDENED"
                hardened += 1

            else:
                verdict = (
                    "HOLDOUT REJECTED"
                )

                holdout_rejected += 1

            discovery_adj = (
                discovery.get(
                    "adjusted_p"
                )
                if discovery
                else None
            )

            holdout_adj = (
                holdout.get(
                    "adjusted_p"
                )
                if holdout
                else None
            )

            print(
                f"{index:2}. "
                f"{result.price_bucket:<8} "
                f"{result.time_bucket:<13} "
                f"{result.strategy.name}"
            )

            print(
                "    Discovery | "
                f"N="
                f"{result.discovery_summary.observations:<5} "
                f"avg="
                f"{_cents(result.discovery_summary.avg_profit)} "
                f"Holm-p="
                f"{_p(discovery_adj)}"
            )

            print(
                "    Holdout   | "
                f"N="
                f"{result.holdout_summary.observations:<5} "
                f"avg="
                f"{_cents(result.holdout_summary.avg_profit)} "
                f"Holm-p="
                f"{_p(holdout_adj)}"
            )

            print(
                "    Verdict   | "
                f"{verdict}"
            )

        print()
        print("=" * 118)

        print(
            f"Current STRONG: {len(strong)}"
        )

        print(
            f"HARDENED: {hardened}"
        )

        print(
            "Discovery rejected: "
            f"{discovery_rejected}"
        )

        print(
            "Holdout rejected: "
            f"{holdout_rejected}"
        )

        print(
            f"Insufficient: {insufficient}"
        )

    finally:
        connection.close()


if __name__ == "__main__":
    main()
