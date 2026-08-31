import json

import pytest

from kalshi_stats.database import (
    connect,
    init_db,
)

from kalshi_stats.kalshi_ws import (
    KalshiTickerWebSocket,
)

from kalshi_stats.paper_broker import (
    PAPER_SIGNAL_MAX_AGE_MS,
    floor_contract_count,
    run_once,
    side_book,
    taker_fee_estimate,
)

from kalshi_stats.strategy_zoo import (
    grid_strategy_key,
)

from kalshi_stats.tail_zoo import (
    tail_strategy_key,
)


TEST_NOW_MS = 20_000


def register_paper_strategy(
    connection,
    *,
    strategy_key,
    family,
    definition,
):
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
        VALUES (?, ?, 1, 'test', ?, 'PAPER', 0, 0, 0, 1)
        """,
        (
            strategy_key,
            family,
            json.dumps(definition),
        ),
    )

    result = run_once(
        connection,
        now_ms=1000,
        starting_cash=10,
        trade_notional=.01,
    )

    assert result["accounts_created"] == 1


def insert_market_feature(
    connection,
    *,
    ticker,
    ts_ms,
    yes_ask,
    no_ask,
    seconds_remaining=300,
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
            ?, ?, ?, 0, 100000, 'greater', 100000,
            0, 0, 0, ?, ?, ?, ?, ?
        )
        """,
        (
            ticker,
            ts_ms,
            ts_ms,
            seconds_remaining,
            max(0.0, yes_ask - .01),
            yes_ask,
            max(0.0, no_ask - .01),
            no_ask,
        ),
    )


def insert_main_confirmation(
    connection,
    *,
    ticker,
    profile_id,
    confirmed_at_ms,
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
            spot,
            episode_number,
            episode_start_ms
        )
        VALUES (
            'test', ?, 'yes', ?, ?, .60, .61, 500,
            100000, 100000, 1, ?
        )
        """,
        (
            ticker,
            confirmed_at_ms,
            confirmed_at_ms,
            confirmed_at_ms,
        ),
    )

    opportunity_id = connection.execute(
        """
        SELECT opportunity_id
        FROM prospective_opportunities
        WHERE market_ticker = ?
        """,
        (ticker,),
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
            ?, 'test', ?, 'yes', 1, ?, ?, 0, 1, 1,
            'CONFIRMED', ?, ?, .60, .61, 500, 1, 1,
            .86, .56, 'PENDING'
        )
        """,
        (
            opportunity_id,
            ticker,
            profile_id,
            confirmed_at_ms,
            confirmed_at_ms,
            confirmed_at_ms,
        ),
    )


def insert_micro_opportunity(
    connection,
    *,
    ticker,
    detected_at_ms,
):
    connection.execute(
        """
        INSERT INTO micro_multiplier_opportunities (
            market_ticker,
            side,
            detected_at_ms,
            market_feature_ts,
            entry_price_key,
            entry_bid,
            entry_ask,
            seconds_remaining,
            time_bucket
        )
        VALUES (?, 'yes', ?, ?, 2, .001, .002, 300, '4-5m')
        """,
        (
            ticker,
            detected_at_ms,
            detected_at_ms,
        ),
    )

    opportunity_id = connection.execute(
        """
        SELECT micro_opportunity_id
        FROM micro_multiplier_opportunities
        WHERE market_ticker = ?
        """,
        (ticker,),
    ).fetchone()[0]

    connection.execute(
        """
        INSERT INTO micro_multiplier_targets (
            micro_opportunity_id,
            target_price,
            multiplier
        )
        VALUES (?, .004, 2)
        """,
        (opportunity_id,),
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


def test_stale_grid_snapshot_advances_cursor_without_trade():
    connection = connect(":memory:")

    try:
        init_db(connection)

        strategy_key = grid_strategy_key(
            "55-64",
            "4-6m",
            "settle",
        )

        register_paper_strategy(
            connection,
            strategy_key=strategy_key,
            family="GRID_V1",
            definition={},
        )

        stale_ts = (
            TEST_NOW_MS
            - PAPER_SIGNAL_MAX_AGE_MS
            - 1
        )

        insert_market_feature(
            connection,
            ticker="STALE_GRID",
            ts_ms=stale_ts,
            yes_ask=.60,
            no_ask=.40,
        )

        result = run_once(
            connection,
            now_ms=TEST_NOW_MS,
            starting_cash=10,
            trade_notional=.01,
        )

        assert result["signals_created"] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM paper_trades"
        ).fetchone()[0] == 0

        cursor = connection.execute(
            """
            SELECT last_ts_ms
            FROM paper_scan_cursors
            WHERE family = 'GRID_V1'
            """
        ).fetchone()

        assert cursor["last_ts_ms"] == stale_ts
    finally:
        connection.close()


