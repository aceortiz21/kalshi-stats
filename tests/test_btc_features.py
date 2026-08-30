from kalshi_stats.btc_features import (
    BTCFeatureEngine,
    BTCSecond,
)
from kalshi_stats.btc_live import (
    CoinbaseBTCState,
    _parse_time_ms,
)


def test_coinbase_nanosecond_timestamp_parser():
    value = _parse_time_ms(
        "2026-08-30T17:45:12.123456789Z"
    )

    assert (
        value
        == 1788111912123
    )


def test_market_trade_maker_side_is_inverted_for_aggressor():
    state = CoinbaseBTCState()

    state.handle_message(
        {
            "channel": "market_trades",
            "events": [
                {
                    "trades": [
                        {
                            "trade_id": "1",
                            "product_id": "BTC-USD",
                            "price": "100000",
                            "size": "2",
                            "side": "SELL",
                            "time": (
                                "2026-08-30T17:45:12Z"
                            ),
                        },
                        {
                            "trade_id": "2",
                            "product_id": "BTC-USD",
                            "price": "100001",
                            "size": "1",
                            "side": "BUY",
                            "time": (
                                "2026-08-30T17:45:13Z"
                            ),
                        },
                    ]
                }
            ],
        }
    )

    trades = list(
        state.feature_engine.trades
    )

    assert trades[0].aggressor == "buy"
    assert trades[1].aggressor == "sell"


def test_level2_absolute_quantity_updates_book():
    state = CoinbaseBTCState()

    state.handle_message(
        {
            "channel": "l2_data",
            "events": [
                {
                    "type": "snapshot",
                    "product_id": "BTC-USD",
                    "updates": [
                        {
                            "side": "bid",
                            "price_level": "100",
                            "new_quantity": "2",
                        },
                        {
                            "side": "offer",
                            "price_level": "101",
                            "new_quantity": "3",
                        },
                    ],
                }
            ],
        }
    )

    assert state.best_bid() == 100.0
    assert state.best_ask() == 101.0

    state.handle_message(
        {
            "channel": "l2_data",
            "events": [
                {
                    "type": "update",
                    "product_id": "BTC-USD",
                    "updates": [
                        {
                            "side": "bid",
                            "price_level": "100",
                            "new_quantity": "0",
                        }
                    ],
                }
            ],
        }
    )

    assert 100.0 not in state.bids


def test_feature_engine_returns_ema_and_flow():
    engine = BTCFeatureEngine()

    start = 1_000_000

    for index in range(70):
        price = (
            100.0
            + index * 0.1
        )

        ts = (
            start
            + index * 1000
        )

        engine.add_trade(
            ts_ms=ts,
            price=price,
            size=1.0,
            aggressor=(
                "buy"
                if index % 2 == 0
                else "sell"
            ),
        )

        engine.add_second(
            BTCSecond(
                ts_ms=ts,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=1.0,
            )
        )

    snapshot = engine.snapshot(
        ts_ms=(
            start + 69_000
        ),
        spot=106.9,
        best_bid=106.8,
        best_ask=107.0,
        book_imbalance_top10=0.25,
    )

    assert snapshot["return_30s"] is not None
    assert snapshot["return_60s"] is not None

    assert snapshot["ema_5"] is not None
    assert snapshot["ema_9"] is not None
    assert snapshot["ema_21"] is not None

    assert snapshot["vwap_60s"] is not None
    assert snapshot["trade_volume_60s"] > 0

    assert (
        snapshot[
            "book_imbalance_top10"
        ]
        == 0.25
    )
