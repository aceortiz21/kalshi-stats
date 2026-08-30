from types import SimpleNamespace

from kalshi_stats.transition_research import (
    competing_barrier_result,
)


def _observation(
    *,
    ts,
    low,
    high,
):
    return SimpleNamespace(
        observed_ts=ts,
        yes_low=low,
        yes_high=high,
        source="candle",
    )


def _occurrence(
    future,
    *,
    eventual_win=False,
):
    return {
        "entry_price": .35,
        "entry_ts": 0,
        "seconds_remaining": 300,
        "side": "yes",
        "future": future,
        "eventual_win": eventual_win,
    }


def test_competing_barrier_upper_first():
    occurrence = _occurrence(
        [
            _observation(
                ts=60,
                low=.33,
                high=.41,
            )
        ]
    )

    assert (
        competing_barrier_result(
            occurrence,
            up_delta=.05,
            down_delta=.05,
        )
        == "UPPER"
    )


def test_competing_barrier_same_candle_is_ambiguous():
    occurrence = _occurrence(
        [
            _observation(
                ts=60,
                low=.29,
                high=.41,
            )
        ]
    )

    assert (
        competing_barrier_result(
            occurrence,
            up_delta=.05,
            down_delta=.05,
        )
        == "AMBIGUOUS"
    )


def test_competing_barrier_uses_settlement_terminal_price():
    occurrence = _occurrence(
        [],
        eventual_win=False,
    )

    assert (
        competing_barrier_result(
            occurrence,
            up_delta=.15,
            down_delta=.35,
            horizon_seconds=None,
        )
        == "LOWER"
    )
