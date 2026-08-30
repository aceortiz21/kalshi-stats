from kalshi_stats.database import (
    connect,
    init_db,
)

from kalshi_stats.micro_multiplier import (
    label_micro_opportunities,
    record_micro_opportunities,
)


def insert_feature(
    connection,
    *,
    ts,
    yes_bid,
    yes_ask,
    seconds_remaining=150,
):
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
            'MICRO',
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

            yes_bid,
            yes_ask,

            1.0 - yes_ask,
            1.0 - yes_bid,
        ),
    )


def test_micro_records_low_price_target_ladder():
    connection = connect(
        ":memory:"
    )

    try:
        init_db(
            connection
        )

        insert_feature(
            connection,
            ts=1_000_000,
            yes_bid=.007,
            yes_ask=.008,
            seconds_remaining=150,
        )

        assert (
            record_micro_opportunities(
                connection,
                now_ms=1_000_000,
            )
            == 1
        )

        row = connection.execute(
            """
            SELECT *
            FROM micro_multiplier_opportunities
            """
        ).fetchone()

        assert (
            row[
                "entry_price_key"
            ]
            == 8
        )

        assert (
            row["time_bucket"]
            == "2-3m"
        )

        targets = connection.execute(
            """
            SELECT
                target_price,
                multiplier

            FROM micro_multiplier_targets

            WHERE micro_opportunity_id = ?

            ORDER BY target_price
            """,
            (
                row[
                    "micro_opportunity_id"
                ],
            ),
        ).fetchall()

        target_prices = [
            round(
                item[
                    "target_price"
                ],
                6,
            )
            for item in targets
        ]

        assert .01 in target_prices
        assert .02 in target_prices
        assert .25 in target_prices
        assert .50 in target_prices

    finally:
        connection.close()


def test_micro_target_uses_future_executable_bid():
    connection = connect(
        ":memory:"
    )

    try:
        init_db(
            connection
        )

        insert_feature(
            connection,
            ts=1_000_000,
            yes_bid=.007,
            yes_ask=.008,
            seconds_remaining=150,
        )

        record_micro_opportunities(
            connection,
            now_ms=1_000_000,
        )

        opportunity = (
            connection.execute(
                """
                SELECT *
                FROM micro_multiplier_opportunities
                """
            ).fetchone()
        )

        insert_feature(
            connection,
            ts=1_001_000,
            yes_bid=.020,
            yes_ask=.021,
            seconds_remaining=149,
        )

        changed = (
            label_micro_opportunities(
                connection
            )
        )

        assert changed > 0

        target = connection.execute(
            """
            SELECT *
            FROM micro_multiplier_targets

            WHERE micro_opportunity_id = ?
              AND ABS(
                    target_price - .02
                  ) < .0000001
            """,
            (
                opportunity[
                    "micro_opportunity_id"
                ],
            ),
        ).fetchone()

        assert (
            target["status"]
            == "HIT"
        )

        assert (
            target["hit_bid"]
            == .020
        )

    finally:
        connection.close()
