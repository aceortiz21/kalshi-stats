from kalshi_stats.strategy_ev_predictor import (
    fit_ridge,
    predict,
)
from kalshi_stats.strategy_predictor import (
    fit_standardizer,
)


def test_ridge_expected_value_learns_direction():
    rows = []

    for index in range(40):
        signal = (
            1.0
            if index % 2 == 0
            else -1.0
        )

        rows.append(
            {
                "market_ticker": f"M{index}",
                "signal": signal,
                "profit": (
                    0.10
                    if signal > 0
                    else -0.05
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

    coefficients = fit_ridge(
        rows,
        feature_names=names,
        standardizer=standardizer,
    )

    assert (
        predict(
            {"signal": 1.0},
            feature_names=names,
            standardizer=standardizer,
            coefficients=coefficients,
        )
        >
        predict(
            {"signal": -1.0},
            feature_names=names,
            standardizer=standardizer,
            coefficients=coefficients,
        )
    )
