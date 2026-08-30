from kalshi_stats.financial_incremental import (
    fit_offset_model,
    logit,
    model_probability,
)


def test_logit_round_trip_direction():
    assert logit(0.8) > 0
    assert logit(0.2) < 0


def test_financial_offset_can_learn_residual_signal():
    rows = []

    for index in range(80):
        z = (
            1.0
            if index % 2 == 0
            else -1.0
        )

        y = (
            1.0
            if z > 0
            else 0.0
        )

        rows.append(
            {
                "market_ticker": f"M{index}",
                "market_price": 0.5,
                "market_logit": 0.0,
                "z": z,
                "y": y,
            }
        )

    intercept, beta = (
        fit_offset_model(
            rows,
            allow_financial=True,
        )
    )

    assert beta > 0

    bullish = {
        **rows[0],
        "z": 1.0,
    }

    bearish = {
        **rows[0],
        "z": -1.0,
    }

    assert (
        model_probability(
            bullish,
            intercept=intercept,
            beta=beta,
        )
        >
        model_probability(
            bearish,
            intercept=intercept,
            beta=beta,
        )
    )
