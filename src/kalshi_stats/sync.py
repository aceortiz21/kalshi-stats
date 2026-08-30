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
            _to_float(price.get("open_dollars") or price.get("open")),
            _to_float(price.get("close_dollars") or price.get("close")),
            _to_float(price.get("high_dollars") or price.get("high")),
            _to_float(price.get("low_dollars") or price.get("low")),
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
                _to_float(price.get("mean_dollars") or price.get("mean")),
                _to_float(price.get("previous_dollars") or price.get("previous")),
                _to_float(yes_bid.get("open_dollars") or yes_bid.get("open")),
                _to_float(yes_bid.get("close_dollars") or yes_bid.get("close")),
                _to_float(yes_bid.get("high_dollars") or yes_bid.get("high")),
                _to_float(yes_bid.get("low_dollars") or yes_bid.get("low")),
                _to_float(yes_ask.get("open_dollars") or yes_ask.get("open")),
                _to_float(yes_ask.get("close_dollars") or yes_ask.get("close")),
                _to_float(yes_ask.get("high_dollars") or yes_ask.get("high")),
                _to_float(yes_ask.get("low_dollars") or yes_ask.get("low")),
                _to_float(candle.get("volume_fp") or candle.get("volume")),
                _to_float(candle.get("open_interest_fp") or candle.get("open_interest")),
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



def backfill_missing_historical_trades(
    connection: sqlite3.Connection,
    client: KalshiClient,
    series_ticker: str,
    workers: int = 8,
    limit_markets: int | None = None,
) -> dict[str, int]:
    markets = connection.execute(
        """
        SELECT m.ticker
        FROM markets m
        WHERE m.series_ticker = ?
          AND m.result IN ('yes', 'no')
          AND NOT EXISTS (
              SELECT 1
              FROM trades t
              WHERE t.market_ticker = m.ticker
          )
        ORDER BY m.open_time DESC
        """,
        (series_ticker,),
    ).fetchall()

    tickers = [str(row["ticker"]) for row in markets]

    if limit_markets is not None:
        tickers = tickers[:limit_markets]

    attempted = 0
    imported_markets = 0
    empty_markets = 0
    failed_markets = 0
    total_trades = 0

    def fetch_one(ticker: str) -> tuple[str, list[dict[str, object]]]:
        trades = client.get_trades(ticker, historical=True)
        return ticker, trades

    print(f"Historical markets missing trades: {len(tickers)}")
    print(f"Workers: {workers}")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(fetch_one, ticker): ticker
            for ticker in tickers
        }

        for future in as_completed(future_map):
            ticker = future_map[future]
            attempted += 1

            try:
                _, trades = future.result()
            except Exception as exc:
                failed_markets += 1
                print(
                    f"[{attempted}/{len(tickers)}] ERROR {ticker}: "
                    f"{type(exc).__name__}: {exc}"
                )
                continue

            if not trades:
                empty_markets += 1
                print(
                    f"[{attempted}/{len(tickers)}] EMPTY {ticker}"
                )
                continue

            try:
                inserted = upsert_trades(connection, ticker, trades)
            except Exception as exc:
                failed_markets += 1
                print(
                    f"[{attempted}/{len(tickers)}] DB ERROR {ticker}: "
                    f"{type(exc).__name__}: {exc}"
                )
                continue

            imported_markets += 1
            total_trades += inserted

            print(
                f"[{attempted}/{len(tickers)}] "
                f"IMPORTED {ticker}: {inserted} trades "
                f"| covered={imported_markets} "
                f"| empty={empty_markets} "
                f"| errors={failed_markets}"
            )

    return {
        "attempted": attempted,
        "markets": imported_markets,
        "trades": total_trades,
        "empty": empty_markets,
        "errors": failed_markets,
    }




