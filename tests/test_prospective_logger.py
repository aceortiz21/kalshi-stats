from kalshi_stats.prospective_logger import (
    aligned,
    qualifies,
    qualifying_sides,
)


def test_side_alignment():
    assert aligned(
        5.0,
        "yes",
    ) == 5.0

    assert aligned(
        5.0,
        "no",
    ) == -5.0


def test_base_strategy_boundaries():
    assert qualifies(
        ask=.60,
        seconds_remaining=300,
    )

    assert qualifies(
        ask=.69,
        seconds_remaining=599,
    )

    assert not qualifies(
        ask=.59,
        seconds_remaining=400,
    )

    assert not qualifies(
        ask=.70,
        seconds_remaining=400,
    )

    assert not qualifies(
        ask=.65,
        seconds_remaining=299,
    )

    assert not qualifies(
        ask=.65,
        seconds_remaining=600,
    )


def test_qualifying_side_uses_executable_ask():
    feature = {
        "yes_bid": .63,
        "yes_ask": .65,

        "no_bid": .34,
        "no_ask": .36,

        "seconds_remaining": 450,
    }

    assert qualifying_sides(
        feature
    ) == [
        "yes"
    ]


def _insert_feature(
    connection,
    *,
    ts,
    yes_bid,
    yes_ask,
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

            400,

            ?,
            ?,
            ?,
            ?
        )
        """,
        (
            ts,
            ts,

            yes_bid,
            yes_ask,

            1.0 - yes_ask,
            1.0 - yes_bid,
        ),
    )


def _insert_opportunity(
    connection,
    *,
    ts=1_000_000,
):
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
            spot
        )
        VALUES (
            'TEST_STRATEGY',
            'TEST',
            'yes',

            ?,
            ?,

            .64,
            .65,
            400,

            100000,
            100000
        )
        """,
        (
            ts,
            ts,
        ),
    )


def test_outcome_labeler_uses_future_executable_bid():
    from kalshi_stats.database import (
        connect,
        init_db,
    )

    from kalshi_stats.prospective_logger import (
        label_pending_opportunities,
    )

    connection = connect(
        ":memory:"
    )

    try:
        init_db(
            connection
        )

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

        _insert_opportunity(
            connection
        )

        _insert_feature(
            connection,
            ts=1_001_000,
            yes_bid=.70,
            yes_ask=.71,
        )

        _insert_feature(
            connection,
            ts=1_002_000,
            yes_bid=.80,
            yes_ask=.81,
        )

        count = (
            label_pending_opportunities(
                connection
            )
        )

        assert count == 1

        row = connection.execute(
            """
            SELECT *
            FROM prospective_opportunities
            """
        ).fetchone()

        assert (
            row["label_status"]
            == "LABELED"
        )

        assert (
            row["first_hit"]
            == "TP"
        )

        assert (
            row["tp_hit"]
            == 1
        )

        assert round(
            row[
                "gross_profit_per_contract"
            ],
            4,
        ) == .15

    finally:
        connection.close()


def test_outcome_labeler_rejects_path_gap_before_hit():
    from kalshi_stats.database import (
        connect,
        init_db,
    )

    from kalshi_stats.prospective_logger import (
        label_pending_opportunities,
    )

    connection = connect(
        ":memory:"
    )

    try:
        init_db(
            connection
        )

        connection.execute(
            """
            INSERT INTO markets (
                ticker,
                series_ticker,
                result,
                close_time
            )
            VALUES (
                'TEST',
                'KXBTC15M',
                'yes',
                '1970-01-01T00:16:47Z'
            )
            """
        )

        _insert_opportunity(
            connection
        )

        # Seven-second hole before an apparent TP.
        # We cannot know whether SL happened first.
        _insert_feature(
            connection,
            ts=1_007_000,
            yes_bid=.80,
            yes_ask=.81,
        )

        count = (
            label_pending_opportunities(
                connection
            )
        )

        assert count == 1

        row = connection.execute(
            """
            SELECT *
            FROM prospective_opportunities
            """
        ).fetchone()

        assert (
            row["label_status"]
            == "INCOMPLETE"
        )

        assert (
            row["first_hit"]
            is None
        )

    finally:
        connection.close()


def test_reentry_after_sustained_exit_creates_new_episode():
    from kalshi_stats.database import (
        connect,
        init_db,
    )

    from kalshi_stats.prospective_logger import (
        record_opportunities,
    )

    connection = connect(
        ":memory:"
    )

    try:
        init_db(
            connection
        )

        # Episode 1 begins.
        _insert_feature(
            connection,
            ts=1_000_000,
            yes_bid=.64,
            yes_ask=.65,
        )

        assert (
            record_opportunities(
                connection,
                now_ms=1_000_000,
            )
            == 1
        )

        # Still inside the same episode.
        _insert_feature(
            connection,
            ts=1_001_000,
            yes_bid=.65,
            yes_ask=.66,
        )

        assert (
            record_opportunities(
                connection,
                now_ms=1_001_000,
            )
            == 0
        )

        # Leave the setup.
        _insert_feature(
            connection,
            ts=1_002_000,
            yes_bid=.54,
            yes_ask=.55,
        )

        record_opportunities(
            connection,
            now_ms=1_002_000,
        )

        # Remain outside for >10 seconds.
        _insert_feature(
            connection,
            ts=1_013_000,
            yes_bid=.54,
            yes_ask=.55,
        )

        record_opportunities(
            connection,
            now_ms=1_013_000,
        )

        # Re-enter: this is episode 2.
        _insert_feature(
            connection,
            ts=1_014_000,
            yes_bid=.63,
            yes_ask=.64,
        )

        assert (
            record_opportunities(
                connection,
                now_ms=1_014_000,
            )
            == 1
        )

        rows = connection.execute(
            """
            SELECT
                episode_number,
                episode_start_ms,
                episode_end_ms

            FROM prospective_opportunities

            WHERE market_ticker = 'TEST'
              AND side = 'yes'

            ORDER BY episode_number
            """
        ).fetchall()

        assert len(rows) == 2

        assert (
            rows[0][
                "episode_number"
            ]
            == 1
        )

        assert (
            rows[0][
                "episode_end_ms"
            ]
            == 1_002_000
        )

        assert (
            rows[1][
                "episode_number"
            ]
            == 2
        )

        assert (
            rows[1][
                "episode_start_ms"
            ]
            == 1_014_000
        )

    finally:
        connection.close()
