from kalshi_stats.tail_zoo import (
    iter_tail_definitions,
)


def test_tail_zoo_has_322_unique_strategies():
    definitions = list(
        iter_tail_definitions()
    )

    assert len(
        definitions
    ) == 322

    assert len(
        {
            row[
                "strategy_key"
            ]
            for row
            in definitions
        }
    ) == 322


def test_tail_zoo_contains_low_and_high():
    definitions = list(
        iter_tail_definitions()
    )

    types = {
        row[
            "tail_type"
        ]
        for row
        in definitions
    }

    assert types == {
        "LOW",
        "HIGH",
    }