def test_fresh_grid_snapshot_creates_trade():
    connection = connect(":memory:")

    try:
        init_db(connection)

        strategy_key = grid_strategy_key(
            "55-64",
            "4-6m",
            "settle",
        )

        register_paper_strategy(
            connection,
            strategy_key=strategy_key,
            family="GRID_V1",
            definition={},
        )

        fresh_ts = TEST_NOW_MS - 1000

        insert_market_feature(
            connection,
            ticker="FRESH_GRID",
            ts_ms=fresh_ts,
            yes_ask=.60,
            no_ask=.40,
        )

        result = run_once(
            connection,
            now_ms=TEST_NOW_MS,
            starting_cash=10,
            trade_notional=.01,
        )

        assert result["signals_created"] == 1

        trade = connection.execute(
            "SELECT * FROM paper_trades"
        ).fetchone()

        assert trade["strategy_key"] == strategy_key
        assert trade["signal_ts_ms"] == fresh_ts
    finally:
        connection.close()


def test_stale_tail_snapshot_advances_cursor_without_trade():
    connection = connect(":memory:")

    try:
        init_db(connection)

        strategy_key = tail_strategy_key(
            "LOW",
            "0.1-0.4",
            "4-6m",
            "settle",
        )

        register_paper_strategy(
            connection,
            strategy_key=strategy_key,
            family="TAIL_V1",
            definition={},
        )

        stale_ts = (
            TEST_NOW_MS
            - PAPER_SIGNAL_MAX_AGE_MS
            - 1
        )

        insert_market_feature(
            connection,
            ticker="STALE_TAIL",
            ts_ms=stale_ts,
            yes_ask=.002,
            no_ask=.998,
        )

        result = run_once(
            connection,
            now_ms=TEST_NOW_MS,
            starting_cash=10,
            trade_notional=.01,
        )

        assert result["signals_created"] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM paper_trades"
        ).fetchone()[0] == 0

        cursor = connection.execute(
            """
            SELECT last_ts_ms
            FROM paper_scan_cursors
            WHERE family = 'TAIL_V1'
            """
        ).fetchone()

        assert cursor["last_ts_ms"] == stale_ts
    finally:
        connection.close()


def test_fresh_tail_snapshot_creates_trade():
    connection = connect(":memory:")

    try:
        init_db(connection)

        strategy_key = tail_strategy_key(
            "LOW",
            "0.1-0.4",
            "4-6m",
            "settle",
        )

        register_paper_strategy(
            connection,
            strategy_key=strategy_key,
            family="TAIL_V1",
            definition={},
        )

        fresh_ts = TEST_NOW_MS - 1000

        insert_market_feature(
            connection,
            ticker="FRESH_TAIL",
            ts_ms=fresh_ts,
            yes_ask=.002,
            no_ask=.998,
        )

        result = run_once(
            connection,
            now_ms=TEST_NOW_MS,
            starting_cash=10,
            trade_notional=.01,
        )

        assert result["signals_created"] == 1

        trade = connection.execute(
            "SELECT * FROM paper_trades"
        ).fetchone()

        assert trade["strategy_key"] == strategy_key
        assert trade["signal_ts_ms"] == fresh_ts
    finally:
        connection.close()


