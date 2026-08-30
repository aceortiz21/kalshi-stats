import json

from kalshi_stats.account_sync import (
    canonical_outcome_side,
    reconstruct_market_pnl,
)
from kalshi_stats.database import (
    connect,
    init_db,
)


def test_canonical_outcome_direction():
    assert canonical_outcome_side(
        {
            "side": "yes",
            "action": "buy",
            "raw_json": "{}",
        }
    ) == "yes"

    assert canonical_outcome_side(
        {
            "side": "no",
            "action": "sell",
            "raw_json": "{}",
        }
    ) == "yes"

    assert canonical_outcome_side(
        {
            "side": "no",
            "action": "buy",
            "raw_json": "{}",
        }
    ) == "no"

    assert canonical_outcome_side(
        {
            "side": "yes",
            "action": "sell",
            "raw_json": "{}",
        }
    ) == "no"


def test_outcome_side_overrides_legacy_fields():
    fill = {
        "side": "yes",
        "action": "buy",

        "raw_json": json.dumps(
            {
                "outcome_side": "no",
            }
        ),
    }

    assert (
        canonical_outcome_side(
            fill
        )
        == "no"
    )


def test_opposite_outcomes_close_at_one_dollar():
    connection = connect(
        ":memory:"
    )

    try:
        init_db(
            connection
        )

        connection.execute(
            """
            INSERT INTO account_fills (
                fill_id,
                market_ticker,
                side,
                action,
                count,
                yes_price,
                no_price,
                fee_cost,
                created_time,
                ts,
                raw_json
            )
            VALUES
                (
                    'F1',
                    'TEST',
                    'yes',
                    'buy',
                    10,
                    .60,
                    .40,
                    .10,
                    '2026-01-01T00:00:00Z',
                    1,
                    '{"outcome_side":"yes"}'
                ),
                (
                    'F2',
                    'TEST',
                    'yes',
                    'sell',
                    10,
                    .75,
                    .25,
                    .10,
                    '2026-01-01T00:01:00Z',
                    2,
                    '{"outcome_side":"no"}'
                )
            """
        )

        connection.execute(
            """
            INSERT INTO account_settlements (
                market_ticker,
                settled_time,
                market_result,
                revenue_cents,
                fee_cost
            )
            VALUES (
                'TEST',
                '2026-01-01T00:15:00Z',
                'yes',
                0,
                .20
            )
            """
        )

        fills = connection.execute(
            """
            SELECT *
            FROM account_fills
            WHERE market_ticker = 'TEST'
            """
        ).fetchall()

        settlement = (
            connection.execute(
                """
                SELECT *
                FROM account_settlements
                WHERE market_ticker = 'TEST'
                """
            ).fetchone()
        )

        result = (
            reconstruct_market_pnl(
                fills,
                settlement,
            )
        )

        # Buy YES for $6.
        # Exit by acquiring NO for $2.50.
        # Pair pays $10.
        # Gross profit $1.50.
        # Fees $0.20.
        assert round(
            result["pnl"],
            2,
        ) == 1.30

        assert round(
            result[
                "paired_payout"
            ],
            2,
        ) == 10.00

        assert round(
            result[
                "settlement_payout"
            ],
            2,
        ) == 0.00

    finally:
        connection.close()


def test_unpaired_winner_uses_settlement_revenue():
    connection = connect(
        ":memory:"
    )

    try:
        init_db(
            connection
        )

        connection.execute(
            """
            INSERT INTO account_fills (
                fill_id,
                market_ticker,
                side,
                action,
                count,
                yes_price,
                no_price,
                fee_cost,
                created_time,
                ts,
                raw_json
            )
            VALUES (
                'F1',
                'TEST',
                'yes',
                'buy',
                10,
                .60,
                .40,
                .10,
                '2026-01-01T00:00:00Z',
                1,
                '{"outcome_side":"yes"}'
            )
            """
        )

        connection.execute(
            """
            INSERT INTO account_settlements (
                market_ticker,
                settled_time,
                market_result,
                yes_count,
                revenue_cents,
                fee_cost
            )
            VALUES (
                'TEST',
                '2026-01-01T00:15:00Z',
                'yes',
                999,
                1000,
                .10
            )
            """
        )

        fills = connection.execute(
            """
            SELECT *
            FROM account_fills
            """
        ).fetchall()

        settlement = (
            connection.execute(
                """
                SELECT *
                FROM account_settlements
                """
            ).fetchone()
        )

        result = (
            reconstruct_market_pnl(
                fills,
                settlement,
            )
        )

        # Authoritative revenue is $10.
        # Cost is $6 and fee is $0.10.
        assert round(
            result["pnl"],
            2,
        ) == 3.90

    finally:
        connection.close()
