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
