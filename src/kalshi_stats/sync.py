from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import re
import sqlite3
from typing import Iterable

from .kalshi_api import KalshiClient


PRICE_RE = re.compile(r"\$([0-9,]+(?:\.[0-9]+)?)")


def _to_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    return float(str(value).replace(",", ""))


def _parse_reference_price(market: dict[str, object]) -> float | None:
    if market.get("floor_strike") is not None:
        return float(market["floor_strike"])
    yes_sub_title = str(market.get("yes_sub_title") or "")
    match = PRICE_RE.search(yes_sub_title.replace(",", ""))
    if match:
        return float(match.group(1))
    return None


def upsert_markets(connection: sqlite3.Connection, markets: Iterable[dict[str, object]], series_ticker: str) -> int:
    rows = []
    for market in markets:
        rows.append(
            (
                market["ticker"],
                series_ticker,
                market.get("event_ticker"),
                market.get("title"),
                market.get("status"),
                market.get("result"),
                market.get("market_type"),
                market.get("open_time"),
                market.get("close_time"),
                market.get("expected_expiration_time"),
                market.get("settlement_ts"),
                market.get("updated_time"),
                market.get("yes_sub_title"),
                market.get("no_sub_title"),
                _parse_reference_price(market),
                _to_float(market.get("expiration_value")),
                _to_float(market.get("last_price_dollars")),
                _to_float(market.get("yes_bid_dollars")),
                _to_float(market.get("yes_ask_dollars")),
                _to_float(market.get("no_bid_dollars")),
                _to_float(market.get("no_ask_dollars")),
                _to_float(market.get("volume_fp")),
                _to_float(market.get("open_interest_fp")),
            )
        )

    connection.executemany(
        """
        INSERT INTO markets (
            ticker, series_ticker, event_ticker, title, status, result, market_type,
            open_time, close_time, expected_expiration_time, settlement_ts, updated_time,
            yes_sub_title, no_sub_title, reference_price, final_price, last_price,
            yes_bid, yes_ask, no_bid, no_ask, volume, open_interest
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            status=excluded.status,
            result=excluded.result,
            expected_expiration_time=excluded.expected_expiration_time,
            settlement_ts=excluded.settlement_ts,
            updated_time=excluded.updated_time,
            yes_sub_title=excluded.yes_sub_title,
            no_sub_title=excluded.no_sub_title,
            reference_price=COALESCE(excluded.reference_price, markets.reference_price),
            final_price=COALESCE(excluded.final_price, markets.final_price),
            last_price=COALESCE(excluded.last_price, markets.last_price),
            yes_bid=COALESCE(excluded.yes_bid, markets.yes_bid),
            yes_ask=COALESCE(excluded.yes_ask, markets.yes_ask),
            no_bid=COALESCE(excluded.no_bid, markets.no_bid),
            no_ask=COALESCE(excluded.no_ask, markets.no_ask),
            volume=COALESCE(excluded.volume, markets.volume),
            open_interest=COALESCE(excluded.open_interest, markets.open_interest)
        """,
        rows,
    )
    connection.commit()
    return len(rows)


