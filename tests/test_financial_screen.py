from kalshi_stats.financial_screen import (
    chronological_three_way_split,
    confluence,
    ema_stack,
    momentum_five_minute,
    target_one_vol,
    target_side,
    vwap_five_minute,
)


def _row():
    return {
        "threshold_distance_bps": 2.0,
        "threshold_distance_vol60": 1.5,
        "return_300s": 0.01,
        "ema_5m": 105.0,
        "ema_9m": 103.0,
        "ema_21m": 100.0,
        "vwap_distance_300s_bps": 3.0,
    }


def test_financial_conditions_are_side_aligned():
    row = _row()

    assert target_side(row, "yes")
    assert target_one_vol(row, "yes")
    assert ema_stack(row, "yes")
    assert vwap_five_minute(row, "yes")
    assert momentum_five_minute(
        row,
        "yes",
    )
    assert confluence(row, "yes")

    assert not target_side(
        row,
        "no",
    )

    assert not target_one_vol(
        row,
        "no",
    )

    assert not ema_stack(
        row,
        "no",
    )

    assert not vwap_five_minute(
        row,
        "no",
    )

    assert not momentum_five_minute(
        row,
        "no",
    )

    assert not confluence(
        row,
        "no",
    )


def test_three_way_split_locks_final_twenty_percent():
    markets = [
        {
            "ticker": str(index),
            "close_time": (
                f"2026-01-{index + 1:02d}"
            ),
        }
        for index in range(10)
    ]

    discovery, validation, locked = (
        chronological_three_way_split(
            markets
        )
    )

    assert len(discovery) == 6
    assert len(validation) == 2
    assert len(locked) == 2

    assert discovery[-1][
        "ticker"
    ] == "5"

    assert validation[-1][
        "ticker"
    ] == "7"

    assert locked[0][
        "ticker"
    ] == "8"
