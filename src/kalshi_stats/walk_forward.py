from __future__ import annotations

import argparse

from .analytics import (
    _build_series_map,
    _settled_markets_with_data,
    build_strategy_entries,
    build_validated_strategies,
    build_walk_forward_strategies,
)
from .database import (
    connect,
    init_db,
)


def _cents(
    value,
) -> str:
    if value is None:
        return "-"

    return f"{value * 100:+.2f}c"


def _ci(
    summary,
) -> str:
    if (
        summary.profit_ci_low is None
        or summary.profit_ci_high
        is None
    ):
        return "[-, -]"

    return (
        "["
        f"{summary.profit_ci_low * 100:+.2f}, "
        f"{summary.profit_ci_high * 100:+.2f}"
        "]"
    )


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--db",
        required=True,
    )

    parser.add_argument(
        "--folds",
        type=int,
        default=5,
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

        walk = (
            build_walk_forward_strategies(
                connection,
                settled_markets=markets,
                series_map=series_map,
                strategy_entries=entries,
                fold_count=args.folds,
                initial_train_fraction=(
                    0.50
                ),
                min_train_n=500,
                min_test_n=50,
                ambiguity_mode=(
                    "conservative"
                ),
            )
        )

        walk_map = {
            (
                result.price_bucket,
                result.time_bucket,
                result.strategy.id,
            ): result
            for result in walk
        }

        strong = [
            result
            for result in current
            if (
                result.validation_status
                == "STRONG"
            )
        ]

        print()
        print(
            "CURRENT STRONG STRATEGIES "
            "WITH WALK-FORWARD VALIDATION"
        )
        print("=" * 120)

        robust = 0
        mixed = 0
        unstable = 0
        insufficient = 0

        for index, result in enumerate(
            strong,
            start=1,
        ):
            key = (
                result.price_bucket,
                result.time_bucket,
                result.strategy.id,
            )

            wf = walk_map.get(
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
                "    80/20 holdout | "
                f"N="
                f"{result.holdout_summary.observations:<5} "
                f"avg="
                f"{_cents(result.holdout_summary.avg_profit)} "
                f"CI="
                f"{_ci(result.holdout_summary)}"
            )

            if wf is None:
                print(
                    "    Walk-forward   | "
                    "INSUFFICIENT — never "
                    "qualified in a fold"
                )
                insufficient += 1
                continue

            if (
                wf.persistence_status
                == "ROBUST"
            ):
                robust += 1
            elif (
                wf.persistence_status
                == "MIXED"
            ):
                mixed += 1
            elif (
                wf.persistence_status
                == "UNSTABLE"
            ):
                unstable += 1
            else:
                insufficient += 1

            print(
                "    Walk-forward   | "
                f"{wf.persistence_status} | "
                f"qualified="
                f"{wf.qualified_folds}/"
                f"{wf.total_folds} | "
                f"evaluated="
                f"{wf.evaluated_folds}/"
                f"{wf.total_folds} | "
                f"positive="
                f"{wf.positive_folds}/"
                f"{wf.evaluated_folds} | "
                f"strong-folds="
                f"{wf.strong_folds}"
            )

            print(
                "    Aggregate OOS  | "
                f"N="
                f"{wf.aggregate_oos_summary.observations:<5} "
                f"avg="
                f"{_cents(wf.aggregate_oos_summary.avg_profit)} "
                f"CI="
                f"{_ci(wf.aggregate_oos_summary)} | "
                f"worst="
                f"{_cents(wf.worst_fold_avg_profit)}"
            )

            fold_parts = []

            fold_lookup = {
                fold.fold_index: fold
                for fold in wf.folds
            }

            for fold_index in range(
                1,
                wf.total_folds + 1,
            ):
                fold = fold_lookup.get(
                    fold_index
                )

                if fold is None:
                    fold_parts.append(
                        f"F{fold_index}=not-qualified"
                    )
                    continue

                fold_parts.append(
                    f"F{fold_index}="
                    f"{_cents(fold.test_summary.avg_profit)}"
                    f"(N={fold.test_summary.observations})"
                )

            print(
                "    Folds          | "
                + " | ".join(
                    fold_parts
                )
            )

        print()
        print("=" * 120)
        print(
            f"Current STRONG: {len(strong)}"
        )
        print(
            f"Walk-forward ROBUST: {robust}"
        )
        print(
            f"Walk-forward MIXED: {mixed}"
        )
        print(
            f"Walk-forward UNSTABLE: {unstable}"
        )
        print(
            "Walk-forward INSUFFICIENT: "
            f"{insufficient}"
        )

    finally:
        connection.close()


if __name__ == "__main__":
    main()
