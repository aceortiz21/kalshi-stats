from kalshi_stats.strategy_predictor import (
    fit_logistic,
    fit_standardizer,
    predict,
    side_value,
)


def test_side_value_flips_direction_for_no():
    assert side_value(
        3.0,
        "yes",
    ) == 3.0

    assert side_value(
        3.0,
        "no",
    ) == -3.0


def test_multivariate_logistic_learns_signal():
    rows = []

    for index in range(80):
        x = (
            1.0
            if index % 2 == 0
            else -1.0
        )

        rows.append(
            {
                "market_ticker": (
                    f"M{index}"
                ),
                "signal": x,
                "y": (
                    1.0
                    if x > 0
                    else 0.0
                ),
            }
        )

    names = (
        "signal",
    )

    standardizer = (
        fit_standardizer(
            rows,
            names,
        )
    )

    coefficients = (
        fit_logistic(
            rows,
            feature_names=names,
            standardizer=(
                standardizer
            ),
        )
    )

    bullish = {
        "signal": 1.0,
    }

    bearish = {
        "signal": -1.0,
    }

    assert (
        predict(
            bullish,
            feature_names=names,
            standardizer=(
                standardizer
            ),
            coefficients=(
                coefficients
            ),
        )
        >
        predict(
            bearish,
            feature_names=names,
            standardizer=(
                standardizer
            ),
            coefficients=(
                coefficients
            ),
        )
    )
