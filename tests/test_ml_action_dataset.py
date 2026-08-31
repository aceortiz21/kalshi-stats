import inspect

import pytest

from kalshi_stats.database import connect, init_db
from kalshi_stats.ml_action_dataset import (
    CANONICAL_ACTIONS,
    EXCLUDED_LEAKAGE_FIELDS,
    STATE_FEATURE_COLUMNS,
    ActionDefinition,
    PathObservation,
    Settlement,
    iter_action_rows,
    label_action,
)
from kalshi_stats.paper_broker import floor_contract_count, taker_fee_estimate


def _action(side="YES", profile="tp10_sl05"):
    return next(
        action
        for action in CANONICAL_ACTIONS
        if action.side == side and action.exit_profile == profile
    )


def _bar(ts, *, bid_close, bid_low, bid_high, ask_close=None, ask_low=None, ask_high=None):
    ask_close = bid_close + 0.02 if ask_close is None else ask_close
    ask_low = bid_low + 0.02 if ask_low is None else ask_low
    ask_high = bid_high + 0.02 if ask_high is None else ask_high
    return PathObservation(
        observed_ts=ts,
        yes_bid_close=bid_close,
        yes_bid_low=bid_low,
        yes_bid_high=bid_high,
        yes_ask_close=ask_close,
        yes_ask_low=ask_low,
        yes_ask_high=ask_high,
    )


def test_canonical_action_definitions_are_stable_and_exact():
    assert len(CANONICAL_ACTIONS) == 12
    assert len({action.action_id for action in CANONICAL_ACTIONS}) == 12
    assert [
        (
            action.action_id,
            action.side,
            action.take_profit_delta,
            action.stop_loss_delta,
            action.settlement_hold,
        )
        for action in CANONICAL_ACTIONS
    ] == [
        ("action:v1:yes:tp05_sl05", "YES", 0.05, 0.05, False),
        ("action:v1:yes:tp10_sl05", "YES", 0.10, 0.05, False),
        ("action:v1:yes:tp15_sl05", "YES", 0.15, 0.05, False),
        ("action:v1:yes:tp20_sl10", "YES", 0.20, 0.10, False),
        ("action:v1:yes:tp25_sl10", "YES", 0.25, 0.10, False),
        ("action:v1:yes:settle", "YES", None, None, True),
        ("action:v1:no:tp05_sl05", "NO", 0.05, 0.05, False),
        ("action:v1:no:tp10_sl05", "NO", 0.10, 0.05, False),
        ("action:v1:no:tp15_sl05", "NO", 0.15, 0.05, False),
        ("action:v1:no:tp20_sl10", "NO", 0.20, 0.10, False),
        ("action:v1:no:tp25_sl10", "NO", 0.25, 0.10, False),
        ("action:v1:no:settle", "NO", None, None, True),
    ]


def test_tp_before_sl_is_labeled_from_future_candle_order():
    outcome = label_action(
        action=_action(),
        entry_ts=100,
        entry_price=0.40,
        future=(
            _bar(160, bid_close=0.50, bid_low=0.39, bid_high=0.51),
            _bar(220, bid_close=0.34, bid_low=0.33, bid_high=0.45),
        ),
        settlement=Settlement("no", 300),
    )
    assert outcome.outcome_class == "TP"
    assert outcome.exit_ts == 160
    assert outcome.exit_price == pytest.approx(0.50)


def test_sl_before_tp_is_labeled_from_future_candle_order():
    outcome = label_action(
        action=_action(),
        entry_ts=100,
        entry_price=0.40,
        future=(
            _bar(160, bid_close=0.34, bid_low=0.33, bid_high=0.42),
            _bar(220, bid_close=0.51, bid_low=0.40, bid_high=0.52),
        ),
        settlement=Settlement("yes", 300),
    )
    assert outcome.outcome_class == "SL"
    assert outcome.exit_ts == 160
    assert outcome.exit_price == pytest.approx(0.34)


def test_same_candle_tp_sl_order_is_ambiguous_and_has_no_fabricated_pnl():
    outcome = label_action(
        action=_action(),
        entry_ts=100,
        entry_price=0.40,
        future=(_bar(160, bid_close=0.42, bid_low=0.34, bid_high=0.51),),
        settlement=Settlement("yes", 300),
    )
    assert outcome.outcome_class == "AMBIGUOUS"
    assert outcome.exit_price is None
    assert outcome.gross_pnl is None
    assert outcome.net_pnl is None


def test_intracandle_only_stop_has_class_but_no_fabricated_fill_price():
    outcome = label_action(
        action=_action(),
        entry_ts=100,
        entry_price=0.40,
        future=(_bar(160, bid_close=0.38, bid_low=0.34, bid_high=0.42),),
        settlement=Settlement("yes", 300),
    )
    assert outcome.outcome_class == "SL"
    assert outcome.resolution_detail == "SL_INTRACANDLE_EXECUTION_PRICE_UNOBSERVED"
    assert outcome.exit_price is None
    assert outcome.net_pnl is None


def test_non_future_observation_is_rejected():
    with pytest.raises(ValueError, match="non-future"):
        label_action(
            action=_action(),
            entry_ts=100,
            entry_price=0.40,
            future=(_bar(100, bid_close=0.50, bid_low=0.40, bid_high=0.51),),
            settlement=Settlement("yes", 300),
        )


