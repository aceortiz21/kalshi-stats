from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

from .dashboard_cache import (
    count_model_pending_markets,
    load_cached_historical_cache,
)
from .database import connect
from .health import (
    build_data_health,
    health_signature,
)
from .kalshi_ws import KalshiTickerWebSocket
from .live import (
    build_live_side_views,
    select_current_market,
)
from .personal_performance import (
    build_personal_performance,
    personal_performance_signature,
)
from .reporting import render_live_market_fragment
from .sync import (
    discover_pending_finalizations,
    finalize_market_data,
    insert_ws_quote_snapshot,
    sync_live,
    sync_recent_market_metadata,
)


def load_live_brti_state(
    connection,
    market_ticker: str,
    *,
    now_ms: int | None = None,
) -> dict[str, object] | None:
    """Load the latest official BRTI state for a live market."""

    market_row = connection.execute(
        """
        SELECT reference_price
        FROM markets
        WHERE ticker = ?
        """,
        (market_ticker,),
    ).fetchone()

    target = (
        None
        if market_row is None
        or market_row["reference_price"] is None
        else float(
            market_row["reference_price"]
        )
    )

    brti_row = connection.execute(
        """
        SELECT
            ts,
            value,
            avg_60s_value,
            avg_60s_window_size,
            final_60s_avg_15m,
            final_60s_window_size_15m
        FROM brti_snapshots
        WHERE index_id = 'BRTI'
        ORDER BY ts DESC
        LIMIT 1
        """
    ).fetchone()

    if target is None and brti_row is None:
        return None

    state: dict[str, object] = {
        "target": target,
        "ts": None,
        "age_ms": None,
        "value": None,
        "distance_dollars": None,
        "distance_bps": None,
        "avg_60s_value": None,
        "avg_60s_window_size": None,
        "final_60s_avg_15m": None,
        "final_60s_window_size_15m": None,
        "final_distance_dollars": None,
        "final_distance_bps": None,
    }

    if brti_row is None:
        return state

    timestamp = int(
        brti_row["ts"]
    )

    if now_ms is None:
        now_ms = int(
            time.time() * 1000
        )

    value = float(
        brti_row["value"]
    )

    avg60 = (
        None
        if brti_row["avg_60s_value"] is None
        else float(
            brti_row["avg_60s_value"]
        )
    )

    final_avg = (
        None
        if brti_row["final_60s_avg_15m"] is None
        else float(
            brti_row["final_60s_avg_15m"]
        )
    )

    state.update(
        {
            "ts": timestamp,
            "age_ms": max(
                0,
                now_ms - timestamp,
            ),
            "value": value,
            "avg_60s_value": avg60,
            "avg_60s_window_size": (
                brti_row[
                    "avg_60s_window_size"
                ]
            ),
            "final_60s_avg_15m": final_avg,
            "final_60s_window_size_15m": (
                brti_row[
                    "final_60s_window_size_15m"
                ]
            ),
        }
    )

    if (
        target is not None
        and target != 0
    ):
        distance = (
            value - target
        )

        state[
            "distance_dollars"
        ] = distance

        state[
            "distance_bps"
        ] = (
            distance
            / target
            * 10000.0
        )

        if final_avg is not None:
            final_distance = (
                final_avg - target
            )

            state[
                "final_distance_dollars"
            ] = final_distance

            state[
                "final_distance_bps"
            ] = (
                final_distance
                / target
                * 10000.0
            )

    return state


