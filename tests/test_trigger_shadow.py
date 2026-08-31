from kalshi_stats.database import (
    connect,
    init_db,
)

from kalshi_stats.trigger_shadow import (
    STRATEGY_ID,
    run_once,
    shadow_count_for_price,
)


def insert_feature(
    connection,
    *,
    ts,
    ask,
    seconds_remaining,
):
    bid = max(
        0.0,
        ask - .01,
    )

    connection.execute(
        """
        INSERT INTO market_feature_snapshots (
            market_ticker,
            ts,

            btc_ts,
            btc_age_ms,

            threshold,
            threshold_rule,

            spot,

            threshold_distance_dollars,
            threshold_distance_pct,
            threshold_distance_bps,

            seconds_remaining,

            yes_bid,
            yes_ask,
            no_bid,
            no_ask
        )
        VALUES (
            'TEST',
            ?,

            ?,
            0,

            100000,
            'greater_or_equal',

            100000,

            0,
            0,
            0,

            ?,

            ?,
            ?,
            ?,
            ?
        )
        """,
        (
            ts,
            ts,

            seconds_remaining,

            bid,
            ask,

            1.0 - ask,
            1.0 - bid,
        ),
    )


def insert_opportunity(
    connection,
):
    connection.execute(
        """
        INSERT INTO markets (
            ticker,
            series_ticker
        )
        VALUES (
            'TEST',
            'KXBTC15M'
        )
        """
    )

    connection.execute(
        """
        INSERT INTO prospective_opportunities (
            strategy_id,
            market_ticker,
            side,

            detected_at_ms,
            market_feature_ts,

            entry_bid,
            entry_ask,
            seconds_remaining,

            threshold,
            spot,

            episode_number,
            episode_start_ms
        )
        VALUES (
            ?,
            'TEST',
            'yes',

            1000000,
            1000000,

            .60,
            .61,
            500,

            100000,
            100000,

            1,
            1000000
        )
        """,
        (
            STRATEGY_ID,
        ),
    )


def test_one_cent_shadow_uses_fractional_contracts():
    count_fp, notional = (
        shadow_count_for_price(
            .61
        )
    )

    assert count_fp == "0.01"

    assert notional <= .01

    assert round(
        notional,
        6,
    ) == .0061


def test_bounce_can_pass_occupancy_without_stable_3s():
    connection = connect(
        ":memory:"
    )

    try:
        init_db(
            connection
        )

        insert_opportunity(
            connection
        )

        prices = [
            .61,
            .61,
            .59,
            .61,
            .61,
            .61,
        ]

        for index, ask in enumerate(
            prices
        ):
            insert_feature(
                connection,
                ts=(
                    1_000_000
                    + index
                    * 1000
                ),
                ask=ask,
                seconds_remaining=(
                    500 - index
                ),
            )

        run_once(
            connection,
            now_ms=1_005_000,
        )

        rows = {
            row[
                "profile_id"
            ]:
            row

            for row
            in connection.execute(
                """
                SELECT *
                FROM main_trigger_confirmations
                """
            ).fetchall()
        }

        assert (
            rows[
                "RAW"
            ][
                "status"
            ]
            == "CONFIRMED"
        )

        assert (
            rows[
                "OCC_4_OF_5S"
            ][
                "status"
            ]
            == "CONFIRMED"
        )

        assert (
            rows[
                "STABLE_3S"
            ][
                "status"
            ]
            == "WAITING"
        )

        shadow_count = (
            connection.execute(
                """
                SELECT COUNT(*)
                FROM shadow_execution_intents
                """
            ).fetchone()[0]
        )

        assert shadow_count == 2

    finally:
        connection.close()


def test_stable_3s_confirms_after_clean_run():
    connection = connect(
        ":memory:"
    )

    try:
        init_db(
            connection
        )

        insert_opportunity(
            connection
        )

        prices = [
            .61,
            .61,
            .59,
            .61,
            .61,
            .61,
            .61,
        ]

        for index, ask in enumerate(
            prices
        ):
            insert_feature(
                connection,
                ts=(
                    1_000_000
                    + index
                    * 1000
                ),
                ask=ask,
                seconds_remaining=(
                    500 - index
                ),
            )

        run_once(
            connection,
            now_ms=1_006_000,
        )

        row = connection.execute(
            """
            SELECT *
            FROM main_trigger_confirmations

            WHERE profile_id = 'STABLE_3S'
            """
        ).fetchone()

        assert (
            row["status"]
            == "CONFIRMED"
        )

        assert (
            row["entry_ask"]
            == .61
        )

        assert (
            row[
                "confirmed_at_ms"
            ]
            == 1_006_000
        )

    finally:
        connection.close()
