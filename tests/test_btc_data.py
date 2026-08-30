from kalshi_stats.btc_data import (
    _normalize_binance_timestamp_ms,
)


def test_binance_millisecond_timestamp_is_unchanged():
    assert (
        _normalize_binance_timestamp_ms(
            1782604800000
        )
        == 1782604800000
    )


def test_binance_microsecond_timestamp_becomes_milliseconds():
    assert (
        _normalize_binance_timestamp_ms(
            1782604800000000
        )
        == 1782604800000
    )
