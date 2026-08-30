from __future__ import annotations

import html
from pathlib import Path

from .analytics import PRICE_AFTER_SECONDS, SCENARIO_TARGETS
from .models import (
    ActiveMarketSideView,
    LiveScenarioMatch,
    MatrixCell,
    ScenarioSummary,
    ValidatedSetup,
)


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



def render_live_decision_cards(
    active_views,
    validated_strategies,
) -> str:
    """Render the fast live decision layer.

    Historical strategy selection remains discovery-based.
    Holdout statistics are displayed as validation evidence,
    not used to rank/select candidates.
    """

    strong_strategy_map = {}

    for result in validated_strategies:
        if result.validation_status != "STRONG":
            continue

        key = (
            result.price_bucket,
            result.time_bucket,
        )

        strong_strategy_map.setdefault(
            key,
            [],
        ).append(result)

    def profit_text(value: float | None) -> str:
        if value is None:
            return "-"
        return f"{value * 100:+.2f}¢"

    def ci_text(summary) -> str:
        if (
            summary.profit_ci_low is None
            or summary.profit_ci_high is None
        ):
            return "-"

        return (
            f"{summary.profit_ci_low * 100:+.2f}¢ "
            f"to {summary.profit_ci_high * 100:+.2f}¢"
        )

    def target_price(
        entry_price: float,
        cents: int | None,
        *,
        direction: int,
    ) -> str:
        if cents is None:
            return "—"

        value = entry_price + direction * cents / 100.0

        if value < 0.0 or value > 1.0:
            return "N/A"

        return _price(value)

    def strategy_panel(view) -> str:
        matches = strong_strategy_map.get(
            (
                view.price_bucket,
                view.time_bucket,
            ),
            [],
        )

        if not matches:
            return """
            <div class="no-validated-setup">
              <div class="decision-badge neutral">
                NO VALIDATED SETUP
              </div>
              <strong>
                No STRONG mechanical strategy matches this state.
              </strong>
              <p>
                Use the historical state odds as context only.
              </p>
            </div>
            """

        # build_validated_strategies already ranks by DISCOVERY
        # CI lower bound. Keep that order here; do not re-rank
        # using holdout results.
        result = matches[0]

        strategy = result.strategy
        holdout = result.holdout_summary
        entry = (
            view.ask_price
            if view.ask_price is not None
            else view.current_price
        )

        tp = target_price(
            entry,
            strategy.take_profit_cents,
            direction=1,
        )

        sl = target_price(
            entry,
            strategy.stop_loss_cents,
            direction=-1,
        )

        if strategy.time_exit_seconds is not None:
            if strategy.take_profit_cents is not None:
                exit_rule = (
                    f"TP or exit after "
                    f"{strategy.time_exit_seconds}s"
                )
            else:
                exit_rule = (
                    f"Exit after "
                    f"{strategy.time_exit_seconds}s"
                )

        elif (
            strategy.hold_to_settlement
            and strategy.take_profit_cents is not None
            and strategy.stop_loss_cents is None
        ):
            exit_rule = "TP or settlement"

        elif (
            strategy.take_profit_cents is not None
            and strategy.stop_loss_cents is not None
        ):
            exit_rule = "TP / SL"

        else:
            exit_rule = "Hold to settlement"

        exit_candidates = [
            ("Take profit", holdout.take_profit_rate),
            ("Stop loss", holdout.stop_loss_rate),
            ("Timed exit", holdout.time_exit_rate),
            (
                "Settlement",
                holdout.settlement_exit_rate,
            ),
        ]

        exit_candidates = [
            (label, rate)
            for label, rate in exit_candidates
            if rate is not None
        ]

        if exit_candidates:
            most_common_label, most_common_rate = max(
                exit_candidates,
                key=lambda item: item[1],
            )

            most_common_exit = (
                f"{most_common_label} "
                f"{_pct(most_common_rate)}"
            )
        else:
            most_common_exit = "-"

        extra = len(matches) - 1

        extra_text = (
            ""
            if extra == 0
            else (
                f'<div class="other-setup-note">'
                f'+{extra} other STRONG setup'
                f'{"s" if extra != 1 else ""} '
                f'match this state. '
                f'See the research table below.'
                f'</div>'
            )
        )

        return """
        <div class="validated-trade-plan">
          <div class="decision-badge strong">
            STRONG VALIDATED SETUP
          </div>

          <h4>{strategy_name}</h4>

          <div class="trade-plan-grid">
            <div>
              <span>Reference Entry</span>
              <strong>{entry}</strong>
            </div>

            <div>
              <span>Take Profit</span>
              <strong>{tp}</strong>
            </div>

            <div>
              <span>Stop Loss</span>
              <strong>{sl}</strong>
            </div>

            <div>
              <span>Exit Rule</span>
              <strong>{exit_rule}</strong>
            </div>

            <div>
              <span>Historical Profitable Trades</span>
              <strong>{win_rate}</strong>
            </div>

            <div>
              <span>Most Common Historical Exit</span>
              <strong>{most_common_exit}</strong>
            </div>
          </div>

          <div class="validation-summary">
            <strong>
              Holdout avg {avg_profit}
            </strong>
            <span>
              95% CI {ci}
              · N={n:,}
              · TP {tp_rate}
              · SL {sl_rate}
            </span>
          </div>

          <p class="entry-warning">
            TP/SL above use the live displayed price as a reference.
            Use your actual fill price as the real entry when placing
            the trade. Fees and slippage are not modeled.
          </p>

          {extra_text}
        </div>
        """.format(
            strategy_name=html.escape(strategy.name),
            entry=_price(entry),
            tp=tp,
            sl=sl,
            exit_rule=html.escape(exit_rule),
            win_rate=_pct(holdout.win_rate),
            most_common_exit=html.escape(
                most_common_exit
            ),
            avg_profit=profit_text(holdout.avg_profit),
            ci=html.escape(ci_text(holdout)),
            n=holdout.observations,
            tp_rate=_pct(holdout.take_profit_rate),
            sl_rate=_pct(holdout.stop_loss_rate),
            extra_text=extra_text,
        )

    rows = []

    for view in active_views:
        rows.append(
            """
            <article class="market-card {side_class}">
              <div class="market-card-top">
                <div>
                  <span class="eyebrow">
                    {side} CONTRACT
                  </span>
                  <h3>{market}</h3>
                </div>

                <div class="live-price-block">
                  <span class="buy-label">
                    BUY {side}
                  </span>

                  <div class="live-price">
                    {buy_price}
                  </div>

                  <small>
                    Bid {bid_price}
                    · Mid {mid_price}
                  </small>

                  <small
                    class="quote-age"
                    data-quote-ts-ms="{quote_ts_ms}"
                  >
                    WEBSOCKET LIVE
                  </small>
                </div>
              </div>

              <div class="decision-state">
                <div>
                  <span>Historical State</span>
                  <strong>
                    {price_bucket} · {time_bucket}
                  </strong>
                </div>

                <div>
                  <span>Time Remaining</span>
                  <strong
                    class="live-countdown"
                    data-close-ts="{close_ts}"
                  >
                    {time_left}
                  </strong>
                </div>

                <small>
                  Historical state sample N={observations}
                </small>
              </div>

              <div class="decision-section-title">
                1 · Historical state odds
              </div>

              <div class="state-odds-grid">
                <div>
                  <span>Settles {side}</span>
                  <strong>{settle}</strong>
                </div>

                <div>
                  <span>Reaches +10¢</span>
                  <strong>{plus_10}</strong>
                </div>

                <div>
                  <span>Reaches +15¢</span>
                  <strong>{plus_15}</strong>
                </div>

                <div>
                  <span>Reaches +20¢</span>
                  <strong>{plus_20}</strong>
                </div>
              </div>

              <div class="decision-section-title">
                2 · Trade plan
              </div>

              {strategy_panel}
            </article>
            """.format(
                side=html.escape(view.side.upper()),
                side_class=(
                    "yes-side"
                    if view.side.lower() == "yes"
                    else "no-side"
                ),
                market=html.escape(view.market_ticker),
                buy_price=_price(
                    view.ask_price
                    if view.ask_price is not None
                    else view.current_price
                ),
                bid_price=_price(view.bid_price),
                mid_price=_price(view.current_price),
                quote_ts_ms=(
                    view.quote_ts_ms
                    if view.quote_ts_ms is not None
                    else 0
                ),
                close_ts=(
                    view.close_ts
                    if view.close_ts is not None
                    else 0
                ),
                price_bucket=html.escape(view.price_bucket),
                time_bucket=html.escape(view.time_bucket),
                time_left=_clock(view.seconds_remaining),
                observations=f"{view.observations:,}",
                settle=_pct(view.win_rate),
                plus_10=_pct(view.plus_10c_rate),
                plus_15=_pct(view.plus_15c_rate),
                plus_20=_pct(view.plus_20c_rate),
                strategy_panel=strategy_panel(view),
            )
        )

    if rows:
        return "\n".join(rows)

    return """
    <div class="empty-state">
      <strong>No active KXBTC15M market.</strong>
      <span>
        Waiting for the next live 15-minute contract.
      </span>
    </div>
    """


