from kalshi_stats.database import (
    connect,
    init_db,
)
from kalshi_stats.market_sync import (
    build_market_feature_snapshot,
    insert_market_feature_snapshot,
    latest_btc_features,
)


def _btc():
    return {
        "ts": 1_000_000,
        "spot": 101.0,
        "spread_bps": 0.2,
        "return_30s": 0.001,
        "return_60s": 0.002,
        "return_180s": 0.003,
        "return_300s": 0.004,
        "ema_5": 100.8,
        "ema_9": 100.7,
        "ema_21": 100.5,
        "ema_5_9_bps": 1.0,
        "ema_9_21_bps": 2.0,
        "ema_5_slope_bps": 0.1,
        "ema_9_slope_bps": 0.2,
        "ema_21_slope_bps": 0.3,
        "vwap_60s": 100.5,
        "vwap_300s": 100.2,
        "vwap_distance_60s_bps": 49.7,
        "vwap_distance_300s_bps": 79.8,
        "realized_vol_60s_bps": 5.0,
        "realized_vol_300s_bps": 10.0,
        "range_60s_bps": 8.0,
        "range_300s_bps": 20.0,
        "trade_volume_60s": 10.0,
        "trade_volume_300s": 40.0,
        "relative_volume_60s": 1.2,
        "trade_imbalance_60s": 0.3,
        "trade_imbalance_300s": 0.1,
        "book_imbalance_top10": -0.2,
    }


def test_threshold_distance_and_vol_normalization():
    market = {
        "ticker": "TEST",
        "reference_price": 100.0,
        "close_time": (
            "1970-01-01T00:18:20Z"
        ),
    }

    quote = {
        "collected_at": (
            "1970-01-01T00:16:40Z"
        ),
        "yes_bid": 0.55,
        "yes_ask": 0.56,
        "no_bid": 0.44,
        "no_ask": 0.45,
    }

    row = (
        build_market_feature_snapshot(
            market=market,
            quote=quote,
            btc=_btc(),
            now_ms=1_000_000,
        )
    )

    assert (
        row[
            "threshold_distance_dollars"
        ]
        == 1.0
    )

    assert abs(
        row[
            "threshold_distance_pct"
        ]
        - 0.01
    ) < 1e-12

    assert abs(
        row[
            "threshold_distance_bps"
        ]
        - 100.0
    ) < 1e-12

    assert abs(
        row[
            "threshold_distance_vol60"
        ]
        - 20.0
    ) < 1e-12

    assert (
        row[
            "seconds_remaining"
        ]
        == 100.0
    )


def test_latest_btc_features_rejects_stale_data():
    connection = connect(
        ":memory:"
    )

    try:
        init_db(
            connection
        )

        connection.execute(
            """
            INSERT INTO btc_feature_snapshots (
                ts,
                source,
                spot
            )
            VALUES (
                1000,
                'coinbase_ws',
                100.0
            )
            """
        )

        connection.commit()

        assert (
            latest_btc_features(
                connection,
                now_ms=5001,
                max_age_ms=3000,
            )
            is None
        )

        assert (
            latest_btc_features(
                connection,
                now_ms=3000,
                max_age_ms=3000,
            )
            is not None
        )

    finally:
        connection.close()


def test_synchronized_snapshot_round_trip():
    connection = connect(
        ":memory:"
    )

    try:
        init_db(
            connection
        )

        market = {
            "ticker": "TEST",
            "reference_price": 100.0,
            "close_time": (
                "1970-01-01T00:18:20Z"
            ),
        }

        quote = {
            "collected_at": (
                "1970-01-01T00:16:40Z"
            ),
            "yes_bid": 0.55,
            "yes_ask": 0.56,
            "no_bid": 0.44,
            "no_ask": 0.45,
        }

        row = (
            build_market_feature_snapshot(
                market=market,
                quote=quote,
                btc=_btc(),
                now_ms=1_000_000,
            )
        )

        insert_market_feature_snapshot(
            connection,
            row,
        )

        connection.commit()

        stored = (
            connection.execute(
                """
                SELECT *
                FROM market_feature_snapshots
                WHERE market_ticker = 'TEST'
                """
            ).fetchone()
        )

        assert stored is not None

        assert (
            stored["spot"]
            == 101.0
        )

        assert (
            stored["threshold"]
            == 100.0
        )

        assert (
            stored["yes_ask"]
            == 0.56
        )

        assert (
            stored[
                "book_imbalance_top10"
            ]
            == -0.2
        )

    finally:
        connection.close()