def backfill_recent_candles(
    connection: sqlite3.Connection,
    client: KalshiClient,
    series_ticker: str,
    start_date: str,
    end_date: str,
    batch_size: int = 96,
    limit_markets: int | None = None,
) -> dict[str, int]:
    if batch_size < 1 or batch_size > 100:
        raise ValueError("batch_size must be between 1 and 100")

    start_dt = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
    end_dt = (
        datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)
        + timedelta(days=1)
    )

    rows = connection.execute(
        """
        SELECT m.ticker, m.open_time, m.close_time
        FROM markets m
        WHERE m.series_ticker = ?
          AND m.result IN ('yes', 'no')
          AND m.open_time >= ?
          AND m.open_time < ?
          AND NOT EXISTS (
              SELECT 1
              FROM candles c
              WHERE c.market_ticker = m.ticker
                AND c.period_interval = 1
          )
        ORDER BY m.open_time
        """,
        (
            series_ticker,
            start_dt.isoformat().replace("+00:00", "Z"),
            end_dt.isoformat().replace("+00:00", "Z"),
        ),
    ).fetchall()

    if limit_markets is not None:
        rows = rows[:limit_markets]

    total = len(rows)
    imported_markets = 0
    candle_rows = 0
    empty_markets = 0
    failed_batches = 0

    print(f"Markets missing 1m candles: {total}")
    print(f"Batch size: {batch_size}")

    def import_batch(batch, label: str) -> None:
        nonlocal imported_markets
        nonlocal candle_rows
        nonlocal empty_markets
        nonlocal failed_batches

        if not batch:
            return

        tickers = [str(row["ticker"]) for row in batch]

        start_ts = min(
            int(_iso_to_dt(str(row["open_time"])).timestamp())
            for row in batch
        )

        end_ts = max(
            int(_iso_to_dt(str(row["close_time"])).timestamp()) + 60
            for row in batch
        )

        try:
            groups = client.get_batch_candles(
                tickers,
                start_ts,
                end_ts,
                period_interval=1,
            )
        except Exception as exc:
            # A batch can occasionally contain a ticker/time combination that
            # the API rejects. Split recursively so one bad market does not
            # prevent the rest of the historical backfill.
            if len(batch) > 1:
                midpoint = len(batch) // 2

                print(
                    f"[{label}] ERROR with {len(batch)} markets: "
                    f"{type(exc).__name__}: {exc} -- splitting"
                )

                import_batch(batch[:midpoint], f"{label}.A")
                import_batch(batch[midpoint:], f"{label}.B")
                return

            failed_batches += 1
            print(
                f"[{label}] SKIP {tickers[0]}: "
                f"{type(exc).__name__}: {exc}"
            )
            return

        returned = set()

        for group in groups:
            ticker = str(group.get("market_ticker") or "")
            candles = group.get("candlesticks") or []

            if not ticker or ticker not in tickers:
                continue

            returned.add(ticker)

            if not candles:
                empty_markets += 1
                print(f"[{label}] EMPTY {ticker}")
                continue

            inserted = upsert_candles(
                connection,
                ticker,
                candles,
                1,
                "recent_batch",
            )

            if inserted:
                imported_markets += 1
                candle_rows += inserted
            else:
                empty_markets += 1

        missing = set(tickers) - returned

        if missing:
            empty_markets += len(missing)
            for ticker in sorted(missing):
                print(f"[{label}] NOT RETURNED {ticker}")

        print(
            f"[{label}] "
            f"requested={len(tickers)} "
            f"returned={len(returned)} "
            f"covered={imported_markets}/{total} "
            f"candles={candle_rows} "
            f"empty={empty_markets} "
            f"errors={failed_batches}"
        )

    batch_total = (total + batch_size - 1) // batch_size

    for offset in range(0, total, batch_size):
        batch = rows[offset : offset + batch_size]
        batch_number = offset // batch_size + 1
        import_batch(batch, f"batch {batch_number}/{batch_total}")

    return {
        "requested_markets": total,
        "markets": imported_markets,
        "candles": candle_rows,
        "empty": empty_markets,
        "errors": failed_batches,
    }




