from __future__ import annotations

import time
from datetime import datetime

from .analytics import (
    MATRIX_TIME_BUCKETS,
    PRICE_BUCKETS,
)
from .models import (
    ActiveMarketSideView,
    MatrixCell,
)


def _iso_timestamp(value: str) -> float:
    return datetime.fromisoformat(
        value.replace("Z", "+00:00")
    ).timestamp()


def select_current_market(
    markets: list[dict],
    now_ts: float | None = None,
) -> dict | None:
    now_ts = time.time() if now_ts is None else now_ts

    for market in markets:
        open_time = market.get("open_time")
        close_time = market.get("close_time")

        if not open_time or not close_time:
            continue

        open_ts = _iso_timestamp(str(open_time))
        close_ts = _iso_timestamp(str(close_time))

        if open_ts <= now_ts < close_ts:
            return market

    return None


def _midpoint(
    bid: float,
    ask: float,
) -> float:
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0

    if ask > 0:
        return ask

    return bid


def _price_bucket(price: float) -> str:
    # Convert to integer cents first so sub-cent midpoints such
    # as 9.5c still belong to the intuitive 0-9c bucket.
    cents = int(
        max(0.0, min(1.0, price)) * 100
    )

    for low, high, label in PRICE_BUCKETS:
        low_cents = int(round(low * 100))
        high_cents = int(round(high * 100))

        if low_cents <= cents <= high_cents:
            return label

    return "unknown"


def _time_bucket(seconds_remaining: int) -> str:
    value = max(0, min(900, seconds_remaining))

    for low, high, label in MATRIX_TIME_BUCKETS:
        if low <= value <= high:
            return label

    return "unknown"


def build_live_side_views(
    *,
    market: dict,
    yes_bid: float,
    yes_ask: float,
    matrix: list[MatrixCell],
    quote_ts_ms: int | None = None,
    now_ts: float | None = None,
) -> list[ActiveMarketSideView]:
    now_ts = time.time() if now_ts is None else now_ts

    ticker = str(market["ticker"])
    status = str(market.get("status") or "active")

    close_ts = int(
        _iso_timestamp(str(market["close_time"]))
    )

    seconds_remaining = max(
        0,
        int(close_ts - now_ts),
    )

    yes_mid = _midpoint(yes_bid, yes_ask)

    no_bid = max(0.0, 1.0 - yes_ask)
    no_ask = min(1.0, 1.0 - yes_bid)
    no_mid = _midpoint(no_bid, no_ask)

    matrix_lookup = {
        (cell.price_bucket, cell.time_bucket): cell
        for cell in matrix
    }

    quote_ts_ms = (
        int(time.time() * 1000)
        if quote_ts_ms is None
        else quote_ts_ms
    )

    views: list[ActiveMarketSideView] = []

    for (
        side,
        current_price,
        bid_price,
        ask_price,
    ) in (
        ("yes", yes_mid, yes_bid, yes_ask),
        ("no", no_mid, no_bid, no_ask),
    ):
        price_bucket = _price_bucket(current_price)
        time_bucket = _time_bucket(seconds_remaining)

        cell = matrix_lookup.get(
            (price_bucket, time_bucket)
        )

        views.append(
            ActiveMarketSideView(
                market_ticker=ticker,
                market_status=status,
                side=side,
                current_price=current_price,
                seconds_remaining=seconds_remaining,
                price_bucket=price_bucket,
                time_bucket=time_bucket,
                observations=(
                    0 if cell is None else cell.observations
                ),
                win_rate=(
                    None if cell is None else cell.win_rate
                ),
                plus_5c_rate=(
                    None
                    if cell is None
                    else cell.plus_5c_rate
                ),
                plus_10c_rate=(
                    None
                    if cell is None
                    else cell.plus_10c_rate
                ),
                plus_15c_rate=(
                    None
                    if cell is None
                    else cell.plus_15c_rate
                ),
                plus_20c_rate=(
                    None
                    if cell is None
                    else cell.plus_20c_rate
                ),
                touch_30_rate=(
                    None
                    if cell is None
                    else cell.touch_30_rate
                ),
                touch_35_rate=(
                    None
                    if cell is None
                    else cell.touch_35_rate
                ),
                touch_40_rate=(
                    None
                    if cell is None
                    else cell.touch_40_rate
                ),
                touch_50_rate=(
                    None
                    if cell is None
                    else cell.touch_50_rate
                ),
                avg_best_subsequent_price=(
                    None
                    if cell is None
                    else cell.avg_best_subsequent_price
                ),
                median_best_subsequent_price=(
                    None
                    if cell is None
                    else cell.median_best_subsequent_price
                ),
                matched_scenarios=[],
                bid_price=bid_price,
                ask_price=ask_price,
                close_ts=close_ts,
                quote_ts_ms=quote_ts_ms,
            )
        )

    return views
