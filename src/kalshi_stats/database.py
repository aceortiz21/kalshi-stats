from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    ticker TEXT PRIMARY KEY,
    series_ticker TEXT NOT NULL,
    event_ticker TEXT,
    title TEXT,
    status TEXT,
    result TEXT,
    market_type TEXT,
    open_time TEXT,
    close_time TEXT,
    expected_expiration_time TEXT,
    settlement_ts TEXT,
    updated_time TEXT,
    yes_sub_title TEXT,
    no_sub_title TEXT,
    reference_price REAL,
    final_price REAL,
    last_price REAL,
    yes_bid REAL,
    yes_ask REAL,
    no_bid REAL,
    no_ask REAL,
    volume REAL,
    open_interest REAL
);

CREATE TABLE IF NOT EXISTS candles (
    market_ticker TEXT NOT NULL,
    end_period_ts INTEGER NOT NULL,
    period_interval INTEGER NOT NULL,
    source TEXT NOT NULL,
    price_open REAL NOT NULL,
    price_close REAL NOT NULL,
    price_high REAL NOT NULL,
    price_low REAL NOT NULL,
    price_mean REAL,
    price_previous REAL,
    yes_bid_open REAL,
    yes_bid_close REAL,
    yes_bid_high REAL,
    yes_bid_low REAL,
    yes_ask_open REAL,
    yes_ask_close REAL,
    yes_ask_high REAL,
    yes_ask_low REAL,
    volume REAL,
    open_interest REAL,
    PRIMARY KEY (market_ticker, end_period_ts, period_interval, source)
);

CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    market_ticker TEXT NOT NULL,
    created_time TEXT NOT NULL,
    yes_price REAL NOT NULL,
    no_price REAL NOT NULL,
    count REAL,
    taker_side TEXT,
    taker_book_side TEXT,
    taker_outcome_side TEXT
);

CREATE TABLE IF NOT EXISTS quote_snapshots (
    market_ticker TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    status TEXT NOT NULL,
    yes_bid REAL NOT NULL,
    yes_ask REAL NOT NULL,
    no_bid REAL NOT NULL,
    no_ask REAL NOT NULL,
    last_price REAL,
    volume REAL,
    open_interest REAL,
    PRIMARY KEY (market_ticker, collected_at)
);

CREATE INDEX IF NOT EXISTS idx_markets_series_status ON markets (series_ticker, status);
CREATE INDEX IF NOT EXISTS idx_candles_market_time ON candles (market_ticker, end_period_ts);
CREATE INDEX IF NOT EXISTS idx_trades_market_time ON trades (market_ticker, created_time);
CREATE INDEX IF NOT EXISTS idx_snapshots_market_time ON quote_snapshots (market_ticker, collected_at);

CREATE TABLE IF NOT EXISTS btc_1s (
    ts INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL
);

CREATE INDEX IF NOT EXISTS idx_btc_1s_source_ts ON btc_1s (source, ts);

CREATE TABLE IF NOT EXISTS btc_feature_snapshots (
    ts INTEGER PRIMARY KEY,
    source TEXT NOT NULL,

    spot REAL,
    best_bid REAL,
    best_ask REAL,
    spread_bps REAL,

    return_30s REAL,
    return_60s REAL,
    return_180s REAL,
    return_300s REAL,

    ema_5 REAL,
    ema_9 REAL,
    ema_21 REAL,
    ema_5_9_bps REAL,
    ema_9_21_bps REAL,
    ema_5_slope_bps REAL,
    ema_9_slope_bps REAL,
    ema_21_slope_bps REAL,

    vwap_60s REAL,
    vwap_300s REAL,
    vwap_distance_60s_bps REAL,
    vwap_distance_300s_bps REAL,

    realized_vol_60s_bps REAL,
    realized_vol_300s_bps REAL,

    range_60s_bps REAL,
    range_300s_bps REAL,

    trade_volume_60s REAL,
    trade_volume_300s REAL,
    relative_volume_60s REAL,

    trade_imbalance_60s REAL,
    trade_imbalance_300s REAL,

    book_imbalance_top10 REAL
);

