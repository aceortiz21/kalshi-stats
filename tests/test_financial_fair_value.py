from kalshi_stats.financial_fair_value import (
    fit_logistic,
    horizon_z,
    predict,
)


def test_horizon_z_scales_by_time_and_volatility():
    near = horizon_z(
        distance_bps=10,
        realized_vol_60s_bps=5,
        seconds_remaining=60,
    )

    far = horizon_z(
        distance_bps=10,
        realized_vol_60s_bps=5,
        seconds_remaining=240,
    )

    assert near is not None
    assert far is not None

    assert near > far > 0


def test_logistic_fit_learns_positive_distance_direction():
    rows = []

    for index in range(20):
        rows.append(
            {
                "market_ticker": (
                    f"N{index}"
                ),
                "x": -2.0,
                "y": 0.0,
                "market_price": 0.5,
            }
        )

        rows.append(
            {
                "market_ticker": (
                    f"Y{index}"
                ),
                "x": 2.0,
                "y": 1.0,
                "market_price": 0.5,
            }
        )

    a, b = fit_logistic(
        rows
    )

    assert b > 0

    assert (
        predict(
            a,
            b,
            2.0,
        )
        >
        predict(
            a,
            b,
            -2.0,
        )
    )


def test_logistic_fit_does_not_explode_on_repeated_market_rows():
    rows = []

    for market in range(40):
        outcome = (
            1.0
            if market % 2 == 0
            else 0.0
        )

        for index in range(10):
            x = (
                1.0
                if outcome == 1.0
                else -1.0
            )

            x += (
                index - 5
            ) * 0.02

            rows.append(
                {
                    "market_ticker": (
                        f"M{market}"
                    ),
                    "x": x,
                    "y": outcome,
                    "market_price": 0.5,
                }
            )

    a, b = fit_logistic(
        rows
    )

    assert abs(a) < 10
    assert 0 < b < 10
