from __future__ import annotations

import html
from pathlib import Path

from .analytics import PRICE_AFTER_SECONDS, SCENARIO_TARGETS
from .models import ActiveMarketSideView, LiveScenarioMatch, MatrixCell, ScenarioSummary


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _price(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}c"


def _seconds(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f}s"

def _clock(seconds: int | float) -> str:
    seconds = max (0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}:{seconds:02d}"


def _sample_badge(summary: ScenarioSummary) -> str:
    return "Low sample" if summary.low_sample_warning else "OK"


def _targets(summary: ScenarioSummary) -> str:
    parts = []
    for target in SCENARIO_TARGETS:
        hit_rate = summary.target_hit_rates[target]
        count = summary.target_touch_counts[target]
        median_time = summary.median_time_to_targets[target]
        parts.append(f"{int(target * 100)}c {_pct(hit_rate)} ({count}/{summary.occurrences}), med {_seconds(median_time)}")
    return ", ".join(parts)


def _after_prices(summary: ScenarioSummary) -> str:
    parts = []
    for offset in PRICE_AFTER_SECONDS:
        parts.append(f"{offset}s {_price(summary.avg_price_after_seconds[offset])}")
    return ", ".join(parts)


def _time_breakdown(summary: ScenarioSummary) -> str:
    parts = []
    for bucket, data in summary.time_breakdown.items():
        parts.append(
            f"{bucket}: N={data['n']}, win={_pct(data['win_rate'])}, med max {_price(data.get('median_max_price'))}"
        )
    return ", ".join(parts)


def _overlaps(summary: ScenarioSummary) -> str:
    if not summary.overlap_market_counts:
        return "-"
    return ", ".join(f"{scenario}:{count}" for scenario, count in summary.overlap_market_counts.items())