def insert_ws_quote_snapshot(
    connection: sqlite3.Connection,
    *,
    market_ticker: str,
    yes_bid: float,
    yes_ask: float,
    last_price: float | None,
    volume: float | None,
    open_interest: float | None,
    ts_ms: int,
) -> None:
    """Persist one downsampled WebSocket quote observation."""

    collected_at = (
        datetime.fromtimestamp(
            ts_ms / 1000.0,
            tz=timezone.utc,
        )
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )

    no_bid = max(
        0.0,
        min(1.0, 1.0 - yes_ask),
    )

    no_ask = max(
        0.0,
        min(1.0, 1.0 - yes_bid),
    )

    connection.execute(
        """
        INSERT OR REPLACE INTO quote_snapshots (
            market_ticker,
            collected_at,
            status,
            yes_bid,
            yes_ask,
            no_bid,
            no_ask,
            last_price,
            volume,
            open_interest
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            market_ticker,
            collected_at,
            "active",
            yes_bid,
            yes_ask,
            no_bid,
            no_ask,
            last_price,
            volume,
            open_interest,
        ),
    )

    connection.commit()





def sync_recent_market_metadata(
    connection: sqlite3.Connection,
    client: KalshiClient,
    series_ticker: str,
) -> int:
    """Refresh recent market metadata.

    This lets a restarted monitor discover contracts that opened
    and closed while the program was not running.
    """

    markets = client.get_recent_markets(
        series_ticker
    )

    if not markets:
        return 0

    return upsert_markets(
        connection,
        markets,
        series_ticker,
    )


def discover_pending_finalizations(
    connection: sqlite3.Connection,
    series_ticker: str,
    lookback_hours: int = 72,
) -> list[str]:
    """Find recent closed markets that still need settlement data."""

    now = datetime.now(timezone.utc)

    threshold = (
        now - timedelta(hours=lookback_hours)
    )

    now_text = (
        now.isoformat()
        .replace("+00:00", "Z")
    )

    threshold_text = (
        threshold.isoformat()
        .replace("+00:00", "Z")
    )

    rows = connection.execute(
        """
        SELECT m.ticker
        FROM markets m
        WHERE m.series_ticker = ?
          AND m.close_time IS NOT NULL
          AND m.close_time < ?
          AND m.close_time >= ?
          AND (
              LOWER(COALESCE(m.result, ''))
                  NOT IN ('yes', 'no')
              OR (
                  SELECT COUNT(*)
                  FROM candles c
                  WHERE c.market_ticker = m.ticker
                    AND c.period_interval = 1
              ) < 14
          )
        ORDER BY m.close_time
        """,
        (
            series_ticker,
            now_text,
            threshold_text,
        ),
    ).fetchall()

    return [
        str(row["ticker"])
        for row in rows
    ]



def finalize_market_data(
    connection: sqlite3.Connection,
    client: KalshiClient,
    *,
    series_ticker: str,
    market_ticker: str,
) -> dict[str, object]:
    """Ingest a market after it closes.

    Safe to call repeatedly. Database upserts make retries
    idempotent.
    """

    market = client.get_market(
        market_ticker
    )

    upsert_markets(
        connection,
        [market],
        series_ticker,
    )

    result = str(
        market.get("result") or ""
    ).lower()

    status = str(
        market.get("status") or ""
    ).lower()

    if result not in {"yes", "no"}:
        return {
            "market_ticker": market_ticker,
            "settled": False,
            "status": status,
            "result": result or None,
            "candles": 0,
            "trades": 0,
        }

    open_time = str(
        market.get("open_time") or ""
    )

    close_time = str(
        market.get("close_time") or ""
    )

    if not open_time or not close_time:
        raise RuntimeError(
            f"{market_ticker} is missing "
            "open_time/close_time"
        )

    candles = []

    # A newly settled market should normally still be on the
    # current candlestick endpoint. If Kalshi has already moved
    # it across the historical cutoff, fall back automatically.
    try:
        candles = client.get_market_candles(
            series_ticker,
            market_ticker,
            open_time,
            close_time,
            period_interval=1,
        )

    except Exception as recent_error:
        try:
            candles = client.get_historical_candles(
                market_ticker,
                open_time,
                close_time,
                period_interval=1,
            )

        except Exception as historical_error:
            raise RuntimeError(
                "Both recent and historical candle "
                f"fetches failed for {market_ticker}. "
                f"Recent: {recent_error}. "
                f"Historical: {historical_error}"
            ) from historical_error

    candle_rows = 0

    if candles:
        candle_rows = upsert_candles(
            connection,
            market_ticker,
            candles,
            1,
            "auto_finalize",
        )

    # Do not bulk-download every public trade during live finalization.
    # Settlement + official 1m candles are sufficient for the historical
    # model, while our live WebSocket snapshots provide higher-resolution
    # observations for newly collected markets. Raw public trades remain
    # available through KalshiClient.get_trades() for selective research.
    trade_rows = 0

    total_candles = connection.execute(
        """
        SELECT COUNT(*)
        FROM candles
        WHERE market_ticker = ?
          AND period_interval = 1
        """,
        (market_ticker,),
    ).fetchone()[0]

    total_trades = connection.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE market_ticker = ?
        """,
        (market_ticker,),
    ).fetchone()[0]

    total_snapshots = connection.execute(
        """
        SELECT COUNT(*)
        FROM quote_snapshots
        WHERE market_ticker = ?
        """,
        (market_ticker,),
    ).fetchone()[0]

    return {
        "market_ticker": market_ticker,
        "settled": True,
        "status": status,
        "result": result,
        "candles": int(total_candles),
        "new_candles": int(candle_rows),
        "trades": int(total_trades),
        "new_trades": int(trade_rows),
        "snapshots": int(total_snapshots),

        # We prefer the completed candle path before declaring
        # the market fully ingested. Snapshot data remains useful
        # even if trades happen to be unavailable.
        "complete": int(total_candles) >= 14,
    }



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
