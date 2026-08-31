from datetime import (
    datetime,
    timedelta,
    timezone,
)

from kalshi_stats.database import (
    connect,
    init_db,
)

from kalshi_stats.health import (
    build_data_health,
)


def iso(value):
    return (
        value
        .replace(
            microsecond=0
        )
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )


def test_high_res_count_uses_same_market_cohort():
    connection = connect(
        ":memory:"
    )

    try:
        init_db(
            connection
        )

        now = datetime.now(
            timezone.utc
        )

        recent_close = iso(
            now
            - timedelta(
                hours=1
            )
        )

        old_close = iso(
            now
            - timedelta(
                days=2
            )
        )

        collected = iso(
            now
            - timedelta(
                minutes=1
            )
        )

        connection.execute(
            """
            INSERT INTO markets (
                ticker,
                series_ticker,
                close_time,
                result
            )
            VALUES
                (
                    'RECENT',
                    'KXBTC15M',
                    ?,
                    'yes'
                ),
                (
                    'OLD',
                    'KXBTC15M',
                    ?,
                    'yes'
                )
            """,
            (
                recent_close,
                old_close,
            ),
        )

        for ticker in (
            "RECENT",
            "OLD",
        ):
            connection.execute(
                """
                INSERT INTO quote_snapshots (
                    market_ticker,
                    collected_at,
                    status,

                    yes_bid,
                    yes_ask,
                    no_bid,
                    no_ask
                )
                VALUES (
                    ?,
                    ?,
                    'closed',

                    .5,
                    .51,
                    .49,
                    .5
                )
                """,
                (
                    ticker,
                    collected,
                ),
            )

        state = build_data_health(
            connection,

            series_ticker=(
                "KXBTC15M"
            ),

            model_meta={},

            model_pending=0,
            auto_rebuild_after=96,

            pending_finalizations=0,

            current_market_ticker=None,
            ws_connected=True,

            last_event_latency_ms=100,

            model_rebuild_running=False,
        )

        assert (
            state[
                "recent_quote_markets"
            ]
            == 1
        )

        assert (
            state[
                "live_status"
            ]
            == "GOOD"
        )

    finally:
        connection.close()
