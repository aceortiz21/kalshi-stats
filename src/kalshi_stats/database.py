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
    connection.commit()
