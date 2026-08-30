from kalshi_stats.database import (
    connect,
    init_db,
)
from kalshi_stats.historical_features import (
    load_historical_observations,
    materialize_historical_features,
)


def _market(
    connection,
):
    connection.execute(
        """
        INSERT INTO markets (
            ticker,
            series_ticker,
            event_ticker,
            title,
            status,
            result,
            open_time,
            close_time,
            reference_price
        )
        VALUES (
            'TEST',
            'KXBTC15M',
            'EVENT',
            'test',
            'settled',
            'yes',
            '2026-01-01T00:00:00Z',
            '2026-01-01T00:15:00Z',
            100.0
        )
        """
    )


def test_historical_observation_window():
    connection = connect(
        ":memory:"
    )

    try:
        init_db(
            connection
        )

        _market(
            connection
        )

        # Candle exactly at market open must
        # not become an observation.
        for ts in (
            1767225600,
            1767225660,
        ):
            connection.execute(
                """
                INSERT INTO candles (
                    market_ticker,
                    end_period_ts,
                    period_interval,
                    source,
                    price_open,
                    price_close,
                    price_high,
                    price_low
                )
                VALUES (
                    'TEST',
                    ?,
                    1,
                    'test',
                    .50,
                    .50,
                    .50,
                    .50
                )
                """,
                (
                    ts,
                ),
            )

        rows = (
            load_historical_observations(
                connection
            )
        )

        assert len(rows) == 1
        assert (
            rows[0][
                "observed_ts"
            ]
            == 1767225660
        )

    finally:
        connection.close()


def test_materializer_excludes_same_second_btc():
    connection = connect(
        ":memory:"
    )

    try:
        init_db(
            connection
        )

        _market(
            connection
        )

        observed_ts = (
            1767225660
        )

        connection.execute(
            """
            INSERT INTO candles (
                market_ticker,
                end_period_ts,
                period_interval,
                source,
                price_open,
                price_close,
                price_high,
                price_low
            )
            VALUES (
                'TEST',
                ?,
                1,
                'test',
                .50,
                .60,
                .62,
                .58
            )
            """,
            (
                observed_ts,
            ),
        )

        # Completed second immediately
        # BEFORE the Kalshi observation.
        connection.execute(
            """
            INSERT INTO btc_1s
            VALUES (
                ?,
                'binance_1s',
                100,
                100,
                100,
                100,
                1
            )
            """,
            (
                observed_ts
                * 1000
                - 1000,
            ),
        )

        # This second begins exactly at the
        # Kalshi observation and must NOT be
        # visible to the feature calculation.
        connection.execute(
            """
            INSERT INTO btc_1s
            VALUES (
                ?,
                'binance_1s',
                999,
                999,
                999,
                999,
                1
            )
            """,
            (
                observed_ts
                * 1000,
            ),
        )

        connection.commit()

        result = (
            materialize_historical_features(
                connection
            )
        )

        assert (
            result["saved"]
            == 1
        )

        row = connection.execute(
            """
            SELECT *
            FROM historical_market_features
            WHERE market_ticker = 'TEST'
            """
        ).fetchone()

        assert row is not None

        assert (
            row["spot"]
            == 100.0
        )

        assert (
            row["btc_ts"]
            == (
                observed_ts
                * 1000
                - 1000
            )
        )

        assert (
            row["btc_age_ms"]
            == 1000
        )

    finally:
        connection.close()


def test_threshold_distance_is_materialized():
    connection = connect(
        ":memory:"
    )

    try:
        init_db(
            connection
        )

        _market(
            connection
        )

        observed_ts = (
            1767225660
        )

        connection.execute(
            """
            INSERT INTO candles (
                market_ticker,
                end_period_ts,
                period_interval,
                source,
                price_open,
                price_close,
                price_high,
                price_low
            )
            VALUES (
                'TEST',
                ?,
                1,
                'test',
                .50,
                .60,
                .62,
                .58
            )
            """,
            (
                observed_ts,
            ),
        )

        connection.execute(
            """
            INSERT INTO btc_1s
            VALUES (
                ?,
                'binance_1s',
                101,
                101,
                101,
                101,
                2
            )
            """,
            (
                observed_ts
                * 1000
                - 1000,
            ),
        )

        connection.commit()

        materialize_historical_features(
            connection
        )

        row = connection.execute(
            """
            SELECT *
            FROM historical_market_features
            WHERE market_ticker = 'TEST'
            """
        ).fetchone()

        assert row is not None

        assert (
            row[
                "threshold_distance_dollars"
            ]
            == 1.0
        )

        assert (
            round(
                row[
                    "threshold_distance_bps"
                ],
                6,
            )
            == 100.0
        )

        # Historical bar proxy should carry
        # the Binance volume into our rolling
        # volume machinery.
        assert (
            row[
                "btc_volume_60s"
            ]
            == 2.0
        )

    finally:
        connection.close()
