# Kalshi BTC Statistics Manager

This project is a real-data research engine for Kalshi's `KXBTC15M` series, titled `BTC price up in next 15 mins?`. It does not place trades or emit buy/sell commands. It pulls data from Kalshi's public API, stores it in SQLite, evaluates scenario definitions, and renders a readable HTML dashboard with historical hit rates and gross scenario P/L statistics.

## What data it uses

Only Kalshi API data is used in the research pipeline:

- settled historical `KXBTC15M` markets
- 1-minute historical candlesticks where Kalshi exposes them
- trade history for settled markets
- live best bid / ask snapshots for current and future markets

External BTC reference data is also supported:

- second-level BTC history from Binance's official public archive when available
- live BTC point-in-time data from Coinbase for future backlog growth

The market is directional, not fixed-strike. For `KXBTC15M`, `YES` resolves true when BTC's end-of-window benchmark is at least the start-of-window benchmark.

## Commands

Initialize the database:

```bash
PYTHONPATH=src python3 -m kalshi_stats.cli init-db --db data/kalshi_stats.sqlite
```

Backfill historical and recent markets from the Kalshi API:

```bash
PYTHONPATH=src python3 -m kalshi_stats.cli backfill --db data/kalshi_stats.sqlite --series KXBTC15M
```

Pull live quote snapshots for active markets:

```bash
PYTHONPATH=src python3 -m kalshi_stats.cli sync-live --db data/kalshi_stats.sqlite --series KXBTC15M
```

Pull one live BTC observation:

```bash
PYTHONPATH=src python3 -m kalshi_stats.cli sync-btc --db data/kalshi_stats.sqlite
```

Backfill BTC second-level history:

```bash
PYTHONPATH=src python3 -m kalshi_stats.cli backfill-btc \
  --db data/kalshi_stats.sqlite \
  --start-date 2026-06-28 \
  --end-date 2026-06-29 \
  --workers 2
```

Backfill recent settled KXBTC15M trade history:

```bash
PYTHONPATH=src python3 -m kalshi_stats.cli backfill-recent-trades \
  --db data/kalshi_stats.sqlite \
  --series KXBTC15M \
  --days 1 \
  --workers 4
```

Generate the HTML dashboard:

```bash
PYTHONPATH=src python3 -m kalshi_stats.cli analyze \
  --db data/kalshi_stats.sqlite \
  --scenarios config/scenarios.json \
  --output reports/dashboard.html
```

Continuously update live snapshots and refresh the dashboard every 5 seconds:

```bash
PYTHONPATH=src python3 -m kalshi_stats.cli monitor \
  --db data/kalshi_stats.sqlite \
  --scenarios config/scenarios.json \
  --output reports/dashboard.html \
  --series KXBTC15M \
  --interval 5
```

Print the generic price/time matrix in the terminal:

```bash
PYTHONPATH=src python3 -m kalshi_stats.cli matrix --db data/kalshi_stats.sqlite
```

Serve the dashboard locally:

```bash
PYTHONPATH=src python3 -m kalshi_stats.cli serve --host 127.0.0.1 --port 8000
```

## Handoff Status

Status snapshot from the current workspace:

- database: [`data/kalshi_stats.sqlite`](/home/aceortiz/stats/data/kalshi_stats.sqlite)
- dashboard: [`reports/dashboard.html`](/home/aceortiz/stats/reports/dashboard.html)
- scenario config: [`config/scenarios.json`](/home/aceortiz/stats/config/scenarios.json)
- analysis/report entrypoint: [`src/kalshi_stats/cli.py`](/home/aceortiz/stats/src/kalshi_stats/cli.py)

Current dataset state last verified on Sunday, August 30, 2026:

- `24,518` total `KXBTC15M` markets stored
- `24,407` settled markets stored
- `316` settled markets with stored trade history
- `126` settled markets with stored 1-minute candlestick history
- `3,796,527` total Kalshi trades stored
- `172,801` BTC second-level rows stored
- `32` live quote snapshots
- latest verified live snapshot stored at `2026-08-30T00:16:14Z`
- verified BTC second-level archive currently covers June 28, 2026 through June 29, 2026, plus one live Coinbase tick collected on Sunday, August 30, 2026

