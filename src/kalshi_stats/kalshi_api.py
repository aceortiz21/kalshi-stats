from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen


class KalshiClient:
    def __init__(self, base_url: str = "https://external-api.kalshi.com/trade-api/v2") -> None:
        self.base_url = base_url.rstrip("/")

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = f"?{urlencode(params)}" if params else ""
        url = f"{self.base_url}{path}{query}"
        with urlopen(url, timeout=30) as response:
            return json.load(response)

    def iter_markets(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        cursor: str | None = None
        results: list[dict[str, Any]] = []
        while True:
            current_params = dict(params)
            if cursor:
                current_params["cursor"] = cursor
            data = self.get_json(path, current_params)
            results.extend(data.get("markets", []))
            cursor = data.get("cursor")
            if not cursor:
                break
        return results

    def get_historical_markets(self, series_ticker: str, limit: int = 1000) -> list[dict[str, Any]]:
        return self.iter_markets("/historical/markets", {"series_ticker": series_ticker, "limit": limit})

    def get_recent_markets(self, series_ticker: str, limit: int = 200) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        earliest = now - timedelta(hours=72)
        latest = now + timedelta(hours=8)
        cursor: str | None = None
        results: list[dict[str, Any]] = []
        for _ in range(8):
            params: dict[str, Any] = {"series_ticker": series_ticker, "limit": limit}
            if cursor:
                params["cursor"] = cursor
            data = self.get_json("/markets", params)
            page_markets = data.get("markets", [])
            if not page_markets:
                break
            for market in page_markets:
                open_time = datetime.fromisoformat(str(market["open_time"]).replace("Z", "+00:00"))
                if earliest <= open_time <= latest:
                    results.append(market)
            oldest_open = datetime.fromisoformat(str(page_markets[-1]["open_time"]).replace("Z", "+00:00"))
            if oldest_open < earliest:
                break
            cursor = data.get("cursor")
            if not cursor:
                break
        return results

    def get_batch_candles(
        self,
        market_tickers: list[str],
        start_ts: int,
        end_ts: int,
        period_interval: int = 1,
    ) -> list[dict[str, Any]]:
        if not market_tickers:
            return []

        data = self.get_json(
            "/markets/candlesticks",
            {
                "market_tickers": ",".join(market_tickers),
                "start_ts": start_ts,
                "end_ts": end_ts,
                "period_interval": period_interval,
            },
        )
        return data.get("markets", [])

    def get_historical_candles(
        self, market_ticker: str, open_time: str, close_time: str, period_interval: int = 1
    ) -> list[dict[str, Any]]:
        start_ts = int(datetime.fromisoformat(open_time.replace("Z", "+00:00")).timestamp())
        end_dt = datetime.fromisoformat(close_time.replace("Z", "+00:00")) + timedelta(minutes=1)
        end_ts = int(end_dt.timestamp())
        data = self.get_json(
            f"/historical/markets/{market_ticker}/candlesticks",
            {"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_interval},
        )
        return data.get("candlesticks", [])

    def get_trades(self, market_ticker: str, historical: bool, limit: int = 1000) -> list[dict[str, Any]]:
        path = "/historical/trades" if historical else "/markets/trades"
        cursor: str | None = None
        trades: list[dict[str, Any]] = []
        while True:
            params: dict[str, Any] = {"ticker": market_ticker, "limit": limit}
            if cursor:
                params["cursor"] = cursor
            data = self.get_json(path, params)
            trades.extend(data.get("trades", []))
            cursor = data.get("cursor")
            if not cursor:
                break
        return trades

    def get_recent_trades_pages(
        self, market_ticker: str, page_limit: int = 1000, max_pages: int | None = None
    ) -> list[dict[str, Any]]:
        cursor: str | None = None
        trades: list[dict[str, Any]] = []
        pages = 0
        while True:
            params: dict[str, Any] = {"ticker": market_ticker, "limit": page_limit}
            if cursor:
                params["cursor"] = cursor
            data = self.get_json("/markets/trades", params)
            current_trades = data.get("trades", [])
            trades.extend(current_trades)
            pages += 1
            cursor = data.get("cursor")
            if not cursor or not current_trades:
                break
            if max_pages is not None and pages >= max_pages:
                break
        return trades

    def get_market(
        self,
        ticker: str,
    ) -> dict[str, Any]:
        """Return one market by ticker."""

        data = self.get_json(
            f"/markets/{ticker}"
        )

        return data["market"]


    def get_market_candles(
        self,
        series_ticker: str,
        market_ticker: str,
        open_time: str,
        close_time: str,
        period_interval: int = 1,
    ) -> list[dict[str, Any]]:
        """Fetch recent/non-archived candles for one market."""

        start_ts = int(
            datetime.fromisoformat(
                open_time.replace("Z", "+00:00")
            ).timestamp()
        )

        close_dt = datetime.fromisoformat(
            close_time.replace("Z", "+00:00")
        )

        # Include the final one-minute candle boundary.
        end_ts = int(
            (
                close_dt
                + timedelta(minutes=1)
            ).timestamp()
        )

        data = self.get_json(
            (
                f"/series/{series_ticker}"
                f"/markets/{market_ticker}"
                f"/candlesticks"
            ),
            {
                "start_ts": start_ts,
                "end_ts": end_ts,
                "period_interval": period_interval,
            },
        )

        return data.get(
            "candlesticks",
            [],
        )


    def get_active_markets(
        self,
        series_ticker: str,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Return all currently open markets for a series.

        The markets endpoint is paginated. Filtering by status=open before
        pagination prevents scheduled/settled markets from crowding the
        current KXBTC15M contract out of the first response page.
        """
        page_limit = max(1, min(limit, 1000))

        return self.iter_markets(
            "/markets",
            {
                "series_ticker": series_ticker,
                "status": "open",
                "limit": page_limit,
            },
        )

    @staticmethod
    def iso_now() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
