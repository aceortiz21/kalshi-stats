from __future__ import annotations

import argparse
from collections import Counter
from math import (
    exp,
    log,
    sqrt,
)

from .analytics import (
    _build_series_map,
    _settled_markets_with_data,
    build_strategy_entries,
)
from .database import (
    connect,
    init_db,
)
from .financial_fair_value import (
    horizon_z,
)
from .financial_screen import (
    chronological_three_way_split,
)
from .strategies import (
    DEFAULT_EXIT_STRATEGIES,
    simulate_strategy_entries,
)


PRICE_BUCKET = "60-69c"
TIME_BUCKET = "5-10m left"
STRATEGY_ID = "tp15_sl5"

MARKET_FEATURES = (
    "entry_price",
    "minutes_remaining",
)

FINANCIAL_FEATURES = (
    "target_horizon_z",
    "return_60s",
    "return_300s",
    "ema_5m_9m_bps",
    "ema_9m_21m_bps",
    "vwap_distance_300s_bps",
    "realized_vol_60s_bps",
)


def sigmoid(value):
    if value >= 0:
        z = exp(-value)
        return 1.0 / (1.0 + z)

    z = exp(value)
    return z / (1.0 + z)


def strategy():
    for item in DEFAULT_EXIT_STRATEGIES:
        if item.id == STRATEGY_ID:
            return item

    raise RuntimeError(
        f"Missing strategy {STRATEGY_ID}"
    )


