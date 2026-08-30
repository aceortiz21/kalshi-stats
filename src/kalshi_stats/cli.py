from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import time

from .analytics import (
    _build_series_map,
    _settled_markets_with_data,
    analyze_scenarios,
    build_active_market_side_views,
    build_live_scenario_board,
    build_probability_matrix,
    database_overview,
)
from .btc_data import backfill_binance_1s, sync_latest_coinbase_second
from .database import connect, init_db
from .kalshi_api import KalshiClient
from .reporting import render_html_report
from .scenarios import load_scenarios
from .sync import backfill_history, sync_live


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kalshi-stats",
        description="Real-data statistics manager for Kalshi BTC 15-minute markets.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-db", help="Initialize the SQLite database.")
    init_parser.add_argument("--db", required=True)

    backfill_parser = subparsers.add_parser("backfill", help="Backfill KXBTC15M historical and recent market data.")
    backfill_parser.add_argument("--db", required=True)
    backfill_parser.add_argument("--series", default="KXBTC15M")

    sync_parser = subparsers.add_parser("sync-live", help="Poll active KXBTC15M markets and store quote snapshots.")
    sync_parser.add_argument("--db", required=True)
    sync_parser.add_argument("--series", default="KXBTC15M")

    sync_btc_parser = subparsers.add_parser("sync-btc", help="Sync recent second-level BTC data.")
    sync_btc_parser.add_argument("--db", required=True)

    backfill_btc_parser = subparsers.add_parser(
        "backfill-btc", help="Backfill second-level BTC history from Binance public data."
    )
    backfill_btc_parser.add_argument("--db", required=True)
    backfill_btc_parser.add_argument("--start-date", required=False)
    backfill_btc_parser.add_argument("--end-date", required=False)
    backfill_btc_parser.add_argument("--workers", type=int, default=4)

    recent_trades_parser = subparsers.add_parser(
        "backfill-recent-trades",
        help="Backfill recent settled KXBTC15M trade history through the live market-data endpoint.",
    )
    recent_trades_parser.add_argument("--db", required=True)
    recent_trades_parser.add_argument("--series", default="KXBTC15M")
    recent_trades_parser.add_argument("--days", type=int, default=2)
    recent_trades_parser.add_argument("--workers", type=int, default=4)
    recent_trades_parser.add_argument("--max-pages", type=int, default=None)

    historical_trades_parser = subparsers.add_parser(
        "backfill-historical-trades",
        help="Backfill missing historical trade history for settled KXBTC15M markets.",
    )
    historical_trades_parser.add_argument("--db", required=True)
    historical_trades_parser.add_argument("--series", default="KXBTC15M")
    historical_trades_parser.add_argument("--workers", type=int, default=8)
    historical_trades_parser.add_argument("--limit-markets", type=int, default=None)

    recent_candles_parser = subparsers.add_parser(
        "backfill-recent-candles",
        help="Backfill missing 1-minute candles for recent settled KXBTC15M markets.",
    )
    recent_candles_parser.add_argument("--db", required=True)
    recent_candles_parser.add_argument("--series", default="KXBTC15M")
    recent_candles_parser.add_argument("--start-date", required=True)
    recent_candles_parser.add_argument("--end-date", required=True)
    recent_candles_parser.add_argument("--batch-size", type=int, default=96)
    recent_candles_parser.add_argument("--limit-markets", type=int, default=None)

    analyze_parser = subparsers.add_parser("analyze", help="Render the scenario dashboard from stored Kalshi data.")
    analyze_parser.add_argument("--db", required=True)
    analyze_parser.add_argument("--scenarios", required=True)
    analyze_parser.add_argument("--output", required=True)

    matrix_parser = subparsers.add_parser("matrix", help="Print the generic price/time matrix.")
    matrix_parser.add_argument("--db", required=True)

    monitor_parser = subparsers.add_parser(
        "monitor",
        help="Continuously sync live markets and refresh the dashboard.",
    )
    monitor_parser.add_argument("--db", required=True)
    monitor_parser.add_argument("--scenarios", required=True)
    monitor_parser.add_argument("--output", required=True)
    monitor_parser.add_argument("--series", default="KXBTC15M")
    monitor_parser.add_argument("--interval", type=int, default=5)

    serve_parser = subparsers.add_parser("serve", help="Serve the dashboard locally over HTTP.")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)

    return parser


