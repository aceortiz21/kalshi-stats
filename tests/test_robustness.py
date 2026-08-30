from types import SimpleNamespace

from kalshi_stats.robustness import (
    cluster_bootstrap_mean_ci,
    stable_seed,
    usable_strategy_outcomes,
)


def _outcome(
    ticker,
    profit,
    exit_reason="TAKE_PROFIT",
):
    return SimpleNamespace(
        market_ticker=ticker,
        profit=profit,
        exit_reason=exit_reason,
    )


def test_usable_strategy_outcomes_excludes_nonusable():
    outcomes = [
        _outcome(
            "A",
            0.01,
        ),
        _outcome(
            "B",
            -0.01,
            "AMBIGUOUS",
        ),
        _outcome(
            "C",
            -0.01,
            "INELIGIBLE",
        ),
    ]

    usable = (
        usable_strategy_outcomes(
            outcomes
        )
    )

    assert len(usable) == 1
    assert usable[0].market_ticker == "A"


def test_cluster_bootstrap_positive_sample_is_positive():
    outcomes = []

    for index in range(40):
        ticker = (
            f"M{index}"
        )

        outcomes.append(
            _outcome(
                ticker,
                0.01,
            )
        )

        outcomes.append(
            _outcome(
                ticker,
                0.02,
            )
        )

    result = (
        cluster_bootstrap_mean_ci(
            outcomes,
            iterations=500,
            seed=stable_seed(
                "test"
            ),
        )
    )

    assert (
        result[
            "cluster_count"
        ]
        == 40
    )

    assert (
        result[
            "observations"
        ]
        == 80
    )

    assert (
        result["ci_low"]
        is not None
    )

    assert (
        result["ci_low"]
        > 0
    )