def test_no_side_uses_reciprocal_yes_ask_path_semantics():
    # NO entry 30c. YES ask falling to 59c makes the NO bid high 41c,
    # crossing the NO +10c target.
    outcome = label_action(
        action=_action(side="NO"),
        entry_ts=100,
        entry_price=0.30,
        future=(
            _bar(
                160,
                bid_close=0.60,
                bid_low=0.58,
                bid_high=0.68,
                ask_close=0.62,
                ask_low=0.59,
                ask_high=0.70,
            ),
        ),
        settlement=Settlement("yes", 300),
    )
    assert outcome.outcome_class == "TP"
    assert outcome.exit_price == pytest.approx(0.40)


@pytest.mark.parametrize(
    ("side", "result", "expected", "price"),
    [
        ("YES", "yes", "SETTLEMENT_WIN", 1.0),
        ("YES", "no", "SETTLEMENT_LOSS", 0.0),
        ("NO", "no", "SETTLEMENT_WIN", 1.0),
        ("NO", "yes", "SETTLEMENT_LOSS", 0.0),
    ],
)
def test_settlement_hold_side_outcomes(side, result, expected, price):
    outcome = label_action(
        action=_action(side=side, profile="settle"),
        entry_ts=100,
        entry_price=0.40,
        future=(),
        settlement=Settlement(result, 300),
    )
    assert outcome.outcome_class == expected
    assert outcome.exit_price == price
    assert outcome.exit_ts == 300
    assert outcome.exit_fee == 0.0


def test_fee_math_reuses_repository_taker_fee_semantics():
    entry = 0.40
    exit_price = 0.50
    count = floor_contract_count(1.0, entry)
    outcome = label_action(
        action=_action(),
        entry_ts=100,
        entry_price=entry,
        future=(_bar(160, bid_close=0.50, bid_low=0.39, bid_high=0.51),),
        settlement=Settlement("no", 300),
    )
    expected_entry_fee = taker_fee_estimate(count, entry)
    expected_exit_fee = taker_fee_estimate(count, exit_price)
    assert outcome.entry_fee == expected_entry_fee
    assert outcome.exit_fee == expected_exit_fee
    assert outcome.net_pnl == pytest.approx(
        count * (exit_price - entry) - expected_entry_fee - expected_exit_fee
    )


def _insert_market_and_state(connection, *, ticker, result, observed_ts):
    connection.execute(
        """
        INSERT INTO markets (
            ticker, series_ticker, event_ticker, title, status, result,
            open_time, close_time, settlement_ts, reference_price
        ) VALUES (?, 'KXBTC15M', 'EVENT', 'test', 'settled', ?,
                  '2026-01-01T00:00:00Z', '2026-01-01T00:15:00Z',
                  '2026-01-01T00:16:00Z', 100.0)
        """,
        (ticker, result),
    )
    values = {
        "market_ticker": ticker,
        "observed_ts": observed_ts,
        "feature_version": 2,
        "result": result,
        "candle_source": "test",
        "btc_source": "test",
        "btc_ts": observed_ts * 1000 - 1000,
    }
    for index, column in enumerate(STATE_FEATURE_COLUMNS, start=1):
        values[column] = float(index) / 100.0
    values.update(
        {
            "kalshi_price_close": 0.5,
            "kalshi_price_low": 0.48,
            "kalshi_price_high": 0.52,
            "yes_bid_close": 0.49,
            "yes_ask_close": 0.51,
            "seconds_remaining": 300,
            "threshold": 100.0,
            "btc_age_ms": 1000,
            "spot": 101.0,
            "threshold_distance_dollars": 1.0,
            "threshold_distance_pct": 0.01,
            "threshold_distance_bps": 100.0,
        }
    )
    columns = tuple(values)
    connection.execute(
        f"INSERT INTO historical_market_features ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})",
        tuple(values[column] for column in columns),
    )
    connection.execute(
        """
        INSERT INTO candles (
            market_ticker, end_period_ts, period_interval, source,
            price_open, price_close, price_high, price_low,
            yes_bid_close, yes_bid_high, yes_bid_low,
            yes_ask_close, yes_ask_high, yes_ask_low
        ) VALUES (?, ?, 1, 'test', .5, .5, .52, .48,
                  .49, .50, .48, .51, .52, .50)
        """,
        (ticker, observed_ts),
    )


def test_dataset_x_excludes_outcome_and_retains_grouping_identifiers():
    connection = connect(":memory:")
    try:
        init_db(connection)
        _insert_market_and_state(
            connection, ticker="YES-MARKET", result="yes", observed_ts=100
        )
        _insert_market_and_state(
            connection, ticker="NO-MARKET", result="no", observed_ts=200
        )
        rows = list(iter_action_rows(connection))
        yes_rows = [row for row in rows if row.market_ticker == "YES-MARKET"]
        no_rows = [row for row in rows if row.market_ticker == "NO-MARKET"]

        assert rows
        assert {row.observed_ts for row in rows} == {100, 200}
        assert {row.side for row in rows} == {"YES", "NO"}
        assert all(row.action_id for row in rows)
        assert yes_rows[0].state_features == no_rows[0].state_features
        assert "result" not in STATE_FEATURE_COLUMNS
        assert "settlement_ts" not in STATE_FEATURE_COLUMNS
        assert set(EXCLUDED_LEAKAGE_FIELDS).isdisjoint(STATE_FEATURE_COLUMNS)
        source = inspect.getsource(iter_action_rows)
        assert "SELECT *" not in source.upper()
    finally:
        connection.close()
