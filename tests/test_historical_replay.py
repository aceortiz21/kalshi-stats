from kalshi_stats.historical_replay import (
    find_band,
    side_price,
)

from kalshi_stats.strategy_zoo import (
    iter_grid_definitions,
)

from kalshi_stats.tail_zoo import (
    iter_tail_definitions,
)


def test_historical_replay_covers_700_zoo_strategies():
    assert (
        len(
            list(
                iter_grid_definitions()
            )
        )
        == 378
    )

    assert (
        len(
            list(
                iter_tail_definitions()
            )
        )
        == 322
    )


def test_no_quotes_are_reciprocal():
    row = {
        "yes_bid_close": .61,
        "yes_ask_close": .62,
    }

    assert (
        side_price(
            row,
            "yes",
            "bid",
        )
        == .61
    )

    assert (
        round(
            side_price(
                row,
                "no",
                "ask",
            ),
            8,
        )
        == .39
    )

    assert (
        round(
            side_price(
                row,
                "no",
                "bid",
            ),
            8,
        )
        == .38
    )


def test_band_lookup():
    bands = [
        (
            "A",
            .10,
            .20,
        )
    ]

    assert (
        find_band(
            .15,
            bands,
        )[0]
        == "A"
    )

    assert (
        find_band(
            .30,
            bands,
        )
        is None
    )