def render_live_market_fragment(
    output_path: str | Path,
    active_views,
    validated_strategies,
) -> None:
    """Write only the lightweight live market UI fragment."""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    rendered = render_live_decision_cards(
        active_views,
        validated_strategies,
    )

    temporary = output.with_name(
        output.name + ".tmp"
    )

    temporary.write_text(
        rendered,
        encoding="utf-8",
    )

    temporary.replace(output)



def render_html_report(
    output_path: str | Path,
    overview: dict[str, int | str],
    summaries: list[ScenarioSummary],
    matrix: list[MatrixCell],
    live_matches: list[LiveScenarioMatch],
    active_views: list[ActiveMarketSideView],
    validated_setups: list[ValidatedSetup],
    validated_strategies,
    refresh_seconds: int | None = None,
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

    active_cards = render_live_decision_cards(
        active_views,
        validated_strategies,
    )

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

    def _pp(value: float | None) -> str:
        return "-" if value is None else f"{value * 100:+.1f}pp"

    setup_rows = "\n".join(
        """
        <tr>
          <td>{rank}</td>
          <td><strong>{price_bucket}</strong></td>
          <td>{time_bucket}</td>
          <td>{discovery_n}</td>
          <td>{discovery_rate}</td>
          <td>{discovery_baseline}</td>
          <td><strong>{discovery_uplift}</strong></td>
          <td>{holdout_n}</td>
          <td>{holdout_rate}</td>
          <td>{holdout_baseline}</td>
          <td><strong>{holdout_uplift}</strong></td>
          <td><strong>{status}</strong></td>
        </tr>
        """.format(
            rank=index,
            price_bucket=html.escape(setup.price_bucket),
            time_bucket=html.escape(setup.time_bucket),
            discovery_n=f"{setup.discovery_path_n:,}",
            discovery_rate=_pct(setup.discovery_plus_10c_rate),
            discovery_baseline=_pct(setup.discovery_baseline_rate),
            discovery_uplift=_pp(setup.discovery_uplift),
            holdout_n=f"{setup.holdout_path_n:,}",
            holdout_rate=_pct(setup.holdout_plus_10c_rate),
            holdout_baseline=_pct(setup.holdout_baseline_rate),
            holdout_uplift=_pp(setup.holdout_uplift),
            status=html.escape(setup.validation_status),
        )
        for index, setup in enumerate(validated_setups, start=1)
    ) or """
        <tr>
          <td colspan="12">
            No discovery states meet the current validation requirements.
          </td>
        </tr>
    """

    # VALIDATED_STRATEGY_REPORT_ROWS
    def _strategy_profit(value: float | None) -> str:
        if value is None:
            return "-"
        return f"{value * 100:+.2f}¢"

    def _strategy_ci(summary) -> str:
        if (
            summary.profit_ci_low is None
            or summary.profit_ci_high is None
        ):
            return "-"
        return (
            f"{summary.profit_ci_low * 100:+.2f}¢ "
            f"to {summary.profit_ci_high * 100:+.2f}¢"
        )

    def _strategy_row(result, rank: int) -> str:
        discovery = result.discovery_summary
        holdout = result.holdout_summary
        status = result.validation_status

        return """
        <tr>
          <td>{rank}</td>
          <td><strong>{price_bucket}</strong><br>{time_bucket}</td>
          <td>{strategy}</td>
          <td>{discovery_n}</td>
          <td><strong>{discovery_avg}</strong></td>
          <td>{discovery_ci}</td>
          <td>{holdout_n}</td>
          <td><strong>{holdout_avg}</strong></td>
          <td>{holdout_ci}</td>
          <td>{ambiguity}</td>
          <td>
            <span class="strategy-status {status_class}">
              {status}
            </span>
          </td>
        </tr>
        """.format(
            rank=rank,
            price_bucket=html.escape(result.price_bucket),
            time_bucket=html.escape(result.time_bucket),
            strategy=html.escape(result.strategy.name),
            discovery_n=f"{discovery.observations:,}",
            discovery_avg=_strategy_profit(discovery.avg_profit),
            discovery_ci=html.escape(_strategy_ci(discovery)),
            holdout_n=f"{holdout.observations:,}",
            holdout_avg=_strategy_profit(holdout.avg_profit),
            holdout_ci=html.escape(_strategy_ci(holdout)),
            ambiguity=html.escape(
                f"D {_pct(discovery.ambiguous_rate)} / "
                f"H {_pct(holdout.ambiguous_rate)}"
            ),
            status=html.escape(status),
            status_class=status.lower(),
        )

    strong_strategies = [
        result
        for result in validated_strategies
        if result.validation_status == "STRONG"
    ]

    promising_strategies = [
        result
        for result in validated_strategies
        if result.validation_status == "PROMISING"
    ]

    failed_strategies = [
        result
        for result in validated_strategies
        if result.validation_status == "FAILED"
    ]

    strong_count = len(strong_strategies)
    promising_count = len(promising_strategies)
    failed_count = len(failed_strategies)

    strong_strategy_rows = "\n".join(
        _strategy_row(result, index)
        for index, result in enumerate(strong_strategies, start=1)
    ) or """
        <tr>
          <td colspan="11">
            No strategies currently meet the STRONG validation threshold.
          </td>
        </tr>
    """

    secondary_strategies = [
        result
        for result in validated_strategies
        if result.validation_status != "STRONG"
    ]

    secondary_strategy_rows = "\n".join(
        _strategy_row(result, index)
        for index, result in enumerate(secondary_strategies, start=1)
    ) or """
        <tr>
          <td colspan="11">
            No additional discovery-qualified strategies.
          </td>
        </tr>
    """

    notes = """
    Historical analysis uses real data already stored in the database. Scenario counting defaults to first entry per market unless a scenario explicitly opts into re-entry after cooldown. Markets with partial trade history are excluded from trade-based scenario analysis so they do not silently dilute the statistics.
    """

    # LIVE_FRAGMENT_POLLING
    if refresh_seconds is None:
        auto_refresh_script = ""
    else:
        auto_refresh_script = """
<script>
let lastLiveMarketHtml = null;

async function refreshLiveMarket() {
  try {
    const response = await fetch(
      "live_market.html?t=" + Date.now(),
      { cache: "no-store" }
    );

    if (!response.ok) {
      return;
    }

    const html = await response.text();

    if (
      html &&
      html !== lastLiveMarketHtml
    ) {
      const container =
        document.getElementById("live-market-grid");

      if (container) {
        container.innerHTML = html;
      }

      lastLiveMarketHtml = html;
    }
  } catch (_) {
    // Keep the last good live view during a transient error.
  }
}

refreshLiveMarket();
setInterval(refreshLiveMarket, 100);

function updateFastLiveFields() {
  const nowMs = Date.now();

  document
    .querySelectorAll(".live-countdown[data-close-ts]")
    .forEach((element) => {
      const closeTs =
        Number(element.dataset.closeTs) * 1000;

      const remainingMs = Math.max(
        0,
        closeTs - nowMs
      );

      const totalSeconds =
        Math.floor(remainingMs / 1000);

      const minutes =
        Math.floor(totalSeconds / 60);

      const seconds =
        totalSeconds % 60;

      element.textContent =
        minutes + ":" +
        String(seconds).padStart(2, "0");
    });

  document
    .querySelectorAll(".quote-age[data-quote-ts-ms]")
    .forEach((element) => {
      const quoteTs =
        Number(element.dataset.quoteTsMs);

      if (!quoteTs) {
        element.textContent = "LIVE";
        return;
      }

      const ageSeconds = Math.max(
        0,
        (nowMs - quoteTs) / 1000
      );

      element.textContent =
        "WEBSOCKET LIVE · " +
        ageSeconds.toFixed(1) +
        "s old";
    });
}

updateFastLiveFields();
setInterval(updateFastLiveFields, 100);
</script>
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
    /* SIMPLE_DECISION_UI */
    .quick-steps {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 10px;
      margin-top: 18px;
    }}

    .quick-steps div {{
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 12px;
      border-radius: 12px;
      background: white;
      border: 1px solid var(--line);
    }}

    .quick-steps strong {{
      display: grid;
      place-items: center;
      width: 28px;
      height: 28px;
      flex: 0 0 28px;
      border-radius: 50%;
      background: var(--accent);
      color: white;
    }}

    .quick-steps span {{
      font-size: 0.88rem;
    }}

    /* WEBSOCKET_LIVE_PRICE */
    .live-price-block {{
      text-align: right;
    }}

    .buy-label {{
      display: block;
      margin-bottom: 4px;
      color: var(--muted);
      font-size: 0.72rem;
      font-weight: bold;
      letter-spacing: 0.06em;
    }}

    .live-price-block small {{
      display: block;
      margin-top: 4px;
      color: var(--muted);
    }}

    .quote-age {{
      font-size: 0.72rem;
      font-weight: bold;
      color: var(--accent) !important;
    }}

    .decision-state {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-top: 14px;
    }}

    .decision-state div {{
      padding: 12px;
      border-radius: 12px;
      background: var(--bg);
    }}

    .decision-state span,
    .trade-plan-grid span,
    .state-odds-grid span {{
      display: block;
      margin-bottom: 4px;
      color: var(--muted);
      font-size: 0.76rem;
    }}

    .decision-state small {{
      grid-column: 1 / -1;
      color: var(--muted);
    }}

    .decision-section-title {{
      margin-top: 18px;
      margin-bottom: 8px;
      font-size: 0.76rem;
      font-weight: bold;
      text-transform: uppercase;
      letter-spacing: 0.07em;
      color: var(--muted);
    }}

    .state-odds-grid {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 8px;
    }}

    .state-odds-grid div {{
      padding: 12px 8px;
      text-align: center;
      border-radius: 12px;
      background: var(--accent-soft);
    }}

    .state-odds-grid strong {{
      font-size: 1.2rem;
      color: var(--accent);
    }}

    .validated-trade-plan {{
      border: 2px solid var(--accent);
      border-radius: 14px;
      padding: 14px;
      background: rgba(13, 107, 83, 0.05);
    }}

    .validated-trade-plan h4 {{
      margin: 9px 0 12px;
      font-size: 1.15rem;
    }}

    .decision-badge {{
      display: inline-block;
      padding: 5px 9px;
      border-radius: 999px;
      font-size: 0.72rem;
      font-weight: bold;
      letter-spacing: 0.05em;
    }}

    .decision-badge.strong {{
      background: var(--accent);
      color: white;
    }}

    .decision-badge.neutral {{
      background: #ece6de;
      color: var(--muted);
    }}

    .trade-plan-grid {{
      display: grid;
      grid-template-columns:
        repeat(auto-fit, minmax(125px, 1fr));
      gap: 8px;
    }}

    .trade-plan-grid div {{
      padding: 10px;
      border-radius: 10px;
      background: white;
    }}

    .trade-plan-grid strong {{
      font-size: 1.05rem;
    }}

    .validation-summary {{
      margin-top: 10px;
      padding: 10px;
      border-radius: 10px;
      background: white;
    }}

    .validation-summary strong,
    .validation-summary span {{
      display: block;
    }}

    .validation-summary span {{
      margin-top: 3px;
      color: var(--muted);
      font-size: 0.8rem;
    }}

    .entry-warning,
    .other-setup-note {{
      margin-top: 9px;
      font-size: 0.78rem;
      color: var(--muted);
    }}

    .no-validated-setup {{
      padding: 14px;
      border: 1px dashed var(--line);
      border-radius: 12px;
      background: #faf8f4;
    }}

    .no-validated-setup strong {{
      display: block;
      margin: 9px 0 5px;
    }}

    @media (max-width: 760px) {{
      .quick-steps {{
        grid-template-columns: 1fr;
      }}

      .state-odds-grid {{
        grid-template-columns: repeat(2, 1fr);
      }}
    }}

    /* LIVE_STRATEGY_MATCH_STYLES */
    .live-strategy-panel {{
      margin-top: 16px;
      border-top: 1px solid var(--line);
      padding-top: 14px;
    }}

    .live-strategy-heading {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      margin-bottom: 10px;
    }}

    .live-strategy-heading span {{
      color: var(--muted);
      font-size: 0.82rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}

    .live-strategy-heading strong {{
      color: var(--accent);
    }}

    .live-strategy-match {{
      border: 1px solid var(--line);
      border-radius: 12px;
      background: rgba(13, 107, 83, 0.05);
      padding: 12px;
      margin-top: 9px;
    }}

    .live-strategy-name {{
      font-weight: 700;
      margin-bottom: 9px;
    }}

    .live-strategy-metrics {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }}

    .live-strategy-metrics div {{
      background: rgba(255, 255, 255, 0.72);
      border-radius: 9px;
      padding: 9px;
    }}

    .live-strategy-metrics span,
    .live-strategy-metrics small {{
      display: block;
      color: var(--muted);
    }}

    .live-strategy-metrics strong {{
      display: block;
      margin: 3px 0;
      color: var(--accent);
      font-size: 1.05rem;
    }}

    .live-exit-behavior {{
      margin-top: 9px;
      font-size: 0.82rem;
      color: var(--muted);
    }}

    .live-strategy-empty {{
      margin-top: 14px;
      padding-top: 12px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 0.9rem;
    }}

    /* VALIDATED_STRATEGY_STYLES */
    .strategy-summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
      margin: 18px 0;
    }}
    .strategy-status {{
      display: inline-block;
      padding: 5px 9px;
      border-radius: 999px;
      font-size: 0.78rem;
      font-weight: 700;
      letter-spacing: 0.04em;
    }}
    .strategy-status.strong {{
      background: var(--accent-soft);
      color: var(--accent);
    }}
    .strategy-status.promising {{
      background: #f6ead2;
      color: #76520f;
    }}
    .strategy-status.failed {{
      background: #f5ded7;
      color: var(--warn);
    }}
    .strategy-note {{
      margin-top: 14px;
      padding: 12px 14px;
      border-left: 4px solid var(--accent);
      background: rgba(13, 107, 83, 0.06);
      border-radius: 8px;
    }}
    details.strategy-details {{
      margin-top: 18px;
    }}
    details.strategy-details summary {{
      cursor: pointer;
      font-weight: 700;
      color: var(--accent);
      margin-bottom: 12px;
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
      <h1>BTC 15-Min Decision Dashboard</h1>
      <p>
        Use the live YES/NO cards below to see the current historical
        state, the observed outcome rates, and whether a validated
        mechanical setup matches.
      </p>

      <div class="quick-steps">
        <div>
          <strong>1</strong>
          <span>Choose the YES or NO side you are evaluating.</span>
        </div>

        <div>
          <strong>2</strong>
          <span>Read the historical settle and price-move rates.</span>
        </div>

        <div>
          <strong>3</strong>
          <span>
            Only follow a mechanical plan when a
            STRONG VALIDATED SETUP is shown.
          </span>
        </div>
      </div>
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
    <section class="live-section">
      <div class="section-heading">
        <div>
          <h2>Current Market</h2>
          <p>
            Current YES and NO prices mapped to the exact historical
            price/time state. Settlement odds and price-move probabilities
            describe what happened historically from comparable states;
            validated strategy matches appear directly on each side.
          </p>
        </div>
      </div>

      <div
        class="live-grid"
        id="live-market-grid"
      >
        {active_cards}
      </div>
    </section>
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
          <h3>Validated Setup Finder</h3>
          <p>
            Finds unusual price/time states in the earlier discovery sample,
            then checks those same states against a later unseen holdout
            sample. Compare the +10¢ rate with the same-price baseline and
            focus on whether the uplift persists out of sample.
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

    <!-- VALIDATED_STRATEGY_FINDER -->
    <section class="strategy-section">
      <div class="section-heading">
        <div>
          <h2>Validated Strategy Finder</h2>
          <p>
            Mechanical exit strategies discovered on the earlier 80% of
            historical markets and tested against the later unseen 20%.
            STRONG means the simulated average return's 95% confidence
            interval remained entirely above zero in both samples.
          </p>
        </div>
      </div>

      <div class="strategy-summary">
        <div class="stat">
          <strong>{strong_count}</strong>
          <span>STRONG</span>
        </div>
        <div class="stat">
          <strong>{promising_count}</strong>
          <span>PROMISING</span>
        </div>
        <div class="stat">
          <strong>{failed_count}</strong>
          <span>FAILED HOLDOUT</span>
        </div>
        <div class="stat">
          <strong>{len(validated_strategies)}</strong>
          <span>DISCOVERY CI-POSITIVE</span>
        </div>
      </div>

      <h3>STRONG strategies</h3>
      <p>
        These are the primary out-of-sample survivors. Ranking remains based
        on discovery data only; holdout is used only to validate the candidate.
      </p>

      <div class="table-wrap" style="margin-top: 14px;">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Price / Time State</th>
              <th>Exit Strategy</th>
              <th>Discovery N</th>
              <th>Discovery Avg</th>
              <th>Discovery 95% CI</th>
              <th>Holdout N</th>
              <th>Holdout Avg</th>
              <th>Holdout 95% CI</th>
              <th>Ambiguity</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>{strong_strategy_rows}</tbody>
        </table>
      </div>

      <details class="strategy-details">
        <summary>
          Show PROMISING and FAILED discovery-qualified strategies
        </summary>

        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Price / Time State</th>
                <th>Exit Strategy</th>
                <th>Discovery N</th>
                <th>Discovery Avg</th>
                <th>Discovery 95% CI</th>
                <th>Holdout N</th>
                <th>Holdout Avg</th>
                <th>Holdout 95% CI</th>
                <th>Ambiguity</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>{secondary_strategy_rows}</tbody>
          </table>
        </div>
      </details>

      <p class="strategy-note">
        Historical simulation only. Positive historical average return and
        confidence intervals do not guarantee future profitability. Current
        results also do not yet correct the strategy search for all
        multiple-comparison/data-mining effects, fees, or slippage.
      </p>
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
          <h2>Validated Setup Finder</h2>
          <p>
            Price/time states are discovered using the earlier 80% of
            historical markets, then tested on the later 20% that was not
            used to select them. Ranking is based only on discovery data.
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
              <th>Discovery Eligible N</th>
              <th>Discovery +10¢</th>
              <th>Discovery Baseline</th>
              <th>Discovery Uplift</th>
              <th>Holdout Eligible N</th>
              <th>Holdout +10¢</th>
              <th>Holdout Baseline</th>
              <th>Holdout Uplift</th>
              <th>Validation</th>
            </tr>
          </thead>
          <tbody>
            {setup_rows}
          </tbody>
        </table>
      </div>

      <div class="note">
        <strong>How validation works:</strong>
        Discovery is the earlier 80% of markets and is the only data used
        to select and rank candidates. Holdout is the later 20% and is used
        only afterward to test whether the pattern continued.
        Baseline is the +10¢ reach rate for the same price bucket at all
        other time buckets. Uplift is the candidate's +10¢ rate minus that
        same-price baseline.
        <strong>PERSISTED</strong> means at least 100 eligible holdout
        observations and at least +2.0 percentage points of holdout uplift.
        <strong>WEAK</strong> means the holdout uplift remained positive but
        below +2.0pp. <strong>FAILED</strong> means holdout uplift was zero
        or negative. <strong>INSUFFICIENT</strong> means there was not enough
        eligible holdout data. These are historical validation labels, not
        profitability guarantees.
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
  {auto_refresh_script}
</body>
</html>
"""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