def render_html_report(
    output_path: str | Path,
    overview: dict[str, int | str],
    summaries: list[ScenarioSummary],
    matrix: list[MatrixCell],
    live_matches: list[LiveScenarioMatch],
    active_views: list[ActiveMarketSideView],
) -> None:
    scenario_rows = "\n".join(
        """
        <tr>
          <td>{name}</td>
          <td>{definition}</td>
          <td>{n}</td>
          <td>{unique_markets}</td>
          <td>{sample_badge}</td>
          <td>{win_rate}</td>
          <td>{win_ci}</td>
          <td>{avg_entry}</td>
          <td>{median_entry}</td>
          <td>{avg_best}</td>
          <td>{median_best}</td>
          <td>{avg_worst}</td>
          <td>{avg_mfe}</td>
          <td>{median_mfe}</td>
          <td>{avg_mae}</td>
          <td>{median_mae}</td>
          <td>{mfe_pct}</td>
          <td>{mae_pct}</td>
          <td>{time_to_best}</td>
          <td>{time_to_worst}</td>
          <td>{after_prices}</td>
          <td>{targets}</td>
          <td>{time_breakdown}</td>
          <td>{overlaps}</td>
        </tr>
        """.format(
            name=html.escape(summary.definition.name),
            definition=html.escape(
                f"{summary.definition.description} | mode={summary.definition.occurrence_mode} | cooldown={summary.definition.cooldown_seconds}s"
            ),
            n=summary.occurrences,
            unique_markets=summary.unique_markets,
            sample_badge=_sample_badge(summary),
            win_rate=_pct(summary.win_rate),
            win_ci=html.escape(f"{_pct(summary.win_rate_ci_low)} to {_pct(summary.win_rate_ci_high)}"),
            avg_entry=_price(summary.avg_entry_price),
            median_entry=_price(summary.median_entry_price),
            avg_best=_price(summary.avg_best_subsequent_price),
            median_best=_price(summary.median_best_subsequent_price),
            avg_worst=_price(summary.avg_worst_subsequent_price),
            avg_mfe=_price(summary.avg_max_favorable_excursion),
            median_mfe=_price(summary.median_max_favorable_excursion),
            avg_mae=_price(summary.avg_max_adverse_excursion),
            median_mae=_price(summary.median_max_adverse_excursion),
            mfe_pct=_pct(summary.avg_max_favorable_excursion_pct),
            mae_pct=_pct(summary.avg_max_adverse_excursion_pct),
            time_to_best=_seconds(summary.median_time_to_best_price),
            time_to_worst=_seconds(summary.median_time_to_worst_price),
            after_prices=html.escape(_after_prices(summary)),
            targets=html.escape(_targets(summary)),
            time_breakdown=html.escape(_time_breakdown(summary)),
            overlaps=html.escape(_overlaps(summary)),
        )
        for summary in summaries
    )

    live_rows = "\n".join(
        """
        <tr>
          <td>{market}</td>
          <td>{status}</td>
          <td>{side}</td>
          <td>{price}</td>
          <td>{time_left}</td>
          <td>{scenario}</td>
          <td>{occurrences}</td>
          <td>{win_rate}</td>
          <td>{targets}</td>
        </tr>
        """.format(
            market=html.escape(match.market_ticker),
            status=html.escape(match.market_status),
            side=html.escape(match.side.upper()),
            price=_price(match.current_price),
            time_left=f"{match.seconds_remaining}s",
            scenario=html.escape(match.scenario_name),
            occurrences=match.historical_occurrences,
            win_rate=_pct(match.historical_win_rate),
            targets=html.escape(
                ", ".join(
                    f"{int(target * 100)}c {_pct(rate)}"
                    for target, rate in match.target_hit_rates.items()
                    if target in SCENARIO_TARGETS
                )
            ),
        )
        for match in live_matches
    ) or """
        <tr><td colspan="9">No active KXBTC15M markets currently match the configured scenario triggers.</td></tr>
    """

        active_cards = "\n".join(
        """
        <article class="market-card {side_class}">
          <div class="market-card-top">
            <div>
              <span class="eyebrow">{side} CONTRACT</span>
              <h3>{market}</h3>
            </div>
            <div class="live-price">{price}</div>
          </div>

          <div class="market-context">
            <div>
              <span>Time Remaining</span>
              <strong>{time_left}</strong>
            </div>
            <div>
              <span>Historical State</span>
              <strong>{price_bucket} · {time_bucket}</strong>
            </div>
            <div>
              <span>Historical N</span>
              <strong>{obs}</strong>
            </div>
            <div>
              <span>Eventual Win</span>
              <strong>{win_rate}</strong>
            </div>
          </div>

          <div class="section-label">Subsequent Price Reach</div>
          <div class="target-grid">
            <div><span>+5¢</span><strong>{plus_5}</strong></div>
            <div><span>+10¢</span><strong>{plus_10}</strong></div>
            <div><span>+15¢</span><strong>{plus_15}</strong></div>
            <div><span>+20¢</span><strong>{plus_20}</strong></div>
          </div>

          <div class="market-footer">
            <div>
              <span>Median Subsequent Max</span>
              <strong>{median_best}</strong>
            </div>
            <div>
              <span>Average Subsequent Max</span>
              <strong>{avg_best}</strong>
            </div>
          </div>

          <div class="scenario-line">
            <span>Matching scenarios</span>
            <strong>{matched}</strong>
          </div>
        </article>
        """.format(
            market=html.escape(view.market_ticker),
            side=html.escape(view.side.upper()),
            side_class="yes-side" if view.side.lower() == "yes" else "no-side",
            price=_price(view.current_price),
            time_left=_clock(view.seconds_remaining),
            price_bucket=html.escape(view.price_bucket),
            time_bucket=html.escape(view.time_bucket),
            obs=f"{view.observations:,}",
            win_rate=_pct(view.win_rate),
            plus_5=_pct(view.plus_5c_rate),
            plus_10=_pct(view.plus_10c_rate),
            plus_15=_pct(view.plus_15c_rate),
            plus_20=_pct(view.plus_20c_rate),
            avg_best=_price(view.avg_best_subsequent_price),
            median_best=_price(view.median_best_subsequent_price),
            matched=html.escape(", ".join(view.matched_scenarios) or "None"),
        )
        for view in active_views
    ) or """
        <div class="empty-state">
          <strong>No active KXBTC15M market in the stored snapshot.</strong>
          <span>Run a live sync while a market is active to populate this board.</span>
        </div>
    """

    matrix_rows = "\n".join(
        """
        <tr>
          <td>{price_bucket}</td>
          <td>{time_bucket}</td>
          <td>{observations}</td>
          <td>{unique_markets}</td>
          <td>{win_rate}</td>
          <td>{avg_best}</td>
          <td>{median_best}</td>
          <td>{plus_5}</td>
          <td>{plus_10}</td>
          <td>{plus_15}</td>
          <td>{plus_20}</td>
          <td>{touch_30}</td>
          <td>{touch_35}</td>
          <td>{touch_40}</td>
          <td>{touch_50}</td>
        </tr>
        """.format(
            price_bucket=html.escape(cell.price_bucket),
            time_bucket=html.escape(cell.time_bucket),
            observations=cell.observations,
            unique_markets=cell.unique_markets,
            win_rate=_pct(cell.win_rate),
            avg_best=_price(cell.avg_best_subsequent_price),
            median_best=_price(cell.median_best_subsequent_price),
            plus_5=_pct(cell.plus_5c_rate),
            plus_10=_pct(cell.plus_10c_rate),
            plus_15=_pct(cell.plus_15c_rate),
            plus_20=_pct(cell.plus_20c_rate),
            touch_30=_pct(cell.touch_30_rate),
            touch_35=_pct(cell.touch_35_rate),
            touch_40=_pct(cell.touch_40_rate),
            touch_50=_pct(cell.touch_50_rate),
        )
        for cell in matrix
    )

    notes = """
    Historical analysis uses real data already stored in the database. Scenario counting defaults to first entry per market unless a scenario explicitly opts into re-entry after cooldown. Markets with partial trade history are excluded from trade-based scenario analysis so they do not silently dilute the statistics.
    """

    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kalshi BTC 15m Statistics Manager</title>
  <style>
    :root {{
      --bg: #f6f1e8;
      --panel: rgba(255, 252, 246, 0.95);
      --ink: #1f1a17;
      --muted: #695f56;
      --line: #d9c8b3;
      --accent: #0d6b53;
      --accent-soft: #dff1ea;
      --warn: #8a3a1f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Palatino Linotype", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(13, 107, 83, 0.12), transparent 25%),
        radial-gradient(circle at bottom right, rgba(138, 58, 31, 0.10), transparent 25%),
        linear-gradient(180deg, #fbf8f3 0%, var(--bg) 100%);
    }}
    main {{
      width: min(1440px, calc(100% - 32px));
      margin: 24px auto 48px;
    }}
    .hero, section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: 0 12px 28px rgba(31, 26, 23, 0.06);
    }}
    .hero {{
      padding: 24px;
      margin-bottom: 18px;
    }}
    h1, h2 {{ margin: 0 0 10px; }}
    p {{
      margin: 0;
      line-height: 1.55;
      color: var(--muted);
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 12px;
      margin-top: 18px;
    }}
    .stat {{
      padding: 14px;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: white;
    }}
    .stat strong {{
      display: block;
      font-size: 1.55rem;
      color: var(--accent);
    }}
    section {{
      padding: 20px;
      margin-top: 16px;
    }}
    .table-wrap {{
      overflow-x: auto;
      margin-top: 12px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.94rem;
    }}
    th, td {{
      padding: 10px 12px;
      text-align: left;
      vertical-align: top;
      border-bottom: 1px solid var(--line);
    }}
    th {{
      position: sticky;
      top: 0;
      background: var(--accent-soft);
    }}
    .note {{
      margin-top: 12px;
      padding: 12px 14px;
      border-left: 4px solid var(--warn);
      background: rgba(138, 58, 31, 0.06);
      color: var(--ink);
    }}
  </style>
