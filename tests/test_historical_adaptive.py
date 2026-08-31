from kalshi_stats.historical_adaptive import (
    Evidence,
    SELECTORS,
    score_candidate,
)


def fake_trade(
    roi,
    market,
):
    return {
        "roi":
            roi,

        "market_ticker":
            market,
    }


def test_evidence_only_contains_added_results():
    evidence = Evidence()

    for index in range(
        20
    ):
        evidence.add(
            fake_trade(
                .10,
                f"M{index}",
            )
        )

    assert evidence.n == 20

    assert (
        len(
            evidence.markets
        )
        == 20
    )

    assert round(
        evidence.mean,
        8,
    ) == .10

    assert round(
        evidence.recent_mean,
        8,
    ) == .10


def test_fast_selector_can_qualify_positive_history():
    evidence = Evidence()

    for index in range(
        20
    ):
        evidence.add(
            fake_trade(
                .05,
                f"M{index}",
            )
        )

    result = score_candidate(
        evidence,
        SELECTORS[
            "FAST"
        ],
    )

    assert result is not None

    assert result[
        "n"
    ] == 20


def test_strict_requires_more_evidence():
    evidence = Evidence()

    for index in range(
        20
    ):
        evidence.add(
            fake_trade(
                .05,
                f"M{index}",
            )
        )

    assert (
        score_candidate(
            evidence,
            SELECTORS[
                "STRICT"
            ],
        )
        is None
    )
