from __future__ import annotations

import argparse
from math import sqrt
from random import Random
from statistics import mean

from .analytics import (
    _settled_markets_with_data,
)
from .database import (
    connect,
    init_db,
)
from .financial_screen import (
    chronological_three_way_split,
)
from .strategy_predictor import (
    FINANCIAL_FEATURES,
    MARKET_FEATURES,
    build_dataset,
    fit_standardizer,
    market_weights,
    percentile,
    solve_linear_system,
    vector,
)


L2 = 5.0
BOOTSTRAP_REPS = 2000
BOOTSTRAP_SEED = 20260830


def fit_ridge(
    rows,
    *,
    feature_names,
    standardizer,
    l2=L2,
):
    dimension = (
        len(feature_names)
        + 1
    )

    matrix = [
        [
            0.0
            for _ in range(
                dimension
            )
        ]
        for _ in range(
            dimension
        )
    ]

    target = [
        0.0
        for _ in range(
            dimension
        )
    ]

    weights = market_weights(
        rows
    )

    for row, weight in zip(
        rows,
        weights,
    ):
        x = vector(
            row,
            feature_names,
            standardizer,
        )

        y = float(
            row["profit"]
        )

        for j in range(
            dimension
        ):
            target[j] += (
                weight
                * x[j]
                * y
            )

            for k in range(
                dimension
            ):
                matrix[j][k] += (
                    weight
                    * x[j]
                    * x[k]
                )

    matrix[0][0] += 1e-6

    for index in range(
        1,
        dimension,
    ):
        matrix[index][index] += (
            l2
        )

    return solve_linear_system(
        matrix,
        target,
    )


def predict(
    row,
    *,
    feature_names,
    standardizer,
    coefficients,
):
    x = vector(
        row,
        feature_names,
        standardizer,
    )

    return sum(
        coefficient
        * value
        for coefficient, value
        in zip(
            coefficients,
            x,
        )
    )


def weighted_mean(
    values,
    weights,
):
    return (
        sum(
            value * weight
            for value, weight
            in zip(
                values,
                weights,
            )
        )
        / sum(weights)
    )


def metrics(
    rows,
    predictions,
):
    weights = market_weights(
        rows
    )

    actual = [
        float(
            row["profit"]
        )
        for row in rows
    ]

    errors = [
        prediction - truth
        for prediction, truth
        in zip(
            predictions,
            actual,
        )
    ]

    mse = weighted_mean(
        [
            error * error
            for error in errors
        ],
        weights,
    )

    mae = weighted_mean(
        [
            abs(error)
            for error in errors
        ],
        weights,
    )

    return {
        "rmse": sqrt(mse),
        "mae": mae,
    }


def cluster_bootstrap_uplift(
    baseline_rows,
    selected_rows,
    *,
    seed,
):
    baseline_by_market = {}
    selected_by_market = {}

    for row in baseline_rows:
        baseline_by_market.setdefault(
            row["market_ticker"],
            [],
        ).append(
            float(
                row["profit"]
            )
        )

    for row in selected_rows:
        selected_by_market.setdefault(
            row["market_ticker"],
            [],
        ).append(
            float(
                row["profit"]
            )
        )

    markets = sorted(
        baseline_by_market
    )

    rng = Random(
        seed
    )

    uplifts = []

    for _ in range(
        BOOTSTRAP_REPS
    ):
        baseline = []
        selected = []

        for _ in markets:
            ticker = rng.choice(
                markets
            )

            baseline.extend(
                baseline_by_market[
                    ticker
                ]
            )

            selected.extend(
                selected_by_market.get(
                    ticker,
                    [],
                )
            )

        if not selected:
            continue

        uplifts.append(
            mean(selected)
            - mean(baseline)
        )

    return (
        percentile(
            uplifts,
            0.025,
        ),
        percentile(
            uplifts,
            0.975,
        ),
    )


def selection_report(
    *,
    discovery_predictions,
    validation_rows,
    validation_predictions,
):
    baseline_profit = mean(
        float(
            row["profit"]
        )
        for row
        in validation_rows
    )

    print(
        f"Validation baseline avg: "
        f"{baseline_profit * 100:+.2f}c"
    )

    for quantile in (
        0.50,
        0.75,
        0.90,
    ):
        cutoff = percentile(
            discovery_predictions,
            quantile,
        )

        selected = [
            row
            for row, prediction
            in zip(
                validation_rows,
                validation_predictions,
            )
            if prediction >= cutoff
        ]

        if not selected:
            print(
                f"Top "
                f"{100 - quantile * 100:.0f}% "
                "N=0"
            )
            continue

        avg_profit = mean(
            float(
                row["profit"]
            )
            for row in selected
        )

        win_rate = mean(
            float(
                row["y"]
            )
            for row in selected
        )

        markets = len(
            {
                row[
                    "market_ticker"
                ]
                for row
                in selected
            }
        )

        low, high = (
            cluster_bootstrap_uplift(
                validation_rows,
                selected,
                seed=(
                    BOOTSTRAP_SEED
                    + int(
                        quantile
                        * 100
                    )
                ),
            )
        )

        print(
            f"Top "
            f"{100 - quantile * 100:.0f}% "
            f"cutoff={cutoff * 100:+.2f}c "
            f"N={len(selected):<4} "
            f"markets={markets:<4} "
            f"win={win_rate * 100:5.1f}% "
            f"avg={avg_profit * 100:+.2f}c "
            f"uplift95=["
            f"{low * 100:+.2f},"
            f"{high * 100:+.2f}]c"
        )


