import json

from kalshi_stats.database import (
    connect,
    init_db,
)

from kalshi_stats.kalshi_ws import (
    KalshiTickerWebSocket,
)

from kalshi_stats.paper_broker import (
    floor_contract_count,
    run_once,
    side_book,
    taker_fee_estimate,
)


def test_fractional_paper_count():
    assert (
        floor_contract_count(
            .01,
            .61,
        )
        == .01
    )

    assert (
        floor_contract_count(
            .01,
            .001,
        )
        == 10.0
    )


def test_fee_estimate_uses_centicent_rounding():
    fee = taker_fee_estimate(
        .01,
        .61,
    )

    assert fee > 0

    assert round(
        fee * 10000
    ) == (
        fee * 10000
    )


def test_no_side_book_uses_reciprocal_sizes():
    row = {
        "yes_bid": .60,
        "yes_bid_size": 20.0,

        "yes_ask": .62,
        "yes_ask_size": 30.0,

        "no_bid": .38,
        "no_bid_size": 30.0,

        "no_ask": .40,
        "no_ask_size": 20.0,
    }

    no = side_book(
        row,
        "no",
    )

    assert no["bid"] == .38
    assert no["bid_size"] == 30.0

    assert no["ask"] == .40
    assert no["ask_size"] == 20.0


def test_paper_main_round_trip_tp():
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
                '60-69c_5-10m_tp25_sl5',
                'TEST',
                'yes',

                2000,
                2000,

                .60,
                .61,
                500,

                100000,
                100000,

                1,
                2000
            )
            """
        )

        opportunity_id = (
            connection.execute(
                """
                SELECT opportunity_id
                FROM prospective_opportunities
                """
            ).fetchone()[0]
        )

        connection.execute(
            """
            INSERT INTO main_trigger_confirmations (
                opportunity_id,
                strategy_id,

                market_ticker,
                side,
                episode_number,

                profile_id,

                raw_start_ms,

                window_seconds,
                minimum_occupancy,
                requires_continuous,

                status,

                confirmed_at_ms,
                confirm_feature_ts,

                entry_bid,
                entry_ask,
                seconds_remaining,

                qualified_samples,
                total_samples,

                tp_price,
                sl_price,

                label_status
            )
            VALUES (
                ?,
                '60-69c_5-10m_tp25_sl5',

                'TEST',
                'yes',
                1,

                'RAW',

                2000,

                0,
                1,
                1,

                'CONFIRMED',

                2000,
                2000,

                .60,
                .61,
                500,

                1,
                1,

                .86,
                .56,

                'PENDING'
            )
            """,
            (
                opportunity_id,
            ),
        )

        connection.execute(
            """
            INSERT INTO shadow_strategy_registry (
                strategy_key,

                family,
                version,

                description,
                definition_json,

                evidence_basis,

                created_at_ms,

                discovery_cutoff_ms,
                shadow_start_ms,

                enabled
            )
            VALUES (
                'main:RAW:v1',

                'MAIN_TRIGGER',
                1,

                'test',
                ?,

                'EXECUTABLE_BID_SHADOW',

                1000,

                999,
                1000,

                1
            )
            """,
            (
                json.dumps(
                    {
                        "profile_id":
                            "RAW"
                    }
                ),
            ),
        )

        connection.execute(
            """
            INSERT INTO topbook_snapshots (
                market_ticker,
                ts_ms,

                yes_bid,
                yes_bid_size,

                yes_ask,
                yes_ask_size,

                no_bid,
                no_bid_size,

                no_ask,
                no_ask_size,

                source
            )
            VALUES (
                'TEST',
                2000,

                .60,
                100,

                .61,
                100,

                .39,
                100,

                .40,
                100,

                'test'
            )
            """
        )

        connection.execute(
            """
            INSERT INTO topbook_snapshots (
                market_ticker,
                ts_ms,

                yes_bid,
                yes_bid_size,

                yes_ask,
                yes_ask_size,

                no_bid,
                no_bid_size,

                no_ask,
                no_ask_size,

                source
            )
            VALUES (
                'TEST',
                3000,

                .86,
                100,

                .87,
                100,

                .13,
                100,

                .14,
                100,

                'test'
            )
            """
        )

        # Start the paper account before the
        # prospective signal exists.
        initial = run_once(
            connection,
            now_ms=1500,

            starting_cash=10,
            trade_notional=.01,
        )

        assert (
            initial[
                "accounts_created"
            ]
            == 1
        )

        # Now advance time through the real signal,
        # entry book, and TP book.
        result = run_once(
            connection,
            now_ms=4000,

            starting_cash=10,
            trade_notional=.01,
        )

        assert (
            result[
                "signals_created"
            ]
            == 1
        )

        trade = connection.execute(
            """
            SELECT *
            FROM paper_trades
            """
        ).fetchone()

        assert (
            trade["state"]
            == "CLOSED"
        )

        assert (
            trade[
                "entry_status"
            ]
            == "FILLED"
        )

        assert (
            trade[
                "exit_reason"
            ]
            == "TP"
        )

        assert (
            trade[
                "filled_count"
            ]
            == .01
        )

        assert (
            trade[
                "entry_avg_price"
            ]
            == .61
        )

        assert (
            trade[
                "exit_avg_price"
            ]
            == .86
        )

    finally:
        connection.close()


def test_paper_broker_does_not_discover_future_signal():
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
                'FUTURE',
                'KXBTC15M'
            )
            """
        )

        connection.execute(
            """
            INSERT INTO shadow_strategy_registry (
                strategy_key,

                family,
                version,

                description,
                definition_json,

                evidence_basis,

                created_at_ms,
                discovery_cutoff_ms,
                shadow_start_ms,

                enabled
            )
            VALUES (
                'future:RAW:v1',

                'MAIN_TRIGGER',
                1,

                'test',
                ?,

                'PAPER',

                1000,
                999,
                1000,

                1
            )
            """,
            (
                json.dumps(
                    {
                        "profile_id":
                            "RAW"
                    }
                ),
            ),
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
                '60-69c_5-10m_tp25_sl5',
                'FUTURE',
                'yes',

                5000,
                5000,

                .60,
                .61,
                500,

                100000,
                100000,

                1,
                5000
            )
            """
        )

        opportunity_id = connection.execute(
            """
            SELECT opportunity_id
            FROM prospective_opportunities
            """
        ).fetchone()[0]

        connection.execute(
            """
            INSERT INTO main_trigger_confirmations (
                opportunity_id,
                strategy_id,

                market_ticker,
                side,
                episode_number,

                profile_id,

                raw_start_ms,

                window_seconds,
                minimum_occupancy,
                requires_continuous,

                status,

                confirmed_at_ms,
                confirm_feature_ts,

                entry_bid,
                entry_ask,
                seconds_remaining,

                qualified_samples,
                total_samples,

                tp_price,
                sl_price,

                label_status
            )
            VALUES (
                ?,
                '60-69c_5-10m_tp25_sl5',

                'FUTURE',
                'yes',
                1,

                'RAW',

                5000,

                0,
                1,
                1,

                'CONFIRMED',

                5000,
                5000,

                .60,
                .61,
                500,

                1,
                1,

                .86,
                .56,

                'PENDING'
            )
            """,
            (
                opportunity_id,
            ),
        )

        result = run_once(
            connection,
            now_ms=2000,

            starting_cash=10,
            trade_notional=.01,
        )

        assert (
            result[
                "accounts_created"
            ]
            == 1
        )

        assert (
            result[
                "signals_created"
            ]
            == 0
        )

        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM paper_trades
            """
        ).fetchone()[0] == 0

    finally:
        connection.close()


