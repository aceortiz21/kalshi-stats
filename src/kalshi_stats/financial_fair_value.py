from __future__ import annotations

import argparse
from collections import Counter
from math import (
    exp,
    log,
    sqrt,
)
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


MIN_SECONDS_REMAINING = 60
EDGE_THRESHOLD = 0.10


def sigmoid(value: float) -> float:
    if value >= 0:
        z = exp(-value)
        return 1.0 / (1.0 + z)

    z = exp(value)
    return z / (1.0 + z)


def horizon_z(
    *,
    distance_bps,
    realized_vol_60s_bps,
    seconds_remaining,
):
    """
    Standardized distance from the Kalshi target.

    Positive = BTC above YES threshold.
    Negative = BTC below YES threshold.

    Remaining-time scaling reflects that the same
    distance is less decisive when more time remains.
    """

    if (
        distance_bps is None
        or realized_vol_60s_bps is None
        or seconds_remaining is None
    ):
        return None

    rv = float(
        realized_vol_60s_bps
    )

    seconds = int(
        seconds_remaining
    )

    if (
        rv <= 0
        or seconds
        < MIN_SECONDS_REMAINING
    ):
        return None

    horizon_scale = sqrt(
        seconds / 60.0
    )

    value = (
        float(distance_bps)
        / (
            rv
            * horizon_scale
        )
    )

    # Prevent a few tiny-volatility observations
    # from dominating the logistic fit.
    return max(
        -8.0,
        min(
            8.0,
            value,
        ),
    )


