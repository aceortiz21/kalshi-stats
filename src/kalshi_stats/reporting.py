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
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}:{seconds:02d}"


def _sample_badge(summary: ScenarioSummary) -> str:
    return "Low sample" if summary.low_sample_warning else "OK"


def _targets(summary: ScenarioSummary) -> str:
    parts = []
    for target in SCENARIO_TARGETS:
        hit_rate = summary.target_hit_rates[target]
        count = summary.target_touch_counts[target]
        eligible = summary.target_eligible_counts[target]
        median_time = summary.median_time_to_targets[target]

        if eligible == 0:
            parts.append(f"{int(target * 100)}c N/A (0 eligible)")
        else:
            parts.append(
                f"{int(target * 100)}c {_pct(hit_rate)} "
                f"({count}/{eligible} eligible), med {_seconds(median_time)}"
            )

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
          <td>{path_n}</td>
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
            path_n=summary.path_observations,
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
          <td>{path_observations}</td>
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
            path_observations=cell.path_observations,
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

    # Candidate states are deliberately selected with transparent rules.
    # This is descriptive historical screening, not a profitability claim.
    setup_candidates = sorted(
        (
            cell
            for cell in matrix
            if cell.path_observations >= 500
            and cell.plus_10c_rate is not None
        ),
        key=lambda cell: (
            cell.plus_10c_rate,
            cell.plus_15c_rate or 0.0,
            cell.path_observations,
        ),
        reverse=True,
    )[:12]

    setup_rows = "\n".join(
        """
        <tr>
          <td>{rank}</td>
          <td><strong>{price_bucket}</strong></td>
          <td>{time_bucket}</td>
          <td>{path_n}</td>
          <td>{win_rate}</td>
          <td>{plus_5}</td>
          <td><strong>{plus_10}</strong></td>
          <td>{plus_15}</td>
          <td>{plus_20}</td>
          <td>{median_best}</td>
        </tr>
        """.format(
            rank=index,
            price_bucket=html.escape(cell.price_bucket),
            time_bucket=html.escape(cell.time_bucket),
            path_n=f"{cell.path_observations:,}",
            win_rate=_pct(cell.win_rate),
            plus_5=_pct(cell.plus_5c_rate),
            plus_10=_pct(cell.plus_10c_rate),
            plus_15=_pct(cell.plus_15c_rate),
            plus_20=_pct(cell.plus_20c_rate),
            median_best=_price(cell.median_best_subsequent_price),
        )
        for index, cell in enumerate(setup_candidates, start=1)
    ) or """
        <tr>
          <td colspan="10">No matrix states meet the current candidate requirements.</td>
        </tr>
    """

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
    .section-heading {{
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 18px;
    }}

    .section-heading p {{
      max-width: 760px;
    }}

    .live-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
      gap: 16px;
      margin-top: 18px;
    }}

    .market-card {{
      padding: 20px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: white;
    }}

    .market-card.yes-side {{
      border-top: 4px solid var(--accent);
    }}

    .market-card.no-side {{
      border-top: 4px solid var(--warn);
    }}

    .market-card-top {{
      display: flex;
      justify-content: space-between;
      align-items: start;
      gap: 16px;
      padding-bottom: 18px;
      border-bottom: 1px solid var(--line);
    }}

    .market-card h3 {{
      margin: 4px 0 0;
      font-size: 1.05rem;
      overflow-wrap: anywhere;
    }}

    .eyebrow,
    .section-label {{
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-size: 0.72rem;
      font-weight: bold;
      color: var(--muted);
    }}

    .live-price {{
      font-size: 2.5rem;
      line-height: 1;
      font-weight: bold;
      color: var(--accent);
    }}

    .no-side .live-price {{
      color: var(--warn);
    }}

    .market-context,
    .market-footer {{
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 12px;
      margin-top: 16px;
    }}

    .market-context div,
    .market-footer div {{
      padding: 12px;
      background: var(--bg);
      border-radius: 12px;
    }}

    .market-context span,
    .market-footer span,
    .scenario-line span {{
      display: block;
      font-size: 0.76rem;
      color: var(--muted);
      margin-bottom: 5px;
    }}

    .market-context strong,
    .market-footer strong {{
      font-size: 1.05rem;
    }}

    .section-label {{
      margin-top: 20px;
      margin-bottom: 8px;
    }}

    .target-grid {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 8px;
    }}

    .target-grid div {{
      padding: 14px 8px;
      text-align: center;
      border-radius: 12px;
      background: var(--accent-soft);
    }}

    .target-grid span {{
      display: block;
      font-size: 0.76rem;
      color: var(--muted);
      margin-bottom: 4px;
    }}

    .target-grid strong {{
      font-size: 1.25rem;
      color: var(--accent);
    }}

    .scenario-line {{
      margin-top: 16px;
      padding-top: 14px;
      border-top: 1px solid var(--line);
    }}

    .empty-state {{
      padding: 28px;
      border: 1px dashed var(--line);
      border-radius: 14px;
      text-align: center;
      background: white;
    }}

    .empty-state strong,
    .empty-state span {{
      display: block;
    }}

    .empty-state span {{
      margin-top: 6px;
      color: var(--muted);
    }}

    @media (max-width: 650px) {{
      .live-grid {{
        grid-template-columns: 1fr;
      }}

      .target-grid {{
        grid-template-columns: repeat(2, 1fr);
      }}

      .market-context {{
        grid-template-columns: 1fr 1fr;
      }}
    }}


    .guide-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
      gap: 12px;
      margin-top: 16px;
    }}

    .guide-card {{
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: white;
    }}

    .guide-card h3 {{
      margin: 0 0 7px;
      font-size: 1rem;
      color: var(--accent);
    }}

    .guide-card p {{
      font-size: 0.88rem;
      line-height: 1.45;
    }}

    .guide-card strong {{
      color: var(--ink);
    }}

    .guide-workflow {{
      margin-top: 16px;
      padding: 16px 18px;
      border-radius: 14px;
      background: var(--accent-soft);
    }}

    .guide-workflow strong {{
      display: block;
      margin-bottom: 7px;
    }}

    .guide-workflow p {{
      color: var(--ink);
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
      <h1>BTC 15-Min Historical Edge Dashboard</h1>
      <p>
        Live KXBTC15M market states compared against thousands of historical
        observations. Built for research, context, and probability—not automated
        trade recommendations.
      </p>
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
      <div class="section-heading">
        <div>
          <h2>How to Read This Dashboard</h2>
          <p>
            Quick reference for the statistics used throughout the page.
            See USER_GUIDE.md for the complete explanation of every scenario
            and metric.
          </p>
        </div>
      </div>

      <div class="guide-grid">

        <div class="guide-card">
          <h3>N</h3>
          <p>
            Number of qualifying historical observations. Use this primarily
            when interpreting settlement statistics such as <strong>Win Rate</strong>.
          </p>
        </div>

        <div class="guide-card">
          <h3>Path N</h3>
          <p>
            Observations with valid future price data. Always check this before
            trusting <strong>+5¢/+10¢ reach, Max Price, MFE, or MAE</strong>.
          </p>
        </div>

        <div class="guide-card">
          <h3>Win Rate</h3>
          <p>
            Percentage of historical observations where the studied side
            eventually settled as the winner. This is different from the
            probability of a temporary rebound.
          </p>
        </div>

        <div class="guide-card">
          <h3>+5¢ / +10¢ / +15¢ / +20¢</h3>
          <p>
            Relative price movement after the historical state. A +10¢ result
            from 23¢ means the contract later reached approximately
            <strong>33¢ or higher</strong>.
          </p>
        </div>

        <div class="guide-card">
          <h3>Median Max</h3>
          <p>
            Median highest subsequent contract price. This describes historical
            opportunity—not a price you could have known to exit at in real time.
          </p>
        </div>

        <div class="guide-card">
          <h3>MFE</h3>
          <p>
            Maximum Favorable Excursion: the largest favorable move after the
            trigger. Absolute cents are usually easier to interpret than MFE %
            for very cheap contracts.
          </p>
        </div>

        <div class="guide-card">
          <h3>MAE</h3>
          <p>
            Maximum Adverse Excursion: the largest unfavorable move after the
            trigger. It describes historical downside movement after entry.
          </p>
        </div>

        <div class="guide-card">
          <h3>Target Touch</h3>
          <p>
            A target such as <strong>40¢</strong> is an absolute contract price.
            This is different from <strong>+10¢</strong>, which is relative to
            the entry state.
          </p>
        </div>

        <div class="guide-card">
          <h3>Price × Time State</h3>
          <p>
            Historical observations grouped by both contract price and time
            remaining. A 15¢ contract with 12 minutes left should not be treated
            like a 15¢ contract with 30 seconds left.
          </p>
        </div>

        <div class="guide-card">
          <h3>Setup Finder</h3>
          <p>
            Automatically surfaces high-sample historical states. These are
            <strong>research candidates</strong>, not validated trading signals.
          </p>
        </div>

        <div class="guide-card">
          <h3>95% CI</h3>
          <p>
            Statistical uncertainty around the observed Win Rate. A narrow
            interval generally reflects greater sampling precision, not a
            guarantee about future markets.
          </p>
        </div>

        <div class="guide-card">
          <h3>Unique Markets</h3>
          <p>
            Number of distinct 15-minute markets represented. This helps show
            whether a result is broadly distributed rather than repeatedly
            generated by only a few markets.
          </p>
        </div>

      </div>

      <div class="guide-workflow">
        <strong>Live-market reading order</strong>
        <p>
          Price + time remaining → comparable historical state → Path N →
          +5¢/+10¢/+15¢/+20¢ reach → Median Max → Win Rate → matching named
          scenarios → current BTC context. Historical frequency is context,
          not a guarantee or proof of profitability.
        </p>
      </div>
    </section>

    <section class="live-section">
      <div class="section-heading">
        <div>
          <h2>Current Market</h2>
          <p>
            Live YES and NO prices mapped directly to historically comparable
            price and time states.
          </p>
        </div>
      </div>

      <div class="live-grid">
        {active_cards}
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
              <th>Path N</th>
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
      <div class="section-heading">
        <div>
          <h2>Historical Setup Finder</h2>
          <p>
            High-sample historical price/time states ranked by subsequent
            +10¢ reach rate. Candidates require at least 500 valid path
            observations. These are research candidates, not validated
            trading signals.
          </p>
        </div>
      </div>

      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Rank</th>
              <th>Price</th>
              <th>Time Left</th>
              <th>Path N</th>
              <th>Win Rate</th>
              <th>+5¢</th>
              <th>+10¢</th>
              <th>+15¢</th>
              <th>+20¢</th>
              <th>Median Max</th>
            </tr>
          </thead>
          <tbody>
            {setup_rows}
          </tbody>
        </table>
      </div>

      <div class="note">
        Ranking is intentionally simple: +10¢ historical reach rate first,
        then +15¢ reach rate and sample size. We will validate promising
        states on unseen data before treating them as evidence of a
        repeatable pattern.
      </div>
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
              <th>Path N</th>
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
