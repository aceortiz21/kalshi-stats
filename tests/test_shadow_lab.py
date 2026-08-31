from kalshi_stats.shadow_lab import (
    score_events,
    strategy_status,
)


def test_shadow_lab_scores_normalized_returns():
    events = [
        {
            "ts": 1,
            "market_ticker": "A",
            "roi": .50,
        },
        {
            "ts": 2,
            "market_ticker": "B",
            "roi": -.25,
        },
        {
            "ts": 3,
            "market_ticker": "C",
            "roi": .10,
        },
    ]

    score = score_events(
        events
    )

    assert score["sample_n"] == 3
    assert score["unique_markets"] == 3

    assert score["wins"] == 2
    assert score["losses"] == 1

    assert round(
        score[
            "cumulative_pnl_per_1"
        ],
        6,
    ) == .35

    assert round(
        score[
            "avg_roi"
        ],
        6,
    ) == round(
        .35 / 3,
        6,
    )

    assert round(
        score[
            "max_drawdown_per_1"
        ],
        6,
    ) == .25


def test_shadow_lab_status_does_not_promote_tiny_samples():
    assert (
        strategy_status(
            sample_n=10,
            avg_roi=5.0,
            recent_20_avg_roi=5.0,
        )
        == "BUILDING"
    )

    assert (
        strategy_status(
            sample_n=30,
            avg_roi=.10,
            recent_20_avg_roi=.10,
        )
        == "WATCH"
    )

    assert (
        strategy_status(
            sample_n=60,
            avg_roi=.10,
            recent_20_avg_roi=-.02,
        )
        == "COOLING"
    )

    assert (
        strategy_status(
            sample_n=60,
            avg_roi=.10,
            recent_20_avg_roi=.05,
        )
        == "PROMISING"
    )