CREATE INDEX IF NOT EXISTS idx_btc_features_source_ts
ON btc_feature_snapshots (source, ts);


CREATE TABLE IF NOT EXISTS account_fills (
    fill_id TEXT PRIMARY KEY,

    trade_id TEXT,
    order_id TEXT,

    market_ticker TEXT NOT NULL,

    side TEXT,
    action TEXT,

    count REAL NOT NULL,

    yes_price REAL,
    no_price REAL,

    fee_cost REAL NOT NULL DEFAULT 0,

    is_taker INTEGER,

    created_time TEXT,
    ts INTEGER,

    subaccount_number INTEGER NOT NULL DEFAULT 0,

    raw_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_account_fills_market_time
ON account_fills (
    market_ticker,
    ts
);


CREATE TABLE IF NOT EXISTS account_settlements (
    market_ticker TEXT NOT NULL,
    settled_time TEXT NOT NULL,
    subaccount_number INTEGER NOT NULL DEFAULT 0,

    event_ticker TEXT,
    market_result TEXT,

    yes_count REAL NOT NULL DEFAULT 0,
    yes_total_cost REAL NOT NULL DEFAULT 0,

    no_count REAL NOT NULL DEFAULT 0,
    no_total_cost REAL NOT NULL DEFAULT 0,

    revenue_cents INTEGER,
    value_cents INTEGER,

    fee_cost REAL NOT NULL DEFAULT 0,

    raw_json TEXT,

    PRIMARY KEY (
        market_ticker,
        settled_time,
        subaccount_number
    )
);

CREATE INDEX IF NOT EXISTS idx_account_settlements_time
ON account_settlements (
    settled_time
);


CREATE TABLE IF NOT EXISTS account_positions (
    market_ticker TEXT NOT NULL,
    subaccount_number INTEGER NOT NULL DEFAULT 0,

    position REAL,
    total_traded REAL,
    market_exposure REAL,

    realized_pnl REAL,
    fees_paid REAL,

    resting_orders_count INTEGER,

    last_updated_ts TEXT,
    collected_at_ms INTEGER NOT NULL,

    raw_json TEXT,

    PRIMARY KEY (
        market_ticker,
        subaccount_number
    )
);


CREATE TABLE IF NOT EXISTS account_balance_snapshots (
    collected_at_ms INTEGER NOT NULL,
    subaccount_number INTEGER NOT NULL DEFAULT 0,

    balance_cents INTEGER NOT NULL,
    portfolio_value_cents INTEGER NOT NULL,

    api_updated_ts INTEGER,

    PRIMARY KEY (
        collected_at_ms,
        subaccount_number
    )
);

CREATE INDEX IF NOT EXISTS idx_account_balance_time
ON account_balance_snapshots (
    collected_at_ms
);



CREATE TABLE IF NOT EXISTS prospective_opportunities (
    opportunity_id INTEGER PRIMARY KEY AUTOINCREMENT,

    strategy_id TEXT NOT NULL,
    market_ticker TEXT NOT NULL,
    side TEXT NOT NULL,

    detected_at_ms INTEGER NOT NULL,
    market_feature_ts INTEGER NOT NULL,

    entry_bid REAL NOT NULL,
    entry_ask REAL NOT NULL,
    seconds_remaining REAL NOT NULL,

    threshold REAL NOT NULL,
    spot REAL NOT NULL,

    side_threshold_distance_bps REAL,

    return_60s_aligned REAL,
    return_300s_aligned REAL,

    vwap_distance_300s_bps_aligned REAL,
    realized_vol_60s_bps REAL,

    trade_imbalance_60s_aligned REAL,
    trade_imbalance_300s_aligned REAL,
    book_imbalance_top10_aligned REAL,

    btc_spread_bps REAL,

    brti_ts INTEGER,
    brti_age_ms INTEGER,

    brti_value REAL,
    brti_avg_60s_value REAL,
    brti_final_60s_avg_15m REAL,

    brti_side_distance_dollars REAL,

    label_status TEXT NOT NULL DEFAULT 'PENDING',

    tp_hit INTEGER,
    sl_hit INTEGER,
    first_hit TEXT,

    exit_ts_ms INTEGER,
    exit_bid REAL,

    gross_profit_per_contract REAL,
    settlement_result TEXT,

    episode_number INTEGER NOT NULL DEFAULT 1,
    episode_start_ms INTEGER NOT NULL DEFAULT 0,
    episode_end_ms INTEGER,

    UNIQUE (
        strategy_id,
        market_ticker,
        side,
        episode_number
    )
);

CREATE INDEX IF NOT EXISTS idx_prospective_opportunities_time
ON prospective_opportunities (
    detected_at_ms
);


CREATE TABLE IF NOT EXISTS prospective_episode_state (
    strategy_id TEXT NOT NULL,
    market_ticker TEXT NOT NULL,
    side TEXT NOT NULL,

    episode_number INTEGER NOT NULL DEFAULT 0,

    in_setup INTEGER NOT NULL DEFAULT 0,
    outside_since_ms INTEGER,
    last_seen_ms INTEGER NOT NULL,

    PRIMARY KEY (
        strategy_id,
        market_ticker,
        side
    )
);



CREATE TABLE IF NOT EXISTS micro_multiplier_opportunities (
    micro_opportunity_id INTEGER
        PRIMARY KEY AUTOINCREMENT,

    market_ticker TEXT NOT NULL,
    side TEXT NOT NULL,

    detected_at_ms INTEGER NOT NULL,
    market_feature_ts INTEGER NOT NULL,

    entry_price_key INTEGER NOT NULL,

    entry_bid REAL NOT NULL,
    entry_ask REAL NOT NULL,

    seconds_remaining REAL NOT NULL,
    time_bucket TEXT NOT NULL,

    label_status TEXT NOT NULL
        DEFAULT 'PENDING',

    settlement_result TEXT,
    path_complete INTEGER,

    UNIQUE (
        market_ticker,
        side,
        entry_price_key,
        time_bucket
    )
);

CREATE INDEX IF NOT EXISTS
idx_micro_multiplier_opportunities_time
ON micro_multiplier_opportunities (
    detected_at_ms
);

CREATE INDEX IF NOT EXISTS
idx_micro_multiplier_opportunities_market
ON micro_multiplier_opportunities (
    market_ticker,
    side
);


CREATE TABLE IF NOT EXISTS micro_multiplier_targets (
    micro_opportunity_id INTEGER NOT NULL,

    target_price REAL NOT NULL,
    multiplier REAL NOT NULL,

    status TEXT NOT NULL
        DEFAULT 'PENDING',

    hit_ts_ms INTEGER,
    hit_bid REAL,

    PRIMARY KEY (
        micro_opportunity_id,
        target_price
    ),

    FOREIGN KEY (
        micro_opportunity_id
    )
    REFERENCES micro_multiplier_opportunities(
        micro_opportunity_id
    )
);

CREATE INDEX IF NOT EXISTS
idx_micro_multiplier_targets_status
ON micro_multiplier_targets (
    status
);




CREATE TABLE IF NOT EXISTS micro_multiplier_atlas (
    entry_price_key INTEGER NOT NULL,
    time_bucket TEXT NOT NULL,
    target_price_key INTEGER NOT NULL,

    entry_price REAL NOT NULL,
    target_price REAL NOT NULL,
    multiplier REAL NOT NULL,

    observations INTEGER NOT NULL,
    unique_markets INTEGER NOT NULL,
    hits INTEGER NOT NULL,

    touch_rate REAL NOT NULL,
    ci_low REAL NOT NULL,
    ci_high REAL NOT NULL,

    break_even_touch REAL NOT NULL,
    conservative_edge REAL NOT NULL,

    limit_only_ev REAL NOT NULL,
    limit_only_roi REAL NOT NULL,

    source_market_count INTEGER NOT NULL,
    generated_at_ms INTEGER NOT NULL,

    PRIMARY KEY (
        entry_price_key,
        time_bucket,
        target_price_key
    )
);

CREATE INDEX IF NOT EXISTS
idx_micro_multiplier_atlas_lookup
ON micro_multiplier_atlas (
    entry_price_key,
    time_bucket
);



CREATE TABLE IF NOT EXISTS fill_feature_snapshots (
    fill_id TEXT PRIMARY KEY,

    market_ticker TEXT NOT NULL,

    fill_created_time TEXT,
    fill_ts_ms INTEGER NOT NULL,

    outcome_side TEXT NOT NULL,

    count REAL NOT NULL,
    fill_price REAL NOT NULL,

    captured_at_ms INTEGER NOT NULL,

    market_feature_ts INTEGER,
    feature_age_ms INTEGER,

    seconds_remaining REAL,

    side_bid REAL,
    side_ask REAL,

    base_setup_qualified INTEGER NOT NULL DEFAULT 0,

    threshold REAL,
    spot REAL,

    side_threshold_distance_bps REAL,

    return_60s_aligned REAL,
    return_300s_aligned REAL,

    vwap_distance_300s_bps_aligned REAL,
    realized_vol_60s_bps REAL,

    trade_imbalance_60s_aligned REAL,
    trade_imbalance_300s_aligned REAL,
    book_imbalance_top10_aligned REAL,

    btc_spread_bps REAL,

    brti_ts INTEGER,
    brti_age_ms INTEGER,

    brti_value REAL,
    brti_avg_60s_value REAL,
    brti_final_60s_avg_15m REAL,

    brti_side_distance_dollars REAL,

    FOREIGN KEY (fill_id)
        REFERENCES account_fills(fill_id)
);

CREATE INDEX IF NOT EXISTS idx_fill_feature_snapshots_time
ON fill_feature_snapshots (
    fill_ts_ms
);

CREATE INDEX IF NOT EXISTS idx_fill_feature_snapshots_market
ON fill_feature_snapshots (
    market_ticker,
    fill_ts_ms
);


CREATE TABLE IF NOT EXISTS historical_market_features (
    market_ticker TEXT NOT NULL,
    observed_ts INTEGER NOT NULL,

    feature_version INTEGER NOT NULL,

    result TEXT NOT NULL,
    candle_source TEXT,

    kalshi_price_close REAL NOT NULL,
    kalshi_price_low REAL NOT NULL,
    kalshi_price_high REAL NOT NULL,

    yes_bid_close REAL,
    yes_ask_close REAL,

    seconds_remaining INTEGER NOT NULL,

    threshold REAL NOT NULL,

    btc_source TEXT NOT NULL,
    btc_ts INTEGER NOT NULL,
    btc_age_ms INTEGER NOT NULL,

    spot REAL NOT NULL,

    threshold_distance_dollars REAL NOT NULL,
    threshold_distance_pct REAL NOT NULL,
    threshold_distance_bps REAL NOT NULL,
    threshold_distance_vol60 REAL,

    return_30s REAL,
    return_60s REAL,
    return_180s REAL,
    return_300s REAL,

    ema_5s REAL,
    ema_9s REAL,
    ema_21s REAL,

    ema_5s_9s_bps REAL,
    ema_9s_21s_bps REAL,

    ema_5s_slope_bps REAL,
    ema_9s_slope_bps REAL,
    ema_21s_slope_bps REAL,

    ema_5m REAL,
    ema_9m REAL,
    ema_21m REAL,

    ema_5m_9m_bps REAL,
    ema_9m_21m_bps REAL,

    ema_5m_slope_bps REAL,
    ema_9m_slope_bps REAL,
    ema_21m_slope_bps REAL,

    vwap_60s_proxy REAL,
    vwap_300s_proxy REAL,

    vwap_distance_60s_bps REAL,
    vwap_distance_300s_bps REAL,

    realized_vol_60s_bps REAL,
    realized_vol_300s_bps REAL,

    range_60s_bps REAL,
    range_300s_bps REAL,

    btc_volume_60s REAL,
    btc_volume_300s REAL,
    relative_volume_60s REAL,

    PRIMARY KEY (
        market_ticker,
        observed_ts
    )
);

CREATE INDEX IF NOT EXISTS idx_historical_market_features_ts
ON historical_market_features (observed_ts);

CREATE INDEX IF NOT EXISTS idx_historical_market_features_result
ON historical_market_features (result);

CREATE TABLE IF NOT EXISTS brti_snapshots (
    index_id TEXT NOT NULL,
    ts INTEGER NOT NULL,
    received_at INTEGER,

    value REAL NOT NULL,

    avg_60s_value REAL,
    avg_60s_window_size INTEGER,
    avg_60s_window_start_ts_ms INTEGER,
    avg_60s_window_end_ts_exclusive INTEGER,

    final_60s_avg_15m REAL,
    final_60s_window_size_15m INTEGER,
    final_60s_window_start_ts_ms_15m INTEGER,
    final_60s_window_end_ts_exclusive_15m INTEGER,

    PRIMARY KEY (index_id, ts)
);

CREATE INDEX IF NOT EXISTS idx_brti_snapshots_ts
ON brti_snapshots (ts);

CREATE TABLE IF NOT EXISTS market_feature_snapshots (
    market_ticker TEXT NOT NULL,
    ts INTEGER NOT NULL,

    quote_collected_at TEXT,
    quote_age_ms INTEGER,

    btc_ts INTEGER NOT NULL,
    btc_age_ms INTEGER NOT NULL,

    threshold REAL NOT NULL,
    threshold_rule TEXT NOT NULL,

    spot REAL NOT NULL,

    threshold_distance_dollars REAL NOT NULL,
    threshold_distance_pct REAL NOT NULL,
    threshold_distance_bps REAL NOT NULL,
    threshold_distance_vol60 REAL,

    seconds_remaining REAL NOT NULL,

    yes_bid REAL NOT NULL,
    yes_ask REAL NOT NULL,
    no_bid REAL NOT NULL,
    no_ask REAL NOT NULL,

    btc_spread_bps REAL,

    return_30s REAL,
    return_60s REAL,
    return_180s REAL,
    return_300s REAL,

    ema_5 REAL,
    ema_9 REAL,
    ema_21 REAL,
    ema_5_9_bps REAL,
    ema_9_21_bps REAL,

    ema_5_slope_bps REAL,
    ema_9_slope_bps REAL,
    ema_21_slope_bps REAL,

    vwap_60s REAL,
    vwap_300s REAL,
    vwap_distance_60s_bps REAL,
    vwap_distance_300s_bps REAL,

    realized_vol_60s_bps REAL,
    realized_vol_300s_bps REAL,

    range_60s_bps REAL,
    range_300s_bps REAL,

    trade_volume_60s REAL,
    trade_volume_300s REAL,
    relative_volume_60s REAL,

    trade_imbalance_60s REAL,
    trade_imbalance_300s REAL,

    book_imbalance_top10 REAL,

    PRIMARY KEY (
        market_ticker,
        ts
    )
);

CREATE INDEX IF NOT EXISTS idx_market_features_ts
ON market_feature_snapshots (ts);

CREATE INDEX IF NOT EXISTS idx_market_features_ticker_ts
ON market_feature_snapshots (market_ticker, ts);
"""



def _migrate_prospective_opportunity_episodes(
    connection: sqlite3.Connection,
) -> None:
    """
    Upgrade the original one-opportunity-per-market/side
    table to episode-aware storage without losing rows.
    """

    columns = {
        row["name"]
        for row in connection.execute(
            """
            PRAGMA table_info(
                prospective_opportunities
            )
            """
        ).fetchall()
    }

    if not columns:
        return

    if "episode_number" in columns:
        return

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        connection.execute(
            """
            ALTER TABLE
                prospective_opportunities
            RENAME TO
                prospective_opportunities_legacy
            """
        )

        connection.execute(
            """
            CREATE TABLE prospective_opportunities (
                opportunity_id INTEGER
                    PRIMARY KEY AUTOINCREMENT,

                strategy_id TEXT NOT NULL,
                market_ticker TEXT NOT NULL,
                side TEXT NOT NULL,

                detected_at_ms INTEGER NOT NULL,
                market_feature_ts INTEGER NOT NULL,

                entry_bid REAL NOT NULL,
                entry_ask REAL NOT NULL,
                seconds_remaining REAL NOT NULL,

                threshold REAL NOT NULL,
                spot REAL NOT NULL,

                side_threshold_distance_bps REAL,

                return_60s_aligned REAL,
                return_300s_aligned REAL,

                vwap_distance_300s_bps_aligned REAL,
                realized_vol_60s_bps REAL,

                trade_imbalance_60s_aligned REAL,
                trade_imbalance_300s_aligned REAL,
                book_imbalance_top10_aligned REAL,

                btc_spread_bps REAL,

                brti_ts INTEGER,
                brti_age_ms INTEGER,

                brti_value REAL,
                brti_avg_60s_value REAL,
                brti_final_60s_avg_15m REAL,

                brti_side_distance_dollars REAL,

                label_status TEXT NOT NULL
                    DEFAULT 'PENDING',

                tp_hit INTEGER,
                sl_hit INTEGER,
                first_hit TEXT,

                exit_ts_ms INTEGER,
                exit_bid REAL,

                gross_profit_per_contract REAL,
                settlement_result TEXT,

                episode_number INTEGER NOT NULL
                    DEFAULT 1,

                episode_start_ms INTEGER NOT NULL
                    DEFAULT 0,

                episode_end_ms INTEGER,

                UNIQUE (
                    strategy_id,
                    market_ticker,
                    side,
                    episode_number
                )
            )
            """
        )

        connection.execute(
            """
            INSERT INTO prospective_opportunities (
                opportunity_id,

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

                side_threshold_distance_bps,

                return_60s_aligned,
                return_300s_aligned,

                vwap_distance_300s_bps_aligned,
                realized_vol_60s_bps,

                trade_imbalance_60s_aligned,
                trade_imbalance_300s_aligned,
                book_imbalance_top10_aligned,

                btc_spread_bps,

                brti_ts,
                brti_age_ms,

                brti_value,
                brti_avg_60s_value,
                brti_final_60s_avg_15m,

                brti_side_distance_dollars,

                label_status,

                tp_hit,
                sl_hit,
                first_hit,

                exit_ts_ms,
                exit_bid,

                gross_profit_per_contract,
                settlement_result,

                episode_number,
                episode_start_ms,
                episode_end_ms
            )

            SELECT
                opportunity_id,

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

                side_threshold_distance_bps,

                return_60s_aligned,
                return_300s_aligned,

                vwap_distance_300s_bps_aligned,
                realized_vol_60s_bps,

                trade_imbalance_60s_aligned,
                trade_imbalance_300s_aligned,
                book_imbalance_top10_aligned,

                btc_spread_bps,

                brti_ts,
                brti_age_ms,

                brti_value,
                brti_avg_60s_value,
                brti_final_60s_avg_15m,

                brti_side_distance_dollars,

                label_status,

                tp_hit,
                sl_hit,
                first_hit,

                exit_ts_ms,
                exit_bid,

                gross_profit_per_contract,
                settlement_result,

                1,
                detected_at_ms,
                NULL

            FROM
                prospective_opportunities_legacy
            """
        )

        connection.execute(
            """
            DROP TABLE
                prospective_opportunities_legacy
            """
        )

        connection.execute(
            """
            CREATE INDEX
            idx_prospective_opportunities_time

            ON prospective_opportunities (
                detected_at_ms
            )
            """
        )

        connection.commit()

    except Exception:
        connection.rollback()
        raise



def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    if path != Path(":memory:"):
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        path,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row

    if path != Path(":memory:"):
        # WAL is important for the always-on architecture:
        # live WebSocket writes can continue while a separate
        # analytics process reads the historical database.
        connection.execute(
            "PRAGMA journal_mode=WAL"
        )

        connection.execute(
            "PRAGMA synchronous=NORMAL"
        )

        connection.execute(
            "PRAGMA busy_timeout=30000"
        )

    return connection


def init_db(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)

    _migrate_prospective_opportunity_episodes(
        connection
    )

    connection.commit()