def _render_dashboard(db: str, scenarios_path: str, output: str) -> None:
    connection = connect(db)
    try:
        init_db(connection)
        scenarios = load_scenarios(scenarios_path)
        settled_markets = _settled_markets_with_data(connection)
        series_map = _build_series_map(connection, settled_markets)
        summaries, _ = analyze_scenarios(connection, scenarios, settled_markets=settled_markets, series_map=series_map)
        matrix = build_probability_matrix(connection, settled_markets=settled_markets, series_map=series_map)
        live_matches = build_live_scenario_board(connection, scenarios, summaries)
        active_views = build_active_market_side_views(connection, scenarios, matrix)
        overview = database_overview(connection)
        render_html_report(output, overview, summaries, matrix, live_matches, active_views)
    finally:
        connection.close()


def main() -> None:
    args = build_parser().parse_args()
    client = KalshiClient()

    if args.command == "init-db":
        connection = connect(args.db)
        try:
            init_db(connection)
        finally:
            connection.close()
        print(f"Initialized database at {args.db}")
        return

    if args.command == "backfill":
        connection = connect(args.db)
        try:
            init_db(connection)
            counts = backfill_history(connection, client, args.series)
        finally:
            connection.close()
        print(
            f"Backfilled {counts['markets']} markets, {counts['candles']} candles, and {counts['trades']} trades for {args.series}"
        )
        return

    if args.command == "sync-live":
        connection = connect(args.db)
        try:
            init_db(connection)
            counts = sync_live(connection, client, args.series)
        finally:
            connection.close()
        print(f"Synced {counts['markets']} markets and {counts['snapshots']} live snapshots for {args.series}")
        return

    if args.command == "sync-btc":
        connection = connect(args.db)
        try:
            init_db(connection)
            rows = sync_latest_coinbase_second(connection)
        finally:
            connection.close()
        print(f"Synced {rows} BTC 1s rows")
        return

    if args.command == "backfill-btc":
        connection = connect(args.db)
        try:
            init_db(connection)
            if args.start_date:
                start_day = date.fromisoformat(args.start_date)
            else:
                value = connection.execute(
                    "SELECT MIN(open_time) FROM markets WHERE result IN ('yes', 'no')"
                ).fetchone()[0]
                start_day = datetime.fromisoformat(value.replace("Z", "+00:00")).date()
            if args.end_date:
                end_day = date.fromisoformat(args.end_date)
            else:
                end_day = datetime.now(timezone.utc).date()
            counts = backfill_binance_1s(connection, start_day, end_day, workers=args.workers)
        finally:
            connection.close()
        print(f"Backfilled BTC 1s for {counts['days']} days and {counts['rows']} rows")
        return

    if args.command == "backfill-recent-trades":
        from .sync import backfill_recent_trade_history

        connection = connect(args.db)
        try:
            init_db(connection)
            counts = backfill_recent_trade_history(
                connection,
                client,
                args.series,
                args.days,
                workers=args.workers,
                max_pages=args.max_pages,
            )
        finally:
            connection.close()
        print(f"Backfilled recent trades for {counts['markets']} markets and {counts['trades']} trades")
        return

    if args.command == "backfill-historical-trades":
        from .sync import backfill_missing_historical_trades

        connection = connect(args.db)
        try:
            init_db(connection)
            counts = backfill_missing_historical_trades(
                connection,
                client,
                args.series,
                workers=args.workers,
                limit_markets=args.limit_markets,
            )
        finally:
            connection.close()

        print(
            "Historical trade backfill complete: "
            f"attempted={counts['attempted']}, "
            f"markets={counts['markets']}, "
            f"trades={counts['trades']}, "
            f"empty={counts['empty']}, "
            f"errors={counts['errors']}"
        )
        return

    if args.command == "backfill-recent-candles":
        from .sync import backfill_recent_candles

        connection = connect(args.db)
        try:
            init_db(connection)
            counts = backfill_recent_candles(
                connection,
                client,
                args.series,
                args.start_date,
                args.end_date,
                batch_size=args.batch_size,
                limit_markets=args.limit_markets,
            )
        finally:
            connection.close()

        print(
            "Recent candle backfill complete: "
            f"requested={counts['requested_markets']}, "
            f"markets={counts['markets']}, "
            f"candles={counts['candles']}, "
            f"empty={counts['empty']}, "
            f"errors={counts['errors']}"
        )
        return

    if args.command == "analyze":
        _render_dashboard(args.db, args.scenarios, args.output)
        print(f"Wrote report to {args.output}")
        return

    if args.command == "matrix":
        connection = connect(args.db)
        try:
            init_db(connection)
            matrix = build_probability_matrix(connection)
        finally:
            connection.close()
        print(
            "price_bucket\ttime_bucket\tobs\tunique_markets\twin_rate\tavg_best\tmedian_best\tplus_5\tplus_10\tplus_15\tplus_20\ttouch_30\ttouch_35\ttouch_40\ttouch_50"
        )
        for cell in matrix:
            win_rate = "-" if cell.win_rate is None else f"{cell.win_rate * 100:.1f}%"
            plus_5 = "-" if cell.plus_5c_rate is None else f"{cell.plus_5c_rate * 100:.1f}%"
            plus_10 = "-" if cell.plus_10c_rate is None else f"{cell.plus_10c_rate * 100:.1f}%"
            plus_15 = "-" if cell.plus_15c_rate is None else f"{cell.plus_15c_rate * 100:.1f}%"
            plus_20 = "-" if cell.plus_20c_rate is None else f"{cell.plus_20c_rate * 100:.1f}%"
            touch_30 = "-" if cell.touch_30_rate is None else f"{cell.touch_30_rate * 100:.1f}%"
            touch_35 = "-" if cell.touch_35_rate is None else f"{cell.touch_35_rate * 100:.1f}%"
            touch_40 = "-" if cell.touch_40_rate is None else f"{cell.touch_40_rate * 100:.1f}%"
            touch_50 = "-" if cell.touch_50_rate is None else f"{cell.touch_50_rate * 100:.1f}%"
            avg_best = "-" if cell.avg_best_subsequent_price is None else f"{cell.avg_best_subsequent_price * 100:.1f}c"
            median_best = (
                "-" if cell.median_best_subsequent_price is None else f"{cell.median_best_subsequent_price * 100:.1f}c"
            )
            print(
                f"{cell.price_bucket}\t{cell.time_bucket}\t{cell.observations}\t{cell.unique_markets}\t{win_rate}\t{avg_best}\t{median_best}\t{plus_5}\t{plus_10}\t{plus_15}\t{plus_20}\t{touch_30}\t{touch_35}\t{touch_40}\t{touch_50}"
            )
        return

    if args.command == "monitor":
        while True:
            connection = connect(args.db)
            try:
                init_db(connection)
                sync_live(connection, client, args.series)
                sync_latest_coinbase_second(connection)
            finally:
                connection.close()
            _render_dashboard(args.db, args.scenarios, args.output)
            print(f"Updated live snapshots and refreshed {args.output}")
            time.sleep(args.interval)

    if args.command == "serve":
        server = ThreadingHTTPServer((args.host, args.port), SimpleHTTPRequestHandler)
        print(f"Serving dashboard at http://{args.host}:{args.port}/reports/dashboard.html")
        server.serve_forever()


if __name__ == "__main__":
    main()
