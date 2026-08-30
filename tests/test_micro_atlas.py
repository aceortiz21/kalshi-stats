from kalshi_stats.database import (
    connect,
    init_db,
)

from kalshi_stats.micro_atlas import (
    lookup_micro_atlas,
)


def test_micro_atlas_live_lookup():
    connection = connect(
        ":memory:"
    )

    try:
        init_db(
            connection
        )

        connection.execute(
            """
            INSERT INTO
            micro_multiplier_atlas (
                entry_price_key,
                time_bucket,
                target_price_key,

                entry_price,
                target_price,
                multiplier,

                observations,
                unique_markets,
                hits,

                touch_rate,
                ci_low,
                ci_high,

                break_even_touch,
                conservative_edge,

                limit_only_ev,
                limit_only_roi,

                source_market_count,
                generated_at_ms
            )
            VALUES (
                8,
                '2-3m',
                200,

                .008,
                .20,
                25.0,

                87,
                87,
                7,

                .08046,
                .039,
                .158,

                .04,
                -.001,

                .00809,
                1.011,
                4000,
                1
            )
            """
        )

        rows = lookup_micro_atlas(
            connection,
            entry_ask=.008,
            seconds_remaining=150,
        )

        assert len(rows) == 1

        assert (
            rows[0][
                "entry_price_key"
            ]
            == 8
        )

        assert (
            rows[0][
                "time_bucket"
            ]
            == "2-3m"
        )

        assert (
            rows[0][
                "target_price_key"
            ]
            == 200
        )

    finally:
        connection.close()


def test_live_micro_requires_real_prospective_sample():
    from kalshi_stats.micro_atlas import (
        build_live_micro_state,
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
            INSERT INTO micro_multiplier_atlas (
                entry_price_key,
                time_bucket,
                target_price_key,

                entry_price,
                target_price,
                multiplier,

                observations,
                unique_markets,
                hits,

                touch_rate,
                ci_low,
                ci_high,

                break_even_touch,
                conservative_edge,

                limit_only_ev,
                limit_only_roi,

                source_market_count,
                generated_at_ms
            )
            VALUES (
                1,
                '3-4m',
                2,

                .001,
                .002,
                2.0,

                99,
                99,
                84,

                .848,
                .765,
                .906,

                .50,
                .265,

                .000697,
                .697,

                4717,
                1
            )
            """
        )

        state = build_live_micro_state(
            connection,
            market_ticker="CURRENT",
            side="no",
            entry_ask=.001,
            seconds_remaining=200,
        )

        assert (
            state["rows"][0]["status"]
            == "HISTORICAL MICRO LEAD"
        )

        # 40/50 prospective executable-bid hits.
        for index in range(50):
            cursor = connection.execute(
                """
                INSERT INTO
                micro_multiplier_opportunities (
                    market_ticker,
                    side,

                    detected_at_ms,
                    market_feature_ts,

                    entry_price_key,

                    entry_bid,
                    entry_ask,

                    seconds_remaining,
                    time_bucket,

                    label_status,
                    path_complete
                )
                VALUES (
                    ?,
                    'no',

                    ?,
                    ?,

                    1,

                    0,
                    .001,

                    200,
                    '3-4m',

                    'COMPLETE',
                    1
                )
                """,
                (
                    f"TEST{index}",
                    1000 + index,
                    1000 + index,
                ),
            )

            connection.execute(
                """
                INSERT INTO
                micro_multiplier_targets (
                    micro_opportunity_id,
                    target_price,
                    multiplier,
                    status
                )
                VALUES (
                    ?,
                    .002,
                    2.0,
                    ?
                )
                """,
                (
                    cursor.lastrowid,
                    (
                        "HIT"
                        if index < 40
                        else "MISS"
                    ),
                ),
            )

        state = build_live_micro_state(
            connection,
            market_ticker="CURRENT",
            side="no",
            entry_ask=.001,
            seconds_remaining=200,
        )

        row = state["rows"][0]

        assert (
            row["live_completed"]
            == 50
        )

        assert (
            row["live_hits"]
            == 40
        )

        assert (
            row["status"]
            == "LIVE-VALIDATED MICRO"
        )

    finally:
        connection.close()