def load_feature_map(
    connection,
    tickers,
):
    output = {}
    tickers = list(tickers)

    for start in range(
        0,
        len(tickers),
        500,
    ):
        chunk = tickers[
            start:start + 500
        ]

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
                realized_vol_60s_bps,

                return_60s,
                return_300s,

                ema_5m_9m_bps,
                ema_9m_21m_bps,

                vwap_distance_300s_bps

            FROM historical_market_features

            WHERE market_ticker
                IN ({placeholders})
            """,
            chunk,
        ).fetchall()

        for row in rows:
            output[
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

    return output


def side_value(
    value,
    side,
):
    if value is None:
        return None

    number = float(value)

    return (
        number
        if side == "yes"
        else -number
    )


def build_model_row(
    *,
    entry,
    outcome,
    feature,
):
    z = horizon_z(
        distance_bps=(
            feature[
                "threshold_distance_bps"
            ]
        ),
        realized_vol_60s_bps=(
            feature[
                "realized_vol_60s_bps"
            ]
        ),
        seconds_remaining=(
            entry.seconds_remaining
        ),
    )

    if z is None:
        return None

    values = {
        "entry_price": float(
            entry.entry_price
        ),

        "minutes_remaining": (
            float(
                entry.seconds_remaining
            )
            / 60.0
        ),

        "target_horizon_z": (
            side_value(
                z,
                entry.side,
            )
        ),

        "return_60s": side_value(
            feature[
                "return_60s"
            ],
            entry.side,
        ),

        "return_300s": side_value(
            feature[
                "return_300s"
            ],
            entry.side,
        ),

        "ema_5m_9m_bps": (
            side_value(
                feature[
                    "ema_5m_9m_bps"
                ],
                entry.side,
            )
        ),

        "ema_9m_21m_bps": (
            side_value(
                feature[
                    "ema_9m_21m_bps"
                ],
                entry.side,
            )
        ),

        "vwap_distance_300s_bps": (
            side_value(
                feature[
                    "vwap_distance_300s_bps"
                ],
                entry.side,
            )
        ),

        "realized_vol_60s_bps": (
            None
            if feature[
                "realized_vol_60s_bps"
            ]
            is None
            else float(
                feature[
                    "realized_vol_60s_bps"
                ]
            )
        ),
    }

    if any(
        value is None
        for value in values.values()
    ):
        return None

    return {
        "market_ticker": (
            entry.market_ticker
        ),
        "side": entry.side,
        "entry_ts": entry.entry_ts,

        "profit": float(
            outcome.profit
        ),

        "y": (
            1.0
            if outcome.profit > 0
            else 0.0
        ),

        **values,
    }


def build_dataset(
    connection,
    *,
    markets,
):
    tickers = {
        str(
            market["ticker"]
        )
        for market in markets
    }

    series_map = _build_series_map(
        connection,
        markets,
    )

    entries, _ = (
        build_strategy_entries(
            connection,
            settled_markets=markets,
            series_map=series_map,
        )
    )

    entries = [
        entry
        for entry in entries
        if (
            entry.price_bucket
            == PRICE_BUCKET
            and entry.time_bucket
            == TIME_BUCKET
        )
    ]

    outcomes = (
        simulate_strategy_entries(
            strategy=strategy(),
            entries=entries,
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
        for outcome in outcomes
        if outcome.exit_reason
        not in {
            "AMBIGUOUS",
            "INELIGIBLE",
        }
    }

    feature_map = load_feature_map(
        connection,
        tickers,
    )

    rows = []

    for entry in entries:
        outcome = outcome_map.get(
            (
                entry.market_ticker,
                entry.side,
                entry.entry_ts,
            )
        )

        feature = feature_map.get(
            (
                entry.market_ticker,
                entry.entry_ts,
            )
        )

        if (
            outcome is None
            or feature is None
        ):
            continue

        row = build_model_row(
            entry=entry,
            outcome=outcome,
            feature=feature,
        )

        if row is not None:
            rows.append(row)

    return rows


def market_weights(rows):
    counts = Counter(
        row[
            "market_ticker"
        ]
        for row in rows
    )

    return [
        1.0
        / counts[
            row[
                "market_ticker"
            ]
        ]
        for row in rows
    ]


def fit_standardizer(
    rows,
    feature_names,
):
    result = {}

    for name in feature_names:
        values = [
            float(row[name])
            for row in rows
        ]

        center = (
            sum(values)
            / len(values)
        )

        variance = (
            sum(
                (
                    value
                    - center
                ) ** 2
                for value in values
            )
            / len(values)
        )

        scale = sqrt(
            variance
        )

        if scale < 1e-12:
            scale = 1.0

        result[name] = (
            center,
            scale,
        )

    return result


def vector(
    row,
    feature_names,
    standardizer,
):
    return [
        1.0,
        *[
            (
                float(row[name])
                - standardizer[
                    name
                ][0]
            )
            / standardizer[
                name
            ][1]
            for name in feature_names
        ],
    ]


def solve_linear_system(
    matrix,
    values,
):
    n = len(values)

    augmented = [
        list(matrix[index])
        + [
            float(values[index])
        ]
        for index in range(n)
    ]

    for column in range(n):
        pivot = max(
            range(
                column,
                n,
            ),
            key=lambda row: abs(
                augmented[row][column]
            ),
        )

        if abs(
            augmented[
                pivot
            ][column]
        ) < 1e-12:
            raise RuntimeError(
                "Singular logistic Hessian"
            )

        if pivot != column:
            augmented[
                column
            ], augmented[
                pivot
            ] = (
                augmented[pivot],
                augmented[column],
            )

        pivot_value = (
            augmented[
                column
            ][column]
        )

        for index in range(
            column,
            n + 1,
        ):
            augmented[
                column
            ][index] /= (
                pivot_value
            )

        for row in range(n):
            if row == column:
                continue

            factor = (
                augmented[
                    row
                ][column]
            )

            for index in range(
                column,
                n + 1,
            ):
                augmented[
                    row
                ][index] -= (
                    factor
                    * augmented[
                        column
                    ][index]
                )

    return [
        augmented[index][n]
        for index in range(n)
    ]


def log_likelihood(
    rows,
    *,
    feature_names,
    standardizer,
    coefficients,
    l2,
):
    weights = market_weights(
        rows
    )

    value = 0.0

    for row, weight in zip(
        rows,
        weights,
    ):
        x = vector(
            row,
            feature_names,
            standardizer,
        )

        linear = sum(
            coefficient
            * feature
            for coefficient, feature
            in zip(
                coefficients,
                x,
            )
        )

        probability = sigmoid(
            linear
        )

        probability = max(
            1e-12,
            min(
                1.0 - 1e-12,
                probability,
            ),
        )

        y = row["y"]

        value += weight * (
            y * log(
                probability
            )
            + (
                1.0 - y
            )
            * log(
                1.0 - probability
            )
        )

    value -= (
        0.5
        * l2
        * sum(
            coefficient
            * coefficient
            for coefficient
            in coefficients[1:]
        )
    )

    return value


def fit_logistic(
    rows,
    *,
    feature_names,
    standardizer,
    l2=2.0,
    iterations=100,
):
    dimension = (
        len(feature_names)
        + 1
    )

    weights = market_weights(
        rows
    )

    yes_rate = (
        sum(
            weight
            * row["y"]
            for row, weight
            in zip(
                rows,
                weights,
            )
        )
        / sum(weights)
    )

    yes_rate = max(
        1e-6,
        min(
            1.0 - 1e-6,
            yes_rate,
        ),
    )

    coefficients = [
        log(
            yes_rate
            / (
                1.0
                - yes_rate
            )
        ),
        *(
            [0.0]
            * len(feature_names)
        ),
    ]

    current_ll = (
        log_likelihood(
            rows,
            feature_names=(
                feature_names
            ),
            standardizer=(
                standardizer
            ),
            coefficients=(
                coefficients
            ),
            l2=l2,
        )
    )

    for _ in range(iterations):
        gradient = [
            0.0
            for _ in range(
                dimension
            )
        ]

        hessian = [
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

        for row, weight in zip(
            rows,
            weights,
        ):
            x = vector(
                row,
                feature_names,
                standardizer,
            )

            linear = sum(
                coefficient
                * feature
                for coefficient, feature
                in zip(
                    coefficients,
                    x,
                )
            )

            probability = sigmoid(
                linear
            )

            residual = (
                row["y"]
                - probability
            )

            variance = max(
                1e-9,
                probability
                * (
                    1.0
                    - probability
                ),
            )

            for j in range(
                dimension
            ):
                gradient[j] += (
                    weight
                    * residual
                    * x[j]
                )

                for k in range(
                    dimension
                ):
                    hessian[j][k] += (
                        weight
                        * variance
                        * x[j]
                        * x[k]
                    )

        hessian[0][0] += 1e-6

        for j in range(
            1,
            dimension,
        ):
            gradient[j] -= (
                l2
                * coefficients[j]
            )

            hessian[j][j] += (
                l2
            )

        step = solve_linear_system(
            hessian,
            gradient,
        )

        step = [
            max(
                -1.0,
                min(
                    1.0,
                    value,
                ),
            )
            for value in step
        ]

        scale = 1.0
        accepted = False

        for _ in range(25):
            candidate = [
                current
                + scale
                * delta
                for current, delta
                in zip(
                    coefficients,
                    step,
                )
            ]

            candidate_ll = (
                log_likelihood(
                    rows,
                    feature_names=(
                        feature_names
                    ),
                    standardizer=(
                        standardizer
                    ),
                    coefficients=(
                        candidate
                    ),
                    l2=l2,
                )
            )

            if (
                candidate_ll
                >= current_ll
            ):
                coefficients = (
                    candidate
                )

                current_ll = (
                    candidate_ll
                )

                accepted = True
                break

            scale *= 0.5

        if not accepted:
            break

        if max(
            abs(
                scale * value
            )
            for value in step
        ) < 1e-8:
            break

    return coefficients


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

    return sigmoid(
        sum(
            coefficient
            * feature
            for coefficient, feature
            in zip(
                coefficients,
                x,
            )
        )
    )


def metrics(
    rows,
    probabilities,
):
    weights = market_weights(
        rows
    )

    denominator = sum(weights)

    brier = (
        sum(
            weight
            * (
                probability
                - row["y"]
            ) ** 2
            for row, probability, weight
            in zip(
                rows,
                probabilities,
                weights,
            )
        )
        / denominator
    )

    losses = []

    for row, probability in zip(
        rows,
        probabilities,
    ):
        probability = max(
            1e-9,
            min(
                1.0 - 1e-9,
                probability,
            ),
        )

        losses.append(
            -(
                row["y"]
                * log(
                    probability
                )
                + (
                    1.0
                    - row["y"]
                )
                * log(
                    1.0
                    - probability
                )
            )
        )

    logloss = (
        sum(
            weight * loss
            for weight, loss
            in zip(
                weights,
                losses,
            )
        )
        / denominator
    )

    return {
        "brier": brier,
        "logloss": logloss,
    }


def percentile(
    values,
    probability,
):
    ordered = sorted(values)

    if not ordered:
        return None

    position = (
        (len(ordered) - 1)
        * probability
    )

    low = int(position)

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


def selection_report(
    *,
    discovery_rows,
    validation_rows,
    discovery_probabilities,
    validation_probabilities,
):
    for quantile in (
        0.50,
        0.75,
        0.90,
    ):
        cutoff = percentile(
            discovery_probabilities,
            quantile,
        )

        selected = [
            (
                row,
                probability,
            )
            for row, probability
            in zip(
                validation_rows,
                validation_probabilities,
            )
            if probability >= cutoff
        ]

        if not selected:
            print(
                f"Top {100 - quantile * 100:.0f}% "
                "N=0"
            )
            continue

        rows = [
            item[0]
            for item in selected
        ]

        avg_profit = (
            sum(
                row["profit"]
                for row in rows
            )
            / len(rows)
        )

        win_rate = (
            sum(
                row["y"]
                for row in rows
            )
            / len(rows)
        )

        markets = len(
            {
                row[
                    "market_ticker"
                ]
                for row in rows
            }
        )

        print(
            f"Top {100 - quantile * 100:.0f}% "
            f"cutoff={cutoff:.3f} "
            f"N={len(rows):<4} "
            f"markets={markets:<4} "
            f"win={win_rate * 100:5.1f}% "
            f"avg={avg_profit * 100:+.2f}c"
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
        init_db(connection)

        covered = {
            str(row[0])
            for row in connection.execute(
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

        discovery_markets, (
            validation_markets
        ), locked_markets = (
            chronological_three_way_split(
                markets
            )
        )

        working_markets = (
            discovery_markets
            + validation_markets
        )

        rows = build_dataset(
            connection,
            markets=working_markets,
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
            fit_logistic(
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
            fit_logistic(
                discovery_rows,
                feature_names=(
                    all_features
                ),
                standardizer=(
                    full_standardizer
                ),
            )
        )

        discovery_rate = (
            sum(
                row["y"]
                for row
                in discovery_rows
            )
            / len(
                discovery_rows
            )
        )

        base_discovery = [
            discovery_rate
            for _ in discovery_rows
        ]

        base_validation = [
            discovery_rate
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
        print("=" * 96)
        print(
            "STRATEGY-SPECIFIC FINANCIAL PREDICTOR"
        )
        print("=" * 96)

        print(
            "Trade:",
            PRICE_BUCKET,
            "/",
            TIME_BUCKET,
            "/ TP +15c / SL -5c",
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
            "PROBABILITY QUALITY"
        )

        for label, d_probs, v_probs in (
            (
                "BASE_RATE",
                base_discovery,
                base_validation,
            ),
            (
                "MARKET_CONTEXT",
                market_discovery,
                market_validation,
            ),
            (
                "MARKET_PLUS_FIN",
                full_discovery,
                full_validation,
            ),
        ):
            d_metric = metrics(
                discovery_rows,
                d_probs,
            )

            v_metric = metrics(
                validation_rows,
                v_probs,
            )

            print(
                f"{label:<18}"
                f"D Brier={d_metric['brier']:.5f} "
                f"LL={d_metric['logloss']:.5f} | "
                f"V Brier={v_metric['brier']:.5f} "
                f"LL={v_metric['logloss']:.5f}"
            )

        print()
        print(
            "MARKET CONTEXT — VALIDATION SELECTION"
        )

        selection_report(
            discovery_rows=(
                discovery_rows
            ),
            validation_rows=(
                validation_rows
            ),
            discovery_probabilities=(
                market_discovery
            ),
            validation_probabilities=(
                market_validation
            ),
        )

        print()
        print(
            "MARKET + FINANCIAL — VALIDATION SELECTION"
        )

        selection_report(
            discovery_rows=(
                discovery_rows
            ),
            validation_rows=(
                validation_rows
            ),
            discovery_probabilities=(
                full_discovery
            ),
            validation_probabilities=(
                full_validation
            ),
        )

        print()
        print(
            "Financial coefficients "
            "(standardized predictors):"
        )

        for name, coefficient in zip(
            all_features,
            full_coefficients[1:],
        ):
            print(
                f"  {name:<28}"
                f"{coefficient:+.5f}"
            )

        print()
        print(
            "Gross TP/SL simulation only; "
            "fees/slippage omitted."
        )

        print(
            "Final 20% remains LOCKED."
        )

    finally:
        connection.close()


if __name__ == "__main__":
    main()