def upsert_candles(
    connection: sqlite3.Connection,
    market_ticker: str,
    candles: Iterable[dict[str, object]],
    period_interval: int,
    source: str,
) -> int:
    rows = []
    for candle in candles:
        price = candle.get("price", {})
        yes_bid = candle.get("yes_bid", {})
        yes_ask = candle.get("yes_ask", {})
        required = (
            _to_float(price.get("open")),
            _to_float(price.get("close")),
            _to_float(price.get("high")),
            _to_float(price.get("low")),
        )
        if any(value is None for value in required):
            continue
        rows.append(
            (
                market_ticker,
                int(candle["end_period_ts"]),
                period_interval,
                source,
                float(required[0]),
                float(required[1]),
                float(required[2]),
                float(required[3]),
                _to_float(price.get("mean")),
                _to_float(price.get("previous")),
                _to_float(yes_bid.get("open")),
                _to_float(yes_bid.get("close")),
                _to_float(yes_bid.get("high")),
                _to_float(yes_bid.get("low")),
                _to_float(yes_ask.get("open")),
                _to_float(yes_ask.get("close")),
                _to_float(yes_ask.get("high")),
                _to_float(yes_ask.get("low")),
                _to_float(candle.get("volume")),
                _to_float(candle.get("open_interest")),
            )
        )
    connection.executemany(
        """
        INSERT OR REPLACE INTO candles (
            market_ticker, end_period_ts, period_interval, source,
            price_open, price_close, price_high, price_low, price_mean, price_previous,
            yes_bid_open, yes_bid_close, yes_bid_high, yes_bid_low,
            yes_ask_open, yes_ask_close, yes_ask_high, yes_ask_low,
            volume, open_interest
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    connection.commit()
    return len(rows)


def upsert_trades(connection: sqlite3.Connection, market_ticker: str, trades: Iterable[dict[str, object]]) -> int:
    rows = [
        (
            trade["trade_id"],
            market_ticker,
            trade["created_time"],
            float(trade["yes_price_dollars"]),
            float(trade["no_price_dollars"]),
            _to_float(trade.get("count_fp")),
            trade.get("taker_side"),
            trade.get("taker_book_side"),
            trade.get("taker_outcome_side"),
        )
        for trade in trades
    ]
    connection.executemany(
        """
        INSERT OR REPLACE INTO trades (
            trade_id, market_ticker, created_time, yes_price, no_price, count,
            taker_side, taker_book_side, taker_outcome_side
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    connection.commit()
    return len(rows)


def insert_snapshot(connection: sqlite3.Connection, market: dict[str, object], collected_at: str) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO quote_snapshots (
            market_ticker, collected_at, status, yes_bid, yes_ask, no_bid, no_ask,
            last_price, volume, open_interest
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            market["ticker"],
            collected_at,
            str(market.get("status") or ""),
            float(str(market.get("yes_bid_dollars") or 0)),
            float(str(market.get("yes_ask_dollars") or 0)),
            float(str(market.get("no_bid_dollars") or 0)),
            float(str(market.get("no_ask_dollars") or 0)),
            _to_float(market.get("last_price_dollars")),
            _to_float(market.get("volume_fp")),
            _to_float(market.get("open_interest_fp")),
        ),
    )
    connection.commit()


def backfill_history(connection: sqlite3.Connection, client: KalshiClient, series_ticker: str) -> dict[str, int]:
    historical_markets = client.get_historical_markets(series_ticker)
    recent_markets = client.get_recent_markets(series_ticker)
    market_rows = upsert_markets(connection, historical_markets + recent_markets, series_ticker)
    candle_rows = 0
    trade_rows = 0

    for market in historical_markets:
        if market.get("open_time") and market.get("close_time"):
            candles = client.get_historical_candles(
                str(market["ticker"]),
                str(market["open_time"]),
                str(market["close_time"]),
                period_interval=1,
            )
            candle_rows += upsert_candles(connection, str(market["ticker"]), candles, 1, "historical")
        trades = client.get_trades(str(market["ticker"]), historical=True)
        trade_rows += upsert_trades(connection, str(market["ticker"]), trades)

    for market in recent_markets:
        if market.get("status") in {"finalized", "settled"}:
            trades = client.get_trades(str(market["ticker"]), historical=False)
            trade_rows += upsert_trades(connection, str(market["ticker"]), trades)

    return {"markets": market_rows, "candles": candle_rows, "trades": trade_rows}


def sync_live(connection: sqlite3.Connection, client: KalshiClient, series_ticker: str) -> dict[str, int]:
    active_markets = client.get_active_markets(series_ticker)
    market_rows = upsert_markets(connection, active_markets, series_ticker)
    collected_at = client.iso_now()
    for market in active_markets:
        insert_snapshot(connection, market, collected_at)
    return {"markets": market_rows, "snapshots": len(active_markets)}


def _iso_to_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def recent_settled_markets(
    connection: sqlite3.Connection, series_ticker: str, days: int
) -> list[sqlite3.Row]:
    threshold = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    return connection.execute(
        """
        SELECT ticker, open_time
        FROM markets
        WHERE series_ticker = ?
          AND result IN ('yes', 'no')
          AND open_time >= ?
        ORDER BY open_time DESC
        """,
        (series_ticker, threshold),
    ).fetchall()


def backfill_recent_trade_history(
    connection: sqlite3.Connection,
    client: KalshiClient,
    series_ticker: str,
    days: int,
    workers: int = 4,
    max_pages: int | None = None,
) -> dict[str, int]:
    markets = recent_settled_markets(connection, series_ticker, days)
    total_markets = 0
    total_trades = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(client.get_recent_trades_pages, row["ticker"], 1000, max_pages): row["ticker"]
            for row in markets
        }
        for future in as_completed(future_map):
            ticker = future_map[future]
            trades = future.result()
            if not trades:
                continue
            total_markets += 1
            total_trades += upsert_trades(connection, ticker, trades)
            print(f"Imported {len(trades)} recent trades for {ticker}")
    return {"markets": total_markets, "trades": total_trades}
