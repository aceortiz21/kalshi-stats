from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from io import BytesIO, TextIOWrapper
import sqlite3
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zipfile import ZipFile


BINANCE_DAILY_TEMPLATE = (
    "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1s/BTCUSDT-1s-{day}.zip"
)


def _daterange(start_day: date, end_day: date) -> list[date]:
    days: list[date] = []
    current = start_day
    while current <= end_day:
        days.append(current)
        current += timedelta(days=1)
    return days


def _download_day(day: date) -> list[tuple[int, str, float, float, float, float, float]]:
    url = BINANCE_DAILY_TEMPLATE.format(day=day.isoformat())
    try:
        with urlopen(url, timeout=120) as response:
            payload = response.read()
    except HTTPError as error:
        if error.code == 404:
            return []
        raise
    rows: list[tuple[int, str, float, float, float, float, float]] = []
    with ZipFile(BytesIO(payload)) as archive:
        name = archive.namelist()[0]
        with archive.open(name) as handle:
            reader = csv.reader(TextIOWrapper(handle, encoding="utf-8"))
            for row in reader:
                open_time_ms = int(row[0])
                rows.append(
                    (
                        open_time_ms,
                        "binance_1s",
                        float(row[1]),
                        float(row[2]),
                        float(row[3]),
                        float(row[4]),
                        float(row[5]),
                    )
                )
    return rows


def backfill_binance_1s(
    connection: sqlite3.Connection,
    start_day: date,
    end_day: date,
    workers: int = 4,
) -> dict[str, int]:
    days = _daterange(start_day, end_day)
    inserted = 0
    succeeded = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(_download_day, day): day for day in days}
        for future in as_completed(future_map):
            day = future_map[future]
            rows = future.result()
            if not rows:
                print(f"No BTC 1s archive published for {day.isoformat()}")
                continue
            connection.executemany(
                """
                INSERT OR REPLACE INTO btc_1s (ts, source, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            connection.commit()
            inserted += len(rows)
            succeeded += 1
            print(f"Imported BTC 1s archive for {day.isoformat()} with {len(rows)} rows")
    return {"days": succeeded, "rows": inserted}


def sync_latest_coinbase_second(connection: sqlite3.Connection) -> int:
    ticker_url = "https://api.exchange.coinbase.com/products/BTC-USD/ticker"
    request = Request(
        ticker_url,
        headers={
            "User-Agent": "kalshi-stats/0.1",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=30) as response:
        data = __import__("json").load(response)
    ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    price = float(data["price"])
    bid = float(data["bid"])
    ask = float(data["ask"])
    rows = [
        (
            ts,
            "coinbase_ticker_1s",
            price,
            max(price, bid, ask),
            min(price, bid, ask),
            price,
            float(data.get("volume", 0.0)),
        )
    ]
    connection.executemany(
        """
        INSERT OR REPLACE INTO btc_1s (ts, source, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    connection.commit()
    return len(rows)
