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
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    if path != Path(":memory:"):
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()