@pytest.mark.parametrize(
    "family",
    [
        "MAIN_TRIGGER",
        "MAIN_CONTEXT",
    ],
)
def test_stale_main_inputs_do_not_create_retroactive_trades(
    family,
):
    connection = connect(":memory:")

    try:
        init_db(connection)

        profile_id = f"{family}_PROFILE"

        register_paper_strategy(
            connection,
            strategy_key=f"{family}:test",
            family=family,
            definition={"profile_id": profile_id},
        )

        stale_ts = (
            TEST_NOW_MS
            - PAPER_SIGNAL_MAX_AGE_MS
            - 1
        )

        insert_main_confirmation(
            connection,
            ticker=f"STALE_{family}",
            profile_id=profile_id,
            confirmed_at_ms=stale_ts,
        )

        result = run_once(
            connection,
            now_ms=TEST_NOW_MS,
            starting_cash=10,
            trade_notional=.01,
        )

        assert result["signals_created"] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM paper_trades"
        ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize(
    "family",
    [
        "MAIN_TRIGGER",
        "MAIN_CONTEXT",
    ],
)
def test_fresh_main_inputs_create_trades(family):
    connection = connect(":memory:")

    try:
        init_db(connection)

        profile_id = f"{family}_PROFILE"

        register_paper_strategy(
            connection,
            strategy_key=f"{family}:test",
            family=family,
            definition={"profile_id": profile_id},
        )

        fresh_ts = TEST_NOW_MS - 1000

        insert_main_confirmation(
            connection,
            ticker=f"FRESH_{family}",
            profile_id=profile_id,
            confirmed_at_ms=fresh_ts,
        )

        result = run_once(
            connection,
            now_ms=TEST_NOW_MS,
            starting_cash=10,
            trade_notional=.01,
        )

        assert result["signals_created"] == 1

        trade = connection.execute(
            "SELECT * FROM paper_trades"
        ).fetchone()

        assert trade["family"] == family
        assert trade["signal_ts_ms"] == fresh_ts
    finally:
        connection.close()


@pytest.mark.parametrize(
    "family",
    [
        "MICRO_MULTIPLIER",
        "MICRO_LIVE_DISCOVERY",
    ],
)
def test_stale_micro_inputs_do_not_create_retroactive_trades(
    family,
):
    connection = connect(":memory:")

    try:
        init_db(connection)

        register_paper_strategy(
            connection,
            strategy_key=f"{family}:test",
            family=family,
            definition={
                "entry_price_key": 2,
                "time_bucket": "4-5m",
                "target_price_key": 4,
            },
        )

        stale_ts = (
            TEST_NOW_MS
            - PAPER_SIGNAL_MAX_AGE_MS
            - 1
        )

        insert_micro_opportunity(
            connection,
            ticker=f"STALE_{family}",
            detected_at_ms=stale_ts,
        )

        result = run_once(
            connection,
            now_ms=TEST_NOW_MS,
            starting_cash=10,
            trade_notional=.01,
        )

        assert result["signals_created"] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM paper_trades"
        ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize(
    "family",
    [
        "MICRO_MULTIPLIER",
        "MICRO_LIVE_DISCOVERY",
    ],
)
def test_fresh_micro_inputs_create_trades(family):
    connection = connect(":memory:")

    try:
        init_db(connection)

        register_paper_strategy(
            connection,
            strategy_key=f"{family}:test",
            family=family,
            definition={
                "entry_price_key": 2,
                "time_bucket": "4-5m",
                "target_price_key": 4,
            },
        )

        fresh_ts = TEST_NOW_MS - 1000

        insert_micro_opportunity(
            connection,
            ticker=f"FRESH_{family}",
            detected_at_ms=fresh_ts,
        )

        result = run_once(
            connection,
            now_ms=TEST_NOW_MS,
            starting_cash=10,
            trade_notional=.01,
        )

        assert result["signals_created"] == 1

        trade = connection.execute(
            "SELECT * FROM paper_trades"
        ).fetchone()

        assert trade["family"] == family
        assert trade["signal_ts_ms"] == fresh_ts
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


def test_tail_multiplier_follows_actual_fill():
    from kalshi_stats.paper_broker import (
        fill_adjusted_exit_prices,
    )

    trade = {
        "family":
            "TAIL_V1",

        "entry_limit":
            .002,

        "tp_price":
            .004,

        "sl_price":
            None,
    }

    tp, sl = (
        fill_adjusted_exit_prices(
            trade,
            .0015,
        )
    )

    assert round(
        tp,
        8,
    ) == .003

    assert sl is None