def main():
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
        init_db(
            connection
        )

        covered = {
            str(row[0])
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

        working = (
            discovery_markets
            + validation_markets
        )

        rows = build_dataset(
            connection,
            markets=working,
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

        discovery_rows = [
            row
            for row in rows
            if row[
                "market_ticker"
            ]
            in discovery_tickers
        ]

        validation_rows = [
            row
            for row in rows
            if row[
                "market_ticker"
            ]
            in validation_tickers
        ]

        all_features = (
            MARKET_FEATURES
            + FINANCIAL_FEATURES
        )

        market_standardizer = (
            fit_standardizer(
                discovery_rows,
                MARKET_FEATURES,
            )
        )

        full_standardizer = (
            fit_standardizer(
                discovery_rows,
                all_features,
            )
        )

        market_coefficients = (
            fit_ridge(
                discovery_rows,
                feature_names=(
                    MARKET_FEATURES
                ),
                standardizer=(
                    market_standardizer
                ),
            )
        )

        full_coefficients = (
            fit_ridge(
                discovery_rows,
                feature_names=(
                    all_features
                ),
                standardizer=(
                    full_standardizer
                ),
            )
        )

        discovery_mean = mean(
            float(row["profit"])
            for row
            in discovery_rows
        )

        base_discovery = [
            discovery_mean
            for _ in discovery_rows
        ]

        base_validation = [
            discovery_mean
            for _ in validation_rows
        ]

        market_discovery = [
            predict(
                row,
                feature_names=(
                    MARKET_FEATURES
                ),
                standardizer=(
                    market_standardizer
                ),
                coefficients=(
                    market_coefficients
                ),
            )
            for row
            in discovery_rows
        ]

        market_validation = [
            predict(
                row,
                feature_names=(
                    MARKET_FEATURES
                ),
                standardizer=(
                    market_standardizer
                ),
                coefficients=(
                    market_coefficients
                ),
            )
            for row
            in validation_rows
        ]

        full_discovery = [
            predict(
                row,
                feature_names=(
                    all_features
                ),
                standardizer=(
                    full_standardizer
                ),
                coefficients=(
                    full_coefficients
                ),
            )
            for row
            in discovery_rows
        ]

        full_validation = [
            predict(
                row,
                feature_names=(
                    all_features
                ),
                standardizer=(
                    full_standardizer
                ),
                coefficients=(
                    full_coefficients
                ),
            )
            for row
            in validation_rows
        ]

        print()
        print(
            "=" * 100
        )
        print(
            "STRATEGY EXPECTED-PROFIT PREDICTOR"
        )
        print(
            "=" * 100
        )

        print(
            "Target: historical gross profit "
            "for 60-69c / 5-10m / "
            "TP +15c / SL -5c"
        )

        print(
            "Discovery rows:",
            f"{len(discovery_rows):,}",
        )

        print(
            "Validation rows:",
            f"{len(validation_rows):,}",
        )

        print(
            "LOCKED markets:",
            f"{len(locked_markets):,}",
            "(NOT EVALUATED)",
        )

        print()
        print(
            "PREDICTION ERROR"
        )

        for (
            label,
            discovery_predictions,
            validation_predictions,
        ) in (
            (
                "BASE_MEAN",
                base_discovery,
                base_validation,
            ),
            (
                "MARKET_EV",
                market_discovery,
                market_validation,
            ),
            (
                "MARKET_PLUS_FIN_EV",
                full_discovery,
                full_validation,
            ),
        ):
            d = metrics(
                discovery_rows,
                discovery_predictions,
            )

            v = metrics(
                validation_rows,
                validation_predictions,
            )

            print(
                f"{label:<20}"
                f"D RMSE={d['rmse'] * 100:.3f}c "
                f"MAE={d['mae'] * 100:.3f}c | "
                f"V RMSE={v['rmse'] * 100:.3f}c "
                f"MAE={v['mae'] * 100:.3f}c"
            )

        print()
        print(
            "MARKET-ONLY EV SELECTION"
        )

        selection_report(
            discovery_predictions=(
                market_discovery
            ),
            validation_rows=(
                validation_rows
            ),
            validation_predictions=(
                market_validation
            ),
        )

        print()
        print(
            "MARKET + FINANCIAL EV SELECTION"
        )

        selection_report(
            discovery_predictions=(
                full_discovery
            ),
            validation_rows=(
                validation_rows
            ),
            validation_predictions=(
                full_validation
            ),
        )

        print()
        print(
            "FULL MODEL COEFFICIENTS "
            "(cents per 1 SD):"
        )

        for name, coefficient in zip(
            all_features,
            full_coefficients[1:],
        ):
            print(
                f"  {name:<28}"
                f"{coefficient * 100:+.3f}c"
            )

        print()
        print(
            "Fees/slippage omitted. "
            "Final 20% remains LOCKED."
        )

    finally:
        connection.close()


if __name__ == "__main__":
    main()
