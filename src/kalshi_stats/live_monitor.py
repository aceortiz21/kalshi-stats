from __future__ import annotations

import asyncio
import time
from pathlib import Path

from .kalshi_ws import KalshiTickerWebSocket
from .live import (
    build_live_side_views,
    select_current_market,
)
from .reporting import render_live_market_fragment
from .sync import (
    insert_ws_quote_snapshot,
    sync_live,
)


async def run_websocket_live_loop(
    *,
    connection,
    client,
    cache,
    output_path: str,
    series_ticker: str,
) -> None:
    live_fragment_path = Path(output_path).with_name(
        "live_market.html"
    )

    websocket_client = (
        KalshiTickerWebSocket.from_env()
    )

    previous_market: str | None = None

    while True:
        markets = client.get_active_markets(
            series_ticker
        )

        market = select_current_market(markets)

        if market is None:
            render_live_market_fragment(
                live_fragment_path,
                [],
                cache["validated_strategies"],
            )

            if previous_market is not None:
                print(
                    "Waiting for next KXBTC15M market..."
                )

            previous_market = None
            await asyncio.sleep(0.5)
            continue

        ticker = str(market["ticker"])

        # Store/update market metadata when a new 15-minute
        # contract becomes current. This is not in the
        # high-frequency quote path.
        if ticker != previous_market:
            sync_live(
                connection,
                client,
                series_ticker,
            )

            previous_market = ticker

            print()
            print("Current market:", ticker)

        yes_bid = float(
            market.get("yes_bid_dollars") or 0
        )

        yes_ask = float(
            market.get("yes_ask_dollars") or 0
        )

        quote_ts_ms = int(time.time() * 1000)

        views = build_live_side_views(
            market=market,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            matrix=cache["matrix"],
            quote_ts_ms=quote_ts_ms,
        )

        render_live_market_fragment(
            live_fragment_path,
            views,
            cache["validated_strategies"],
        )

        close_ts = (
            views[0].close_ts
            if views
            else int(time.time())
        )

        reconnect_delay = 0.5

        while time.time() < close_ts:
            websocket = None

            try:
                websocket = (
                    await websocket_client.open(ticker)
                )

                print(
                    "WebSocket subscribed:",
                    ticker,
                )

                reconnect_delay = 0.5

                last_signature = None
                last_log_time = 0.0
                last_saved_second = None
                captured_quotes = 0

                while time.time() < close_ts:
                    ticker_update = None

                    try:
                        raw = await asyncio.wait_for(
                            websocket.recv(),
                            timeout=0.25,
                        )

                        ticker_update = (
                            websocket_client.parse_ticker(
                                raw,
                                ticker,
                            )
                        )

                    except asyncio.TimeoutError:
                        pass

                    if ticker_update is not None:
                        yes_bid = (
                            ticker_update.yes_bid
                        )
                        yes_ask = (
                            ticker_update.yes_ask
                        )
                        quote_ts_ms = (
                            ticker_update.ts_ms
                        )

                        event_second = (
                            ticker_update.ts_ms // 1000
                        )

                        # Maximum one stored observation per
                        # second. Rapid intra-second events still
                        # drive the dashboard immediately but do
                        # not explode the historical database.
                        if (
                            event_second
                            != last_saved_second
                        ):
                            insert_ws_quote_snapshot(
                                connection,
                                market_ticker=ticker,
                                yes_bid=yes_bid,
                                yes_ask=yes_ask,
                                last_price=(
                                    ticker_update.last_price
                                ),
                                volume=(
                                    ticker_update.volume
                                ),
                                open_interest=(
                                    ticker_update.open_interest
                                ),
                                ts_ms=ticker_update.ts_ms,
                            )

                            last_saved_second = (
                                event_second
                            )

                            captured_quotes += 1

                    # This lookup is deliberately cheap. It lets
                    # state/strategy changes happen immediately
                    # on price moves and at time-bucket boundaries.
                    views = build_live_side_views(
                        market=market,
                        yes_bid=yes_bid,
                        yes_ask=yes_ask,
                        matrix=cache["matrix"],
                        quote_ts_ms=quote_ts_ms,
                    )

                    state_signature = (
                        round(yes_bid, 4),
                        round(yes_ask, 4),
                        tuple(
                            (
                                view.side,
                                view.price_bucket,
                                view.time_bucket,
                            )
                            for view in views
                        ),
                    )

                    if state_signature != last_signature:
                        render_live_market_fragment(
                            live_fragment_path,
                            views,
                            cache["validated_strategies"],
                        )

                        last_signature = (
                            state_signature
                        )

                    if ticker_update is not None:
                        now = time.perf_counter()

                        if now - last_log_time >= 1.0:
                            no_bid = 1.0 - yes_ask
                            no_ask = 1.0 - yes_bid

                            message_age_ms = max(
                                0,
                                int(
                                    time.time() * 1000
                                    - quote_ts_ms
                                ),
                            )

                            print(
                                "WS live | "
                                f"YES "
                                f"{yes_bid*100:.1f}/"
                                f"{yes_ask*100:.1f}c | "
                                f"NO "
                                f"{no_bid*100:.1f}/"
                                f"{no_ask*100:.1f}c | "
                                f"event latency "
                                f"{message_age_ms}ms | "
                                f"saved {captured_quotes}"
                            )

                            last_log_time = now

                break

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                print(
                    "WebSocket disconnected:",
                    f"{type(exc).__name__}: {exc}",
                )

                print(
                    "Reconnecting in "
                    f"{reconnect_delay:.1f}s..."
                )

                await asyncio.sleep(
                    reconnect_delay
                )

                reconnect_delay = min(
                    reconnect_delay * 2,
                    10.0,
                )

            finally:
                if websocket is not None:
                    try:
                        await websocket.close()
                    except Exception:
                        pass

        # The old market expired. Immediately discover the
        # next 15-minute contract and establish a new stream.
        previous_market = None
        await asyncio.sleep(0.05)
