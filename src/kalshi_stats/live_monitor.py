from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

from .dashboard_cache import (
    count_model_pending_markets,
    load_cached_historical_cache,
)
from .kalshi_ws import KalshiTickerWebSocket
from .live import (
    build_live_side_views,
    select_current_market,
)
from .reporting import render_live_market_fragment
from .sync import (
    discover_pending_finalizations,
    finalize_market_data,
    insert_ws_quote_snapshot,
    sync_live,
    sync_recent_market_metadata,
)


async def run_websocket_live_loop(
    *,
    connection,
    client,
    cache,
    output_path: str,
    series_ticker: str,
    db_path: str | None = None,
    scenarios_path: str | None = None,
    cache_path: str | None = None,
    auto_rebuild_after: int = 96,
) -> None:
    live_fragment_path = Path(
        output_path
    ).with_name(
        "live_market.html"
    )

    websocket_client = (
        KalshiTickerWebSocket.from_env()
    )

    previous_market: str | None = None

    # --------------------------------------------------------
    # Restart recovery
    # --------------------------------------------------------

    try:
        recent_count = sync_recent_market_metadata(
            connection,
            client,
            series_ticker,
        )

        print(
            "Recent market metadata refreshed: "
            f"{recent_count}"
        )

    except Exception as exc:
        print(
            "RECOVERY WARNING | "
            f"{type(exc).__name__}: {exc}"
        )

    recovered = discover_pending_finalizations(
        connection,
        series_ticker,
        lookback_hours=72,
    )

    pending_finalizations = {
        ticker: None
        for ticker in recovered
    }

    if recovered:
        print(
            "Recovered "
            f"{len(recovered)} market(s) "
            "awaiting final ingestion."
        )

    last_finalize_check = 0.0

    # --------------------------------------------------------
    # Background model rebuild
    # --------------------------------------------------------

    model_process = None
    last_model_check = 0.0

    async def service_model_rebuild() -> None:
        nonlocal model_process
        nonlocal last_model_check

        if model_process is not None:
            if model_process.returncode is None:
                return

            output, _ = (
                await model_process.communicate()
            )

            decoded = (
                output.decode(
                    "utf-8",
                    errors="replace",
                )
                if output
                else ""
            )

            if decoded.strip():
                print(decoded.strip())

            if model_process.returncode == 0:
                if (
                    db_path
                    and scenarios_path
                    and cache_path
                ):
                    refreshed = (
                        load_cached_historical_cache(
                            connection=connection,
                            scenarios_path=(
                                scenarios_path
                            ),
                            cache_path=cache_path,
                        )
                    )

                    if refreshed is not None:
                        cache.clear()
                        cache.update(refreshed)

                        meta = cache.get(
                            "_model_meta",
                            {},
                        )

                        print(
                            "LIVE MODEL UPDATED | "
                            f"v"
                            f"{meta.get('model_number')} | "
                            f"markets="
                            f"{meta.get('market_count')} | "
                            f"STRONG="
                            f"{meta.get('strong_strategies')}"
                        )

                    else:
                        print(
                            "MODEL RELOAD WARNING | "
                            "database changed while model "
                            "was rebuilding; another rebuild "
                            "will be scheduled."
                        )

            else:
                print(
                    "MODEL REBUILD FAILED | "
                    f"exit="
                    f"{model_process.returncode}"
                )

            model_process = None

        if (
            auto_rebuild_after <= 0
            or not db_path
            or not scenarios_path
            or not cache_path
        ):
            return

        now = time.monotonic()

        if now - last_model_check < 30.0:
            return

        last_model_check = now

        pending = count_model_pending_markets(
            connection,
            cache,
        )

        meta = cache.get(
            "_model_meta",
            {},
        )

        print(
            "MODEL STATUS | "
            f"v{meta.get('model_number', '?')} | "
            f"new markets={pending}/"
            f"{auto_rebuild_after}"
        )

        if pending < auto_rebuild_after:
            return

        print(
            "MODEL REBUILD STARTING | "
            f"{pending} new settled markets"
        )

        model_process = (
            await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "kalshi_stats.model_rebuild",
                "--db",
                db_path,
                "--scenarios",
                scenarios_path,
                "--cache",
                cache_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        )

    # --------------------------------------------------------
    # Finished-market ingestion
    # --------------------------------------------------------

    async def service_pending_finalizations() -> None:
        nonlocal last_finalize_check

        if not pending_finalizations:
            return

        now = time.monotonic()

        if now - last_finalize_check < 5.0:
            return

        ticker = next(
            iter(pending_finalizations)
        )

        try:
            finalized = finalize_market_data(
                connection,
                client,
                series_ticker=series_ticker,
                market_ticker=ticker,
            )

            if (
                finalized["settled"]
                and finalized["complete"]
            ):
                print(
                    "INGESTED | "
                    f"{ticker} | "
                    f"result="
                    f"{finalized['result']} | "
                    f"candles="
                    f"{finalized['candles']} | "
                    f"trades="
                    f"{finalized['trades']} | "
                    f"live snapshots="
                    f"{finalized['snapshots']}"
                )

                del pending_finalizations[
                    ticker
                ]

            else:
                if finalized["settled"]:
                    print(
                        "SETTLED, WAITING FOR "
                        "COMPLETE CANDLES | "
                        f"{ticker} | "
                        f"candles="
                        f"{finalized['candles']}"
                    )

                # Rotate so one slow market cannot prevent
                # other pending markets from being ingested.
                pending_finalizations[ticker] = (
                    pending_finalizations.pop(
                        ticker
                    )
                )

        except Exception as exc:
            print(
                "FINALIZE RETRY | "
                f"{ticker} | "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            pending_finalizations[ticker] = (
                pending_finalizations.pop(
                    ticker
                )
            )

        last_finalize_check = now

    # --------------------------------------------------------
    # Main always-on loop
    # --------------------------------------------------------

    try:
        while True:
            await service_pending_finalizations()
            await service_model_rebuild()

            try:
                markets = client.get_active_markets(
                    series_ticker
                )

            except Exception as exc:
                print(
                    "MARKET DISCOVERY RETRY | "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

                await asyncio.sleep(2.0)
                continue

            market = select_current_market(
                markets
            )

            if market is None:
                render_live_market_fragment(
                    live_fragment_path,
                    [],
                    cache["validated_strategies"],
                )

                if previous_market is not None:
                    print(
                        "Waiting for next "
                        "KXBTC15M market..."
                    )

                previous_market = None

                await asyncio.sleep(0.5)
                continue

            ticker = str(
                market["ticker"]
            )

            if ticker != previous_market:
                try:
                    sync_live(
                        connection,
                        client,
                        series_ticker,
                    )
                except Exception as exc:
                    print(
                        "METADATA SYNC WARNING | "
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    )

                previous_market = ticker

                print()
                print(
                    "Current market:",
                    ticker,
                )

            yes_bid = float(
                market.get(
                    "yes_bid_dollars"
                )
                or 0
            )

            yes_ask = float(
                market.get(
                    "yes_ask_dollars"
                )
                or 0
            )

            quote_ts_ms = int(
                time.time() * 1000
            )

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
                cache[
                    "validated_strategies"
                ],
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
                        await websocket_client.open(
                            ticker
                        )
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

                    while (
                        time.time() < close_ts
                    ):
                        await (
                            service_pending_finalizations()
                        )

                        await (
                            service_model_rebuild()
                        )

                        ticker_update = None

                        try:
                            raw = (
                                await asyncio.wait_for(
                                    websocket.recv(),
                                    timeout=0.25,
                                )
                            )

                            ticker_update = (
                                websocket_client
                                .parse_ticker(
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
                                ticker_update.ts_ms
                                // 1000
                            )

                            if (
                                event_second
                                != last_saved_second
                            ):
                                try:
                                    insert_ws_quote_snapshot(
                                        connection,
                                        market_ticker=(
                                            ticker
                                        ),
                                        yes_bid=yes_bid,
                                        yes_ask=yes_ask,
                                        last_price=(
                                            ticker_update
                                            .last_price
                                        ),
                                        volume=(
                                            ticker_update
                                            .volume
                                        ),
                                        open_interest=(
                                            ticker_update
                                            .open_interest
                                        ),
                                        ts_ms=(
                                            ticker_update
                                            .ts_ms
                                        ),
                                    )

                                    last_saved_second = (
                                        event_second
                                    )

                                    captured_quotes += 1

                                except Exception as exc:
                                    print(
                                        "QUOTE SAVE WARNING | "
                                        f"{type(exc).__name__}: "
                                        f"{exc}"
                                    )

                        views = build_live_side_views(
                            market=market,
                            yes_bid=yes_bid,
                            yes_ask=yes_ask,
                            matrix=cache["matrix"],
                            quote_ts_ms=(
                                quote_ts_ms
                            ),
                        )

                        model_number = (
                            cache
                            .get(
                                "_model_meta",
                                {},
                            )
                            .get(
                                "model_number",
                                0,
                            )
                        )

                        state_signature = (
                            round(
                                yes_bid,
                                4,
                            ),
                            round(
                                yes_ask,
                                4,
                            ),
                            model_number,
                            tuple(
                                (
                                    view.side,
                                    view.price_bucket,
                                    view.time_bucket,
                                )
                                for view in views
                            ),
                        )

                        if (
                            state_signature
                            != last_signature
                        ):
                            render_live_market_fragment(
                                live_fragment_path,
                                views,
                                cache[
                                    "validated_strategies"
                                ],
                            )

                            last_signature = (
                                state_signature
                            )

                        if (
                            ticker_update
                            is not None
                        ):
                            now = (
                                time.perf_counter()
                            )

                            if (
                                now
                                - last_log_time
                                >= 1.0
                            ):
                                no_bid = (
                                    1.0 - yes_ask
                                )

                                no_ask = (
                                    1.0 - yes_bid
                                )

                                event_latency_ms = max(
                                    0,
                                    int(
                                        time.time()
                                        * 1000
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
                                    f"{event_latency_ms}ms | "
                                    f"saved "
                                    f"{captured_quotes}"
                                )

                                last_log_time = now

                    break

                except asyncio.CancelledError:
                    raise

                except Exception as exc:
                    print(
                        "WebSocket disconnected:",
                        f"{type(exc).__name__}: "
                        f"{exc}",
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

            pending_finalizations[
                ticker
            ] = None

            print(
                "CLOSED | queued for ingestion:",
                ticker,
            )

            previous_market = None

            await asyncio.sleep(0.05)

    finally:
        if (
            model_process is not None
            and model_process.returncode
                is None
        ):
            model_process.terminate()

            try:
                await asyncio.wait_for(
                    model_process.wait(),
                    timeout=5,
                )
            except Exception:
                model_process.kill()
