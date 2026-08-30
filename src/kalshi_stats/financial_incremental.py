from __future__ import annotations

import argparse
from math import (
    exp,
    log,
)

from .analytics import (
    _settled_markets_with_data,
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


EPSILON = 1e-5


def sigmoid(value: float) -> float:
    if value >= 0:
        z = exp(-value)
        return 1.0 / (1.0 + z)

    z = exp(value)
    return z / (1.0 + z)


def logit(probability: float) -> float:
    p = max(
        EPSILON,
        min(
            1.0 - EPSILON,
            float(probability),
        ),
    )

    return log(
        p / (1.0 - p)
    )


def load_rows(
    connection,
    tickers,
):
    tickers = list(tickers)
    output = []

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
                result,
                kalshi_price_close,
                seconds_remaining,
                threshold_distance_bps,
                realized_vol_60s_bps

            FROM historical_market_features

            WHERE market_ticker
                IN ({placeholders})

              AND seconds_remaining >= 60

            ORDER BY
                observed_ts,
                market_ticker
            """,
            chunk,
        ).fetchall()

        for row in rows:
            market_price = float(
                row[
                    "kalshi_price_close"
                ]
            )

            if not (
                0.0
                < market_price
                < 1.0
            ):
                continue

            z = horizon_z(
                distance_bps=(
                    row[
                        "threshold_distance_bps"
                    ]
                ),
                realized_vol_60s_bps=(
                    row[
                        "realized_vol_60s_bps"
                    ]
                ),
                seconds_remaining=(
                    row[
                        "seconds_remaining"
                    ]
                ),
            )

            if z is None:
                continue

            output.append(
                {
                    "market_ticker": str(
                        row[
                            "market_ticker"
                        ]
                    ),
                    "market_price": (
                        market_price
                    ),
                    "market_logit": (
                        logit(
                            market_price
                        )
                    ),
                    "z": float(z),
                    "y": (
                        1.0
                        if row["result"]
                        == "yes"
                        else 0.0
                    ),
                }
            )

    return output


def market_weights(rows):
    counts = {}

    for row in rows:
        ticker = row[
            "market_ticker"
        ]

        counts[ticker] = (
            counts.get(
                ticker,
                0,
            )
            + 1
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


def weighted_mean(
    values,
    weights,
):
    denominator = sum(weights)

    return (
        sum(
            value * weight
            for value, weight
            in zip(
                values,
                weights,
            )
        )
        / denominator
    )


def log_likelihood(
    rows,
    weights,
    *,
    intercept: float,
    financial_beta: float,
    l2: float,
):
    value = 0.0

    for row, weight in zip(
        rows,
        weights,
    ):
        linear = (
            row[
                "market_logit"
            ]
            + intercept
            + financial_beta
            * row["z"]
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
            y * log(probability)
            + (1.0 - y)
            * log(
                1.0 - probability
            )
        )

    value -= (
        0.5
        * l2
        * financial_beta
        * financial_beta
    )

    return value


def fit_offset_model(
    rows,
    *,
    allow_financial: bool,
    iterations: int = 100,
    l2: float = 1.0,
):
    """
    Fixed Kalshi logit offset:

      calibrated market:
        logit(p) = logit(market) + a

      financial challenger:
        logit(p) = logit(market) + a + b*z

    This directly asks whether financial state adds
    information beyond the market itself.
    """

    weights = market_weights(
        rows
    )

    intercept = 0.0
    beta = 0.0

    current_ll = log_likelihood(
        rows,
        weights,
        intercept=intercept,
        financial_beta=beta,
        l2=l2,
    )

    for _ in range(iterations):
        g0 = 0.0
        g1 = (
            -l2 * beta
            if allow_financial
            else 0.0
        )

        h00 = 1e-6
        h01 = 0.0
        h11 = (
            l2 + 1e-6
        )

        for row, weight in zip(
            rows,
            weights,
        ):
            z = (
                row["z"]
                if allow_financial
                else 0.0
            )

            probability = sigmoid(
                row[
                    "market_logit"
                ]
                + intercept
                + beta * z
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

            g0 += (
                weight
                * residual
            )

            g1 += (
                weight
                * residual
                * z
            )

            h00 += (
                weight
                * variance
            )

            h01 += (
                weight
                * variance
                * z
            )

            h11 += (
                weight
                * variance
                * z
                * z
            )

        if not allow_financial:
            h01 = 0.0
            g1 = 0.0

        determinant = (
            h00 * h11
            - h01 * h01
        )

        if determinant <= 1e-12:
            break

        step_intercept = (
            g0 * h11
            - g1 * h01
        ) / determinant

        step_beta = (
            h00 * g1
            - h01 * g0
        ) / determinant

        if not allow_financial:
            step_beta = 0.0

        step_intercept = max(
            -1.0,
            min(
                1.0,
                step_intercept,
            ),
        )

        step_beta = max(
            -1.0,
            min(
                1.0,
                step_beta,
            ),
        )

        scale = 1.0
        accepted = False

        for _ in range(25):
            candidate_intercept = (
                intercept
                + scale
                * step_intercept
            )

            candidate_beta = (
                beta
                + scale
                * step_beta
            )

            candidate_ll = (
                log_likelihood(
                    rows,
                    weights,
                    intercept=(
                        candidate_intercept
                    ),
                    financial_beta=(
                        candidate_beta
                    ),
                    l2=l2,
                )
            )

            if (
                candidate_ll
                >= current_ll
            ):
                intercept = (
                    candidate_intercept
                )

                beta = (
                    candidate_beta
                )

                current_ll = (
                    candidate_ll
                )

                accepted = True
                break

            scale *= 0.5

        if not accepted:
            break

        if (
            abs(
                scale
                * step_intercept
            )
            < 1e-9
            and abs(
                scale
                * step_beta
            )
            < 1e-9
        ):
            break

    return (
        intercept,
        beta,
    )


def model_probability(
    row,
    *,
    intercept,
    beta,
):
    return sigmoid(
        row["market_logit"]
        + intercept
        + beta
        * row["z"]
    )


def metrics(
    rows,
    probabilities,
):
    weights = market_weights(
        rows
    )

    outcomes = [
        row["y"]
        for row in rows
    ]

    brier = weighted_mean(
        [
            (p - y) ** 2
            for p, y
            in zip(
                probabilities,
                outcomes,
            )
        ],
        weights,
    )

    losses = []

    for p, y in zip(
        probabilities,
        outcomes,
    ):
        p = max(
            1e-9,
            min(
                1.0 - 1e-9,
                p,
            ),
        )

        losses.append(
            -(
                y * log(p)
                + (1.0 - y)
                * log(
                    1.0 - p
                )
            )
        )

    return {
        "brier": brier,
        "logloss": (
            weighted_mean(
                losses,
                weights,
            )
        ),
    }


def evaluate(
    rows,
    *,
    calibration_intercept,
    financial_intercept,
    financial_beta,
):
    raw = [
        row[
            "market_price"
        ]
        for row in rows
    ]

    calibrated = [
        model_probability(
            row,
            intercept=(
                calibration_intercept
            ),
            beta=0.0,
        )
        for row in rows
    ]

    financial = [
        model_probability(
            row,
            intercept=(
                financial_intercept
            ),
            beta=(
                financial_beta
            ),
        )
        for row in rows
    ]

    return {
        "RAW_MARKET": (
            metrics(
                rows,
                raw,
            )
        ),
        "CALIBRATED": (
            metrics(
                rows,
                calibrated,
            )
        ),
        "MARKET_PLUS_BTC": (
            metrics(
                rows,
                financial,
            )
        ),
    }


def edge_buckets(
    rows,
    *,
    intercept,
    beta,
):
    """
    Predeclared high-conviction residual thresholds.

    Gross settlement return uses historical Kalshi
    candle close as a price proxy, not executable ask.
    """

    thresholds = (
        0.05,
        0.10,
        0.15,
    )

    results = []

    for threshold in thresholds:
        profits = []
        markets = set()

        for row in rows:
            fair_yes = (
                model_probability(
                    row,
                    intercept=intercept,
                    beta=beta,
                )
            )

            market_yes = (
                row[
                    "market_price"
                ]
            )

            difference = (
                fair_yes
                - market_yes
            )

            if (
                difference
                >= threshold
            ):
                profit = (
                    row["y"]
                    - market_yes
                )

            elif (
                difference
                <= -threshold
            ):
                market_no = (
                    1.0
                    - market_yes
                )

                no_win = (
                    1.0
                    - row["y"]
                )

                profit = (
                    no_win
                    - market_no
                )

            else:
                continue

            profits.append(
                profit
            )

            markets.add(
                row[
                    "market_ticker"
                ]
            )

        results.append(
            {
                "threshold": (
                    threshold
                ),
                "observations": (
                    len(profits)
                ),
                "markets": (
                    len(markets)
                ),
                "avg_profit": (
                    sum(profits)
                    / len(profits)
                    if profits
                    else None
                ),
            }
        )

    return results


def print_metrics(
    name,
    result,
):
    print(
        f"{name:<18}"
        f"Brier={result['brier']:.5f} "
        f"LogLoss={result['logloss']:.5f}"
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

        discovery, validation, locked = (
            chronological_three_way_split(
                markets
            )
        )

        discovery_rows = load_rows(
            connection,
            {
                str(m["ticker"])
                for m in discovery
            },
        )

        validation_rows = load_rows(
            connection,
            {
                str(m["ticker"])
                for m in validation
            },
        )

        calibration_intercept, _ = (
            fit_offset_model(
                discovery_rows,
                allow_financial=False,
            )
        )

        (
            financial_intercept,
            financial_beta,
        ) = fit_offset_model(
            discovery_rows,
            allow_financial=True,
        )

        discovery_results = evaluate(
            discovery_rows,
            calibration_intercept=(
                calibration_intercept
            ),
            financial_intercept=(
                financial_intercept
            ),
            financial_beta=(
                financial_beta
            ),
        )

        validation_results = evaluate(
            validation_rows,
            calibration_intercept=(
                calibration_intercept
            ),
            financial_intercept=(
                financial_intercept
            ),
            financial_beta=(
                financial_beta
            ),
        )

        print()
        print("=" * 92)
        print(
            "INCREMENTAL FINANCIAL MODEL"
        )
        print("=" * 92)

        print(
            "Question: does BTC state add "
            "information beyond Kalshi price?"
        )

        print()
        print(
            "Calibration intercept:",
            f"{calibration_intercept:+.5f}",
        )

        print(
            "Financial model:",
            "logit(Kalshi)",
            f"{financial_intercept:+.5f}",
            f"{financial_beta:+.5f}",
            "* horizon_z",
        )

        print()
        print(
            "LOCKED test markets:",
            f"{len(locked):,}",
            "(NOT EVALUATED)",
        )

        print()
        print(
            "DISCOVERY"
        )

        for name, result in (
            discovery_results.items()
        ):
            print_metrics(
                name,
                result,
            )

        print()
        print(
            "VALIDATION"
        )

        for name, result in (
            validation_results.items()
        ):
            print_metrics(
                name,
                result,
            )

        print()
        raw = validation_results[
            "RAW_MARKET"
        ]

        calibrated = validation_results[
            "CALIBRATED"
        ]

        financial = validation_results[
            "MARKET_PLUS_BTC"
        ]

        print(
            "VALIDATION IMPROVEMENT VS RAW"
        )

        print(
            "Calibration Brier:",
            f"{raw['brier'] - calibrated['brier']:+.6f}",
        )

        print(
            "BTC Brier:",
            f"{raw['brier'] - financial['brier']:+.6f}",
        )

        print(
            "Calibration LogLoss:",
            f"{raw['logloss'] - calibrated['logloss']:+.6f}",
        )

        print(
            "BTC LogLoss:",
            f"{raw['logloss'] - financial['logloss']:+.6f}",
        )

        print()
        print(
            "VALIDATION HIGH-CONVICTION "
            "RESIDUALS"
        )

        for row in edge_buckets(
            validation_rows,
            intercept=(
                financial_intercept
            ),
            beta=(
                financial_beta
            ),
        ):
            avg = (
                "-"
                if row[
                    "avg_profit"
                ]
                is None
                else (
                    f"{row['avg_profit'] * 100:+.2f}c"
                )
            )

            print(
                f">= {row['threshold'] * 100:.0f}pp "
                f"N={row['observations']:<5} "
                f"markets={row['markets']:<5} "
                f"gross avg={avg}"
            )

        print()
        print(
            "Historical candle close is only "
            "a price proxy. Fees/slippage omitted."
        )

        print(
            "Final 20% remains LOCKED."
        )

    finally:
        connection.close()


if __name__ == "__main__":
    main()