Current configured scenarios and occurrence counts from the stored database:

- `early_20s_rebound`: `156`
- `teens_comeback`: `245`
- `single_digit_resurrection`: `217`
- `favorite_fade_80s`: `55`
- `full_flip_from_20s`: `245`
- `late_underdog_comeback`: `220`

Important current limitation:

- The market table is large because `GET /markets` includes many future precreated `KXBTC15M` markets. Historical analysis currently uses settled markets only, so the useful research count is the settled count above.
- Kalshi public API history is not true second-by-second for old markets. Historical analysis is based on Kalshi candles where available and otherwise trade prints, and only complete-enough trade histories are used in scenario analysis.
- Live Binance REST access was blocked in this environment on Sunday, August 30, 2026, so live BTC collection currently uses Coinbase while historical BTC second-level backlog uses Binance archive files.
- For future accuracy, keep `monitor` running during trading so your own live KXBTC15M snapshots and BTC observations accumulate going forward.

Recommended next engineering steps:

1. Continue recent-trade backfill for additional settled days until the complete-history count is much larger than `316`.
2. Add explicit fee-aware net P/L metrics instead of gross target P/L only.
3. Add BTC-path-driven scenarios using the local `btc_1s` table.
4. Add scenario families grouped by time bucket, especially `10s_but_wins_by_time_bucket` and `first_1m_3m_5m_favorite_accuracy`.
5. Auto-refresh the local HTTP dashboard while `monitor` is running.

## Dashboard contents

The report at [`reports/dashboard.html`](/home/aceortiz/stats/reports/dashboard.html) includes:

- an active market board for all current KXBTC15M sides with their current price/time bucket statistics
- a live scenario board for current active KXBTC15M markets
- a historical scenario matrix with occurrence counts, win rates, target-hit rates, and average gross P/L by target
- a generic price-by-time matrix for the whole dataset

How to use the dashboard:

- Open [`reports/dashboard.html`](/home/aceortiz/stats/reports/dashboard.html) in a browser, or run `serve` and open `http://127.0.0.1:8000/reports/dashboard.html`.
- Start at `Active Market Board` to compare every live YES/NO side against the historical price/time matrix.
- Use `Live Scenario Board` to see whether any active `KXBTC15M` market currently matches one of your named triggers.
- Move to `Scenario Matrix` to compare historical win rate, target hit rate, and average gross P/L for each scenario.
- Use `Price × Time Matrix` when you want a more generic answer such as “what usually happens after 24c with 8 minutes left?” even if no named scenario exactly matches.
- Interpret the dashboard as a probability reference, not an execution engine. It is meant to support your judgment, not replace it.

## Historical accuracy notes

There is one important limit from the Kalshi API itself:

- For past markets, the best structured intramarket history available is the Kalshi candlestick/trade history. That means historical scenario detection is minute-candle based when candles exist, and otherwise trade-print based.
- For current and future markets, this project records your own live quote snapshots continuously, so those markets become higher resolution from the moment the tracker is running.
- Kalshi's public market API does not expose the full intramarket BTC benchmark path directly in the same way it exposes contract price history, so this project currently analyzes contract behavior rather than a second-by-second BTC spot path.

## Default scenarios

The starter config in [`config/scenarios.json`](/home/aceortiz/stats/config/scenarios.json) includes:

- early 20s rebound
- teens comeback
- single-digit resurrection
- favorite fade from the 80s
- full flip from the 20s
- late underdog comeback

## Strong next scenarios to add

- `10s_but_wins_by_time_bucket`
- `single_digits_to_30_or_40`
- `favorite_90s_failure`
- `multiple_lead_changes`
- `first_1m_3m_5m_favorite_accuracy`
- `boundary_volatility_between_contracts`
- `kalshi_price_vs_recent_direction`
- `late_80s_or_90s_failure`

## Sources

- Kalshi historical data overview: https://docs.kalshi.com/getting_started/historical_data
- Kalshi market API reference: https://docs.kalshi.com/api-reference/market/get-markets
- Binance official public data repository: https://data.binance.vision/
