from kalshi_stats.prospective_logger import (
    aligned,
    qualifies,
    qualifying_sides,
)


def test_side_alignment():
    assert aligned(
        5.0,
        "yes",
    ) == 5.0

    assert aligned(
        5.0,
        "no",
    ) == -5.0


def test_base_strategy_boundaries():
    assert qualifies(
        ask=.60,
        seconds_remaining=300,
    )

    assert qualifies(
        ask=.69,
        seconds_remaining=599,
    )

    assert not qualifies(
        ask=.59,
        seconds_remaining=400,
    )

    assert not qualifies(
        ask=.70,
        seconds_remaining=400,
    )

    assert not qualifies(
        ask=.65,
        seconds_remaining=299,
    )

    assert not qualifies(
        ask=.65,
        seconds_remaining=600,
    )


def test_qualifying_side_uses_executable_ask():
    feature = {
        "yes_bid": .63,
        "yes_ask": .65,

        "no_bid": .34,
        "no_ask": .36,

        "seconds_remaining": 450,
    }

    assert qualifying_sides(
        feature
    ) == [
        "yes"
    ]