def load_rows(
    connection,
    tickers,
):
    tickers = list(
        tickers
    )

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

              AND seconds_remaining >= ?

            ORDER BY
                observed_ts,
                market_ticker
            """,
            [
                *chunk,
                MIN_SECONDS_REMAINING,
            ],
        ).fetchall()

        for row in rows:
            x = horizon_z(
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

            if x is None:
                continue

            price = float(
                row[
                    "kalshi_price_close"
                ]
            )

            if not (
                0.0
                < price
                < 1.0
            ):
                continue

            output.append(
                {
                    "market_ticker": str(
                        row[
                            "market_ticker"
                        ]
                    ),
                    "x": x,
                    "y": (
                        1.0
                        if row["result"]
                        == "yes"
                        else 0.0
                    ),
                    "market_price": (
                        price
                    ),
                }
            )

    return output


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


def _weighted_log_likelihood(
    rows,
    weights,
    *,
    a: float,
    b: float,
    l2: float,
) -> float:
    value = 0.0

    for row, weight in zip(
        rows,
        weights,
    ):
        x = float(
            row["x"]
        )

        y = float(
            row["y"]
        )

        p = sigmoid(
            a + b * x
        )

        p = max(
            1e-12,
            min(
                1.0 - 1e-12,
                p,
            ),
        )

        value += weight * (
            y * log(p)
            + (1.0 - y)
            * log(1.0 - p)
        )

    # Do not penalize the intercept.
    value -= (
        0.5
        * l2
        * b
        * b
    )

    return value


def fit_logistic(
    rows,
    *,
    iterations: int = 100,
    l2: float = 1.0,
):
    """
    Fit:

        P(YES) = sigmoid(a + b*x)

    using market-balanced weights, L2 regularization on
    the slope, damped Newton updates, and a likelihood
    line search.

    The damping/line-search combination prevents the
    coefficient explosion that ordinary Newton updates
    can suffer under quasi-separation.
    """

    if not rows:
        raise ValueError(
            "Cannot fit logistic model with no rows"
        )

    weights = market_weights(
        rows
    )

    # Initialize intercept from the market-balanced
    # empirical YES frequency instead of starting at 0.
    yes_rate = weighted_mean(
        [
            float(
                row["y"]
            )
            for row in rows
        ],
        weights,
    )

    yes_rate = max(
        1e-6,
        min(
            1.0 - 1e-6,
            float(yes_rate),
        ),
    )

    a = log(
        yes_rate
        / (1.0 - yes_rate)
    )

    b = 0.0

    current_ll = (
        _weighted_log_likelihood(
            rows,
            weights,
            a=a,
            b=b,
            l2=l2,
        )
    )

    for _ in range(
        iterations
    ):
        g0 = 0.0
        g1 = (
            -l2 * b
        )

        h00 = 0.0
        h01 = 0.0
        h11 = l2

        for row, weight in zip(
            rows,
            weights,
        ):
            x = float(
                row["x"]
            )

            y = float(
                row["y"]
            )

            p = sigmoid(
                a + b * x
            )

            residual = (
                y - p
            )

            variance = max(
                1e-9,
                p * (1.0 - p),
            )

            g0 += (
                weight
                * residual
            )

            g1 += (
                weight
                * residual
                * x
            )

            h00 += (
                weight
                * variance
            )

            h01 += (
                weight
                * variance
                * x
            )

            h11 += (
                weight
                * variance
                * x
                * x
            )

        # Small intercept damping for numerical safety.
        h00 += 1e-6

        determinant = (
            h00 * h11
            - h01 * h01
        )

        if (
            determinant <= 1e-12
        ):
            break

        step_a = (
            g0 * h11
            - g1 * h01
        ) / determinant

        step_b = (
            h00 * g1
            - h01 * g0
        ) / determinant

        # Additional hard guard. A legitimate two-parameter
        # calibration should never need an enormous single
        # iteration.
        step_a = max(
            -2.0,
            min(
                2.0,
                step_a,
            ),
        )

        step_b = max(
            -2.0,
            min(
                2.0,
                step_b,
            ),
        )

        scale = 1.0
        accepted = False

        for _ in range(25):
            candidate_a = (
                a
                + scale
                * step_a
            )

            candidate_b = (
                b
                + scale
                * step_b
            )

            candidate_ll = (
                _weighted_log_likelihood(
                    rows,
                    weights,
                    a=candidate_a,
                    b=candidate_b,
                    l2=l2,
                )
            )

            if (
                candidate_ll
                >= current_ll
            ):
                a = candidate_a
                b = candidate_b
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
                scale * step_a
            )
            < 1e-8
            and abs(
                scale * step_b
            )
            < 1e-8
        ):
            break

    if (
        not (
            abs(a) < 50.0
            and abs(b) < 50.0
        )
    ):
        raise RuntimeError(
            "Logistic calibration diverged: "
            f"a={a}, b={b}"
        )

    return (
        a,
        b,
    )


def predict(
    a,
    b,
    x,
):
    return sigmoid(
        a + b * x
    )


def weighted_mean(
    values,
    weights,
):
    denominator = sum(
        weights
    )

    if denominator <= 0:
        return None

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


def metrics(
    rows,
    *,
    a,
    b,
):
    weights = (
        market_weights(
            rows
        )
    )

    model_probs = [
        predict(
            a,
            b,
            row["x"],
        )
        for row in rows
    ]

    market_probs = [
        row[
            "market_price"
        ]
        for row in rows
    ]

    outcomes = [
        row["y"]
        for row in rows
    ]

    model_brier = weighted_mean(
        [
            (p - y) ** 2
            for p, y in zip(
                model_probs,
                outcomes,
            )
        ],
        weights,
    )

    market_brier = weighted_mean(
        [
            (p - y) ** 2
            for p, y in zip(
                market_probs,
                outcomes,
            )
        ],
        weights,
    )

    def log_loss(
        probabilities,
    ):
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
                    + (
                        1.0 - y
                    )
                    * log(
                        1.0 - p
                    )
                )
            )

        return weighted_mean(
            losses,
            weights,
        )

    return {
        "rows": len(rows),
        "markets": len(
            {
                row[
                    "market_ticker"
                ]
                for row in rows
            }
        ),
        "model_brier": (
            model_brier
        ),
        "market_brier": (
            market_brier
        ),
        "model_logloss": (
            log_loss(
                model_probs
            )
        ),
        "market_logloss": (
            log_loss(
                market_probs
            )
        ),
    }


def edge_summary(
    rows,
    *,
    a,
    b,
    threshold=EDGE_THRESHOLD,
):
    yes = []
    no = []

    for row in rows:
        fair_yes = predict(
            a,
            b,
            row["x"],
        )

        market_yes = (
            row[
                "market_price"
            ]
        )

        yes_edge = (
            fair_yes
            - market_yes
        )

        no_edge = (
            market_yes
            - fair_yes
        )

        if yes_edge >= threshold:
            yes.append(
                1.0
                if row["y"] == 1.0
                else 0.0
            )

        elif no_edge >= threshold:
            no.append(
                1.0
                if row["y"] == 0.0
                else 0.0
            )

    return {
        "yes_n": len(yes),
        "yes_win": (
            mean(yes)
            if yes
            else None
        ),
        "no_n": len(no),
        "no_win": (
            mean(no)
            if no
            else None
        ),
        "combined_n": (
            len(yes)
            + len(no)
        ),
        "combined_win": (
            mean(
                yes + no
            )
            if (
                yes
                or no
            )
            else None
        ),
    }


def print_metrics(
    name,
    result,
):
    print(
        f"{name:<12}"
        f" rows={result['rows']:<7,}"
        f" markets={result['markets']:<5,}"
        f" | Brier model="
        f"{result['model_brier']:.5f}"
        f" market="
        f"{result['market_brier']:.5f}"
        f" | LogLoss model="
        f"{result['model_logloss']:.5f}"
        f" market="
        f"{result['market_logloss']:.5f}"
    )


def pct(
    value,
):
    if value is None:
        return "-"

    return (
        f"{value * 100:.1f}%"
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

        (
            discovery_markets,
            validation_markets,
            locked_markets,
        ) = chronological_three_way_split(
            markets
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

        discovery = load_rows(
            connection,
            discovery_tickers,
        )

        validation = load_rows(
            connection,
            validation_tickers,
        )

        a, b = fit_logistic(
            discovery
        )

        discovery_metrics = metrics(
            discovery,
            a=a,
            b=b,
        )

        validation_metrics = metrics(
            validation,
            a=a,
            b=b,
        )

        validation_edge = (
            edge_summary(
                validation,
                a=a,
                b=b,
            )
        )

        print()
        print(
            "=" * 100
        )

        print(
            "FINANCIAL FAIR-VALUE MODEL"
        )

        print(
            "=" * 100
        )

        print(
            "Model:"
        )

        print(
            "P(YES) = sigmoid("
            f"{a:+.4f} "
            f"{b:+.4f} * horizon_z)"
        )

        print()
        print(
            "horizon_z = "
            "target distance bps / "
            "(realized vol 60s * sqrt(minutes remaining))"
        )

        print()
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
            "OUT-OF-SAMPLE PROBABILITY QUALITY"
        )

        print_metrics(
            "Discovery",
            discovery_metrics,
        )

        print_metrics(
            "Validation",
            validation_metrics,
        )

        print()
        print(
            "VALIDATION DISCREPANCIES "
            f"(financial edge >= "
            f"{EDGE_THRESHOLD * 100:.0f}pp)"
        )

        print(
            "YES:",
            f"N={validation_edge['yes_n']}",
            "settlement win=",
            pct(
                validation_edge[
                    "yes_win"
                ]
            ),
        )

        print(
            "NO: ",
            f"N={validation_edge['no_n']}",
            "settlement win=",
            pct(
                validation_edge[
                    "no_win"
                ]
            ),
        )

        print(
            "ALL:",
            f"N={validation_edge['combined_n']}",
            "settlement win=",
            pct(
                validation_edge[
                    "combined_win"
                ]
            ),
        )

        print()
        print(
            "IMPORTANT: Kalshi candle close is "
            "a historical price proxy, not an "
            "executable ask. Fees/slippage omitted."
        )

        print(
            "The final 20% remains LOCKED."
        )

    finally:
        connection.close()


if __name__ == "__main__":
    main()