def brti_state_signature(
    state,
):
    if not state:
        return None

    def rounded(name):
        value = state.get(name)

        if value is None:
            return None

        return round(
            float(value),
            4,
        )

    return (
        state.get("ts"),
        rounded("target"),
        rounded("value"),
        rounded("avg_60s_value"),
        state.get(
            "avg_60s_window_size"
        ),
        rounded(
            "final_60s_avg_15m"
        ),
        state.get(
            "final_60s_window_size_15m"
        ),
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

    # --------------------------------------------------------
    # Runtime health + background finalization
    # --------------------------------------------------------

    health_state: dict[str, object] = {}
    last_health_check = 0.0
    last_health_log = 0.0

    current_market_ticker: str | None = None
    ws_connected = False
    last_event_latency_ms: int | None = None
    brti_state: dict[str, object] | None = None

    personal_state = None
    last_personal_check = 0.0

    finalization_task = None
    finalization_ticker: str | None = None

    def refresh_personal(
        *,
        force: bool = False,
    ) -> None:
        nonlocal personal_state
        nonlocal last_personal_check

        now = time.monotonic()

        if (
            not force
            and now
            - last_personal_check
            < 5.0
        ):
            return

        last_personal_check = now

        try:
            personal_state = (
                build_personal_performance(
                    connection
                )
            )

        except Exception as exc:
            print(
                "PERSONAL LEDGER WARNING | "
                f"{type(exc).__name__}: "
                f"{exc}"
            )


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
    # Health + finished-market ingestion
    # --------------------------------------------------------

    def refresh_health(
        *,
        force: bool = False,
    ) -> None:
        nonlocal health_state
        nonlocal last_health_check
        nonlocal last_health_log

        now = time.monotonic()

        if (
            not force
            and now - last_health_check < 5.0
        ):
            return

        last_health_check = now

        try:
            model_pending = (
                count_model_pending_markets(
                    connection,
                    cache,
                )
            )

            model_meta = cache.get(
                "_model_meta",
                {},
            )

            model_running = bool(
                model_process is not None
                and model_process.returncode is None
            )

            health_state = build_data_health(
                connection,
                series_ticker=series_ticker,
                model_meta=model_meta,
                model_pending=model_pending,
                auto_rebuild_after=auto_rebuild_after,
                pending_finalizations=len(
                    pending_finalizations
                ),
                current_market_ticker=(
                    current_market_ticker
                ),
                ws_connected=ws_connected,
                last_event_latency_ms=(
                    last_event_latency_ms
                ),
                model_rebuild_running=(
                    model_running
                ),
            )

            if (
                now - last_health_log >= 30.0
            ):
                print(
                    "HEALTH | "
                    f"{health_state.get('status')} | "
                    f"WS="
                    f"{'up' if ws_connected else 'down'} | "
                    f"24h markets="
                    f"{health_state.get('recent_markets')}/"
                    f"{health_state.get('expected_recent_markets')} | "
                    f"candles="
                    f"{health_state.get('complete_candles')}/"
                    f"{health_state.get('recent_settled')} | "
                    f"high-res="
                    f"{health_state.get('recent_quote_markets')} | "
                    f"pending="
                    f"{health_state.get('pending_finalizations')} | "
                    f"model=v"
                    f"{health_state.get('model_number')} "
                    f"+{health_state.get('model_pending')}"
                )

                last_health_log = now

        except Exception as exc:
            health_state = {
                "status": "WARNING",
                "issues": [
                    "health check failed: "
                    f"{type(exc).__name__}"
                ],
                "ws_connected": ws_connected,
                "pending_finalizations": len(
                    pending_finalizations
                ),
                "model_number": (
                    cache
                    .get("_model_meta", {})
                    .get("model_number", 0)
                ),
                "model_market_count": (
                    cache
                    .get("_model_meta", {})
                    .get("market_count", 0)
                ),
                "strong_strategies": (
                    cache
                    .get("_model_meta", {})
                    .get("strong_strategies", 0)
                ),
                "model_pending": 0,
                "auto_rebuild_after": (
                    auto_rebuild_after
                ),
            }


    def finalize_in_worker(
        ticker: str,
    ) -> dict[str, object]:
        """
        Run settlement/candle ingestion away from the WebSocket
        event loop with its own SQLite connection.
        """

        if not db_path:
            raise RuntimeError(
                "db_path is required for "
                "background finalization"
            )

        worker_connection = connect(
            db_path
        )

        try:
            return finalize_market_data(
                worker_connection,
                client,
                series_ticker=series_ticker,
                market_ticker=ticker,
            )

        finally:
            worker_connection.close()


    async def service_pending_finalizations() -> None:
        nonlocal last_finalize_check
        nonlocal finalization_task
        nonlocal finalization_ticker

        now = time.monotonic()

        # Harvest a completed background job.
        if finalization_task is not None:
            if not finalization_task.done():
                return

            ticker = finalization_ticker
            task = finalization_task

            finalization_task = None
            finalization_ticker = None

            try:
                finalized = task.result()

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

                    if ticker is not None:
                        pending_finalizations.pop(
                            ticker,
                            None,
                        )

                else:
                    if finalized["settled"]:
                        print(
                            "SETTLED, WAITING FOR "
                            "COMPLETE CANDLES | "
                            f"{ticker} | "
                            f"candles="
                            f"{finalized['candles']}"
                        )

                    if (
                        ticker is not None
                        and ticker
                        in pending_finalizations
                    ):
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

                if (
                    ticker is not None
                    and ticker
                    in pending_finalizations
                ):
                    pending_finalizations[ticker] = (
                        pending_finalizations.pop(
                            ticker
                        )
                    )

            last_finalize_check = now

            refresh_health(
                force=True
            )

        if finalization_task is not None:
            return

        if not pending_finalizations:
            return

        now = time.monotonic()

        if (
            now - last_finalize_check < 5.0
        ):
            return

        ticker = next(
            iter(pending_finalizations)
        )

        finalization_ticker = ticker

        finalization_task = asyncio.create_task(
            asyncio.to_thread(
                finalize_in_worker,
                ticker,
            )
        )

    # --------------------------------------------------------
    # Main always-on loop
    # --------------------------------------------------------

    try:
        while True:
            await service_pending_finalizations()
            await service_model_rebuild()
            refresh_health()
            refresh_personal()

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
                current_market_ticker = None
                ws_connected = False
                brti_state = None

                refresh_health(
                    force=True
                )

                render_live_market_fragment(
                    live_fragment_path,
                    [],
                    cache["validated_strategies"],
                    health=health_state,
                    brti=brti_state,
                    personal=personal_state,
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

            current_market_ticker = ticker

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

            brti_state = load_live_brti_state(
                connection,
                ticker,
            )

            refresh_health(
                force=True
            )

            render_live_market_fragment(
                live_fragment_path,
                views,
                cache[
                    "validated_strategies"
                ],
                health=health_state,
                brti=brti_state,
                personal=personal_state,
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

                    ws_connected = True

                    refresh_health(
                        force=True
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

                        refresh_health()
                        refresh_personal()

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

                            last_event_latency_ms = max(
                                0,
                                int(
                                    time.time()
                                    * 1000
                                    - quote_ts_ms
                                ),
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

                        brti_state = load_live_brti_state(
                            connection,
                            ticker,
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
                            health_signature(
                                health_state
                            ),
                            brti_state_signature(
                                brti_state
                            ),
                            personal_performance_signature(
                                personal_state
                            ),
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
                                health=health_state,
                                brti=brti_state,
                                personal=personal_state,
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
                    ws_connected = False

                    refresh_health(
                        force=True
                    )

                    try:
                        render_live_market_fragment(
                            live_fragment_path,
                            views,
                            cache[
                                "validated_strategies"
                            ],
                            health=health_state,
                            brti=brti_state,
                        )
                    except Exception:
                        pass

                    if websocket is not None:
                        try:
                            await websocket.close()
                        except Exception:
                            pass

            pending_finalizations[
                ticker
            ] = None

            current_market_ticker = None
            ws_connected = False

            refresh_health(
                force=True
            )

            print(
                "CLOSED | queued for ingestion:",
                ticker,
            )

            previous_market = None

            await asyncio.sleep(0.05)

    finally:
        if (
            finalization_task is not None
            and not finalization_task.done()
        ):
            try:
                await asyncio.wait_for(
                    asyncio.shield(
                        finalization_task
                    ),
                    timeout=5,
                )
            except Exception:
                pass

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
