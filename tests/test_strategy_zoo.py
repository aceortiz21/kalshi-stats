from kalshi_stats.strategy_zoo import (
    iter_grid_definitions,
    price_band_for,
    time_band_for,
)


def test_strategy_zoo_has_378_unique_rules():
    definitions = list(
        iter_grid_definitions()
    )

    assert len(
        definitions
    ) == 378

    keys = {
        row[
            "strategy_key"
        ]
        for row
        in definitions
    }

    assert len(
        keys
    ) == 378


def test_price_band_lookup():
    assert (
        price_band_for(
            .35
        )[0]
        == "35-44"
    )

    assert (
        price_band_for(
            .949
        )[0]
        == "85-94"
    )

    assert (
        price_band_for(
            .02
        )
        is None
    )


def test_time_band_lookup():
    assert (
        time_band_for(
            300
        )[0]
        == "4-6m"
    )

    assert (
        time_band_for(
            700
        )[0]
        == "10-12m"
    )