</head>
<body>
  <main>
    <div class="hero">
      <h1>Kalshi BTC 15m Statistics Manager</h1>
      <p>Historical behavior companion for KXBTC15M. The emphasis is sample size, post-trigger path behavior, and price/time context, not automated recommendations.</p>
      <div class="stats">
        <div class="stat"><span>Total Markets</span><strong>{overview["market_count"]}</strong></div>
        <div class="stat"><span>Settled Markets</span><strong>{overview["settled_market_count"]}</strong></div>
        <div class="stat"><span>Settled With Trade History</span><strong>{overview["settled_with_trade_history"]}</strong></div>
        <div class="stat"><span>Settled With Candle History</span><strong>{overview["settled_with_candle_history"]}</strong></div>
        <div class="stat"><span>BTC-Covered Markets</span><strong>{overview["btc_covered_markets"]}</strong></div>
        <div class="stat"><span>Kalshi Trades</span><strong>{overview["trade_count"]}</strong></div>
        <div class="stat"><span>BTC 1s Rows</span><strong>{overview["btc_row_count"]}</strong></div>
        <div class="stat"><span>Last Live Sync</span><strong>{html.escape(str(overview["last_snapshot"] or "-"))}</strong></div>
      </div>
    </div>
    <section>
      <h2>Data Coverage / Health</h2>
      <p>Interpret any percentage in the rest of the dashboard through these coverage counts first.</p>
    </section>
    <section>
      <h2>Active Market Board</h2>
      <p>Every current YES/NO side is mapped to the historical price/time matrix so you can compare a live contract against prior behavior quickly.</p>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Market</th>
              <th>Side</th>
              <th>Current Price</th>
              <th>Time Left</th>
              <th>Price Bucket</th>
              <th>Time Bucket</th>
              <th>N</th>
              <th>Win Rate</th>
              <th>Reach +5c</th>
              <th>Reach +10c</th>
              <th>Reach +15c</th>
              <th>Reach +20c</th>
              <th>Touch 30c</th>
              <th>Touch 35c</th>
              <th>Touch 40c</th>
              <th>Touch 50c</th>
              <th>Avg Max Price</th>
              <th>Median Max Price</th>
              <th>Named Scenarios</th>
            </tr>
          </thead>
          <tbody>{active_rows}</tbody>
        </table>
      </div>
    </section>
    <section>
      <h2>Live Scenario Board</h2>
      <p>Only active markets that currently satisfy one of the named scenario definitions appear here.</p>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Market</th>
              <th>Status</th>
              <th>Side</th>
              <th>Current Price</th>
              <th>Time Left</th>
              <th>Scenario</th>
              <th>N</th>
              <th>Win Rate</th>
              <th>Target Touch Rates</th>
            </tr>
          </thead>
          <tbody>{live_rows}</tbody>
        </table>
      </div>
    </section>
    <section>
      <h2>Historical Scenario Statistics</h2>
      <p>Each row represents a scenario definition and its post-trigger behavior. Sample size and unique-market counts are shown explicitly.</p>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Scenario</th>
              <th>Definition</th>
              <th>N</th>
              <th>Unique Markets</th>
              <th>Reliability</th>
              <th>Win Rate</th>
              <th>95% CI</th>
              <th>Avg Trigger</th>
              <th>Median Trigger</th>
              <th>Avg Max Price</th>
              <th>Median Max Price</th>
              <th>Avg Min Price</th>
              <th>Avg MFE</th>
              <th>Median MFE</th>
              <th>Avg MAE</th>
              <th>Median MAE</th>
              <th>Avg MFE %</th>
              <th>Avg MAE %</th>
              <th>Median Time to Max</th>
              <th>Median Time to Min</th>
              <th>Avg Price After</th>
              <th>Target Touch Summary</th>
              <th>Time-Remaining Breakdown</th>
              <th>Overlap Markets</th>
            </tr>
          </thead>
          <tbody>{scenario_rows}</tbody>
        </table>
      </div>
      <div class="note">{notes}</div>
    </section>
    <section>
      <h2>Price × Time Matrix</h2>
      <p>Generic behavior for a side priced in a given bucket with a given amount of time left. This is the most broadly reusable reference table on the page.</p>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Price Bucket</th>
              <th>Time Bucket</th>
              <th>N</th>
              <th>Unique Markets</th>
              <th>Win Rate</th>
              <th>Avg Max Price</th>
              <th>Median Max Price</th>
              <th>Reach +5c</th>
              <th>Reach +10c</th>
              <th>Reach +15c</th>
              <th>Reach +20c</th>
              <th>Touch 30c</th>
              <th>Touch 35c</th>
              <th>Touch 40c</th>
              <th>Touch 50c</th>
            </tr>
          </thead>
          <tbody>{matrix_rows}</tbody>
        </table>
      </div>
    </section>
  </main>
</body>
</html>
"""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