def test_empty_paper_dashboard_state():
    from kalshi_stats.paper_broker import (
        build_paper_dashboard_state,
    )

    connection = connect(
        ":memory:"
    )

    try:
        init_db(
            connection
        )

        state = build_paper_dashboard_state(
            connection
        )

        assert state[
            "account_count"
        ] == 0

        assert state[
            "signal_count"
        ] == 0

        assert state[
            "total_equity"
        ] == 0

        assert state[
            "best"
        ] is None

    finally:
        connection.close()


def test_paper_dashboard_signature_changes_with_equity():
    from kalshi_stats.paper_broker import (
        paper_dashboard_signature,
    )

    state = {
        "account_count": 1,
        "signal_count": 0,
        "closed_count": 0,
        "open_count": 0,
        "no_fill_count": 0,
        "total_equity": 10.0,

        "best": {
            "strategy_key":
                "test",

            "equity":
                10.0,
        },

        "recent": [],
    }

    first = paper_dashboard_signature(
        state
    )

    state[
        "total_equity"
    ] = 10.5

    state[
        "best"
    ][
        "equity"
    ] = 10.5

    second = paper_dashboard_signature(
        state
    )

    assert first != second


def test_delta_exits_follow_actual_fill():
    from kalshi_stats.paper_broker import (
        fill_adjusted_exit_prices,
    )

    trade = {
        "family":
            "MAIN_TRIGGER",

        "entry_limit":
            .67,

        "tp_price":
            .92,

        "sl_price":
            .62,
    }

    tp, sl = (
        fill_adjusted_exit_prices(
            trade,
            .61,
        )
    )

    assert round(
        tp,
        8,
    ) == .86

    assert round(
        sl,
        8,
    ) == .56


def test_micro_target_remains_absolute():
    from kalshi_stats.paper_broker import (
        fill_adjusted_exit_prices,
    )

    trade = {
        "family":
            "MICRO_MULTIPLIER",

        "entry_limit":
            .001,

        "tp_price":
            .005,

        "sl_price":
            None,
    }

    tp, sl = (
        fill_adjusted_exit_prices(
            trade,
            .0008,
        )
    )

    assert tp == .005
    assert sl is None
