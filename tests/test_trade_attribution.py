import json

from kalshi_stats.database import (
    connect,
    init_db,
)

from kalshi_stats.trade_attribution import (
    NO_SYSTEM_DATA,
    PASS_TRADED,
    QUALIFIED_TRADED,
    build_trade_attribution,
    reconstruct_trade_sessions,
)


def insert_fill(
    connection,
    *,
    fill_id,
    ticker,
    created,
    side,
    price,
    count=10,
    fee=.10,
):
    yes_price = (
        price
        if side == "yes"
        else 1.0 - price
    )

    no_price = (
        price
        if side == "no"
        else 1.0 - price
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
            raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fill_id,
            ticker,
            side,
            "buy",
            count,
            yes_price,
            no_price,
            fee,
            created,
            json.dumps(
                {
                    "outcome_side":
                        side,
                }
            ),
        ),
    )


def insert_snapshot(
    connection,
    *,
    fill_id,
    ticker,
    ts,
    side,
    qualified,
):
    connection.execute(
        """
        INSERT INTO fill_feature_snapshots (
            fill_id,
            market_ticker,
            fill_created_time,
            fill_ts_ms,
            outcome_side,
            count,
            fill_price,
            captured_at_ms,
            market_feature_ts,
            feature_age_ms,
            seconds_remaining,
            side_bid,
            side_ask,
            base_setup_qualified
        )
        VALUES (
            ?, ?, ?, ?, ?,
            10, .65, ?,
            ?, 0, 400,
            .64, .65, ?
        )
        """,
        (
            fill_id,
            ticker,
            "2026-01-01T00:00:00Z",
            ts,
            side,
            ts,
            ts,
            int(
                qualified
            ),
        ),
    )


def test_qualified_roundtrip_session():
    connection = connect(
        ":memory:"
    )

    try:
        init_db(
            connection
        )

        insert_fill(
            connection,
            fill_id="A",
            ticker="TEST",
            created="2026-01-01T00:00:00Z",
            side="yes",
            price=.60,
        )

        insert_snapshot(
            connection,
            fill_id="A",
            ticker="TEST",
            ts=1_000,
            side="yes",
            qualified=True,
        )

        # Acquiring NO at 25c closes the YES position.
        insert_fill(
            connection,
            fill_id="B",
            ticker="TEST",
            created="2026-01-01T00:01:00Z",
            side="no",
            price=.25,
        )

        sessions = (
            reconstruct_trade_sessions(
                connection
            )
        )

        assert len(
            sessions
        ) == 1

        session = sessions[0]

        assert (
            session[
                "system_status"
            ]
            == QUALIFIED_TRADED
        )

        # $10 payout - $6 YES cost
        # - $2.50 NO close cost
        # - $0.20 fees = $1.30.
        assert round(
            session["pnl"],
            2,
        ) == 1.30

    finally:
        connection.close()


def test_pass_and_no_data_classification():
    connection = connect(
        ":memory:"
    )

    try:
        init_db(
            connection
        )

        insert_fill(
            connection,
            fill_id="PASS1",
            ticker="PASS",
            created="2026-01-01T00:00:00Z",
            side="yes",
            price=.60,
        )

        insert_snapshot(
            connection,
            fill_id="PASS1",
            ticker="PASS",
            ts=1_000,
            side="yes",
            qualified=False,
        )

        insert_fill(
            connection,
            fill_id="OLD1",
            ticker="OLD",
            created="2025-01-01T00:00:00Z",
            side="yes",
            price=.60,
        )

        sessions = (
            reconstruct_trade_sessions(
                connection
            )
        )

        statuses = {
            row[
                "market_ticker"
            ]:
            row[
                "system_status"
            ]
            for row
            in sessions
        }

        assert (
            statuses["PASS"]
            == PASS_TRADED
        )

        assert (
            statuses["OLD"]
            == NO_SYSTEM_DATA
        )

    finally:
        connection.close()


def test_qualified_opportunity_skipped():
    connection = connect(
        ":memory:"
    )

    try:
        init_db(
            connection
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
                label_status,
                first_hit,
                gross_profit_per_contract
            )
            VALUES (
                'RULE',
                'TEST',
                'yes',
                1000,
                1000,
                .64,
                .65,
                400,
                100000,
                100000,
                'LABELED',
                'TP',
                .15
            )
            """
        )

        result = (
            build_trade_attribution(
                connection
            )
        )

        assert (
            result[
                "qualified_and_traded"
            ]
            == 0
        )

        assert (
            result[
                "qualified_and_skipped"
            ]
            == 1
        )

        assert (
            result[
                "skipped_labeled"
            ]
            == 1
        )

        assert (
            result[
                "skipped_tp_rate"
            ]
            == 1.0
        )

    finally:
        connection.close()
