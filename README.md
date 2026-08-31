# Kalshi BTC15M Research System

A research platform for studying Kalshi's 15-minute Bitcoin
prediction markets (`KXBTC15M`).

The project combines:

- historical market reconstruction,
- live Kalshi market collection,
- official BRTI tracking,
- BTC market features,
- strategy discovery,
- chronological historical replay,
- realistic paper execution,
- prospective strategy validation,
- adaptive portfolio research,
- and, eventually, machine-learning-based probability and
  execution models.

The system is currently **research and paper trading only**.

Real-money automated execution is not enabled.

---

## Project Goal

The long-term objective is not to find one fixed trading rule.

The goal is to build a continuously learning research system
that can:

1. observe historical and live BTC/Kalshi data,
2. generate candidate strategies,
3. test them without lookahead,
4. paper trade them with realistic execution assumptions,
5. determine which apparent edges survive forward validation,
6. detect changing market regimes,
7. dynamically choose among sufficiently validated strategies,
8. pass when no reliable edge exists,
9. eventually support tightly guarded live execution.

Historical profitability is never treated as proof of future
profitability.

---

## Core Research Principle

The project separates:

DISCOVERY
    ↓
HISTORICAL SCREENING
    ↓
OUT-OF-SAMPLE TESTING
    ↓
PROSPECTIVE PAPER EXECUTION
    ↓
EXECUTION ELIGIBILITY
    ↓
ACTIVE / PASS

Strategies are frozen once created.

A strategy cannot be modified using the same forward data that
is currently evaluating it.

A modified strategy becomes a new version with a new forward
clock.

---

## Current Architecture

The system currently contains several major layers.

### Data collection

Live and historical data include:

- Kalshi KXBTC15M market prices,
- bid/ask quotes,
- top-of-book size,
- official Kalshi BRTI,
- BTC spot/reference data,
- synchronized feature snapshots,
- completed market outcomes.

Feature snapshots include variables such as:

- seconds remaining,
- contract price,
- BTC distance from threshold,
- threshold distance in dollars/bps/volatility units,
- 30s / 60s / 180s / 300s returns,
- EMA spreads,
- EMA slopes,
- VWAP distances,
- realized volatility,
- ranges,
- volume / relative volume,
- trade imbalance,
- book imbalance.

### Strategy research

Current strategy families include:

MAIN_TRIGGER
MAIN_CONTEXT
MICRO_MULTIPLIER
GRID_V1
TAIL_V1

`GRID_V1` provides broad combinations of:

- contract-price ranges,
- time-remaining ranges,
- TP/SL rules,
- settlement exits.

`TAIL_V1` extends experiments into extreme low- and high-priced
contracts.

The challenger generator can create additional frozen strategy
versions when research identifies an interesting subdivision.

---

## Historical Replay

Historical replay is implemented in:

src/kalshi_stats/historical_replay.py

It walks historical market observations chronologically.

A simulated strategy decision may only use information that
existed at that timestamp.

Settlement outcomes are not used to make entry decisions.

Historical replay is used for:

- strategy screening,
- discovering leads,
- studying payoff structure,
- preparing prospective experiments.

Historical replay does **not** count as prospective proof.

Known historical replay limitations include:

- no true historical full-depth order book,
- no true historical IOC latency,
- no historical queue position,
- simplified execution assumptions,
- estimated fees.

---

## PaperBroker

The realistic live paper execution engine is implemented in:

src/kalshi_stats/paper_broker.py

The design goal is:

strategy
   ↓
trade intent
   ↓
execution engine
   ↓
PaperBroker

with a future real broker using the same trading intent and
risk logic.

Current PaperBroker behavior includes:

- approximately $1 paper trade notional per signal,
- approximately $10 virtual starting bankroll per experimental
  strategy,
- IOC-style entry limits,
- observed best-price liquidity constraints,
- partial fills,
- TP handling,
- stop triggering,
- gap/slippage handling,
- settlement of residual positions,
- fee estimates,
- per-strategy virtual accounting.

Each strategy bankroll is an independent research experiment.

The sum of all strategy bankrolls is **not** one deployable
portfolio.

---

## Paper Snapshot

The full PaperBroker research state can be exported with:

./snapshot.sh

This writes:

reports/paper_engine_snapshot.json

The snapshot contains:

- all registered strategies,
- paper accounts,
- paper trades,
- fills,
- no-fill diagnostics,
- current score snapshots,
- market results,
- signal-time feature context,
- scan cursors.

This is the preferred file for analyzing the entire live paper
experiment.

---

## Current Execution Findings

Observed live paper execution has shown that many `NO_FILL`
events occur because:

PRICE_MOVED_ABOVE_IOC_LIMIT

A common pattern is:

signal ask
    ↓
~hundreds of milliseconds
    ↓
ask has moved roughly one tick higher
    ↓
strict IOC refuses to chase

Multiple strategies often share the same entry condition, so
many strategy-level `NO_FILL` rows may represent one underlying
market/side execution episode.

Always distinguish:

strategy rows
unique markets
unique market/side episodes

Larger future trade sizes will require stronger liquidity and
depth modeling.

---

## Adaptive Portfolio Research

The project also includes historical walk-forward adaptive
portfolio experiments.

### Adaptive V1

Adaptive V1 ranked strategies largely using historical ROI.

It failed badly because binary-market returns are highly
asymmetric.

Cheap contracts can generate rare enormous percentage wins,
causing mean ROI to look much more reliable than it really is.

### Adaptive V2

Adaptive V2 uses Bayesian settlement-probability estimates and
fee-adjusted breakeven probabilities.

At high portfolio risk, it still produced unacceptable
drawdowns.

At approximately 1% bankroll risk per selected trade,
historical walk-forward results were much safer.

Example research result:

BAYES_FAST
start           ~$10
finish          ~$12.18
historical ROI  ~+21.8%
max drawdown    ~23.4%

This remains retrospective research evidence only.

Performance was strongly regime-dependent, motivating the next
research layer.

---

## Planned Machine Learning Layer

The next major research phase is intended to estimate market
probabilities directly rather than only choosing from manually
defined strategies.

Initial ML benchmark:

historical observable features
          ↓
probability model
          ↓
P(YES) / P(NO)
          ↓
compare with Kalshi price
          ↓
fee-adjusted estimated edge

The first models should be:

1. logistic regression,
2. gradient-boosted decision trees.

Validation must be chronological.

Random train/test splits must not be used for time-series
performance claims.

Important metrics include:

- Brier score,
- log loss,
- calibration,
- probability error,
- market-relative edge,
- fee-adjusted expected value.

Possible later research includes:

- regime/change-point detection,
- online learning,
- expert weighting,
- contextual bandits,
- execution/fill prediction,
- slippage modeling,
- evolutionary challenger generation.

Reinforcement learning is intentionally deferred until the
execution simulator is substantially more realistic.

---

## Research Agent Direction

A future repository-level research agent may help automate:

observe
  ↓
analyze
  ↓
propose experiment
  ↓
backtest
  ↓
walk-forward validation
  ↓
tests
  ↓
prospective paper validation
  ↓
report

The agent may assist development and research.

It must not be allowed to silently:

- weaken risk controls,
- change promotion standards,
- expose private credentials,
- enable live-money execution,
- promote a backtest directly to production.

Research autonomy and trading authority should remain separate.

---

## Running the System

Activate the project environment and launch the supervised
processes with:

cd ~/stats
./start.sh

The launcher starts the project's live collectors, research
processes, dashboard generation, strategy tracking, and
PaperBroker components.

The exact processes should be verified against the current
`start.sh`, because this project evolves frequently.

---

## Tests

Run the complete test suite with:

cd ~/stats

PYTHONPATH=src .venv/bin/python \
  -m compileall -q src tests

PYTHONPATH=src .venv/bin/python \
  -m pytest -q

bash -n start.sh
bash -n snapshot.sh

git diff --check

Do not commit research-engine changes while tests are failing.

---

## Database

Primary live/research SQLite database:

data/kalshi_stats_snapshot.sqlite

For heavy historical research, prefer a SQLite backup rather
than stressing the live database:

rm -f /tmp/kalshi_historical_replay.sqlite

sqlite3 \
  data/kalshi_stats_snapshot.sqlite \
  ".backup '/tmp/kalshi_historical_replay.sqlite'"

Then use the frozen copy for historical replay/model training.

---

## Important Files

Common entry points include:

src/kalshi_stats/database.py
src/kalshi_stats/market_sync.py
src/kalshi_stats/live_monitor.py
src/kalshi_stats/kalshi_api.py
src/kalshi_stats/kalshi_account.py
src/kalshi_stats/kalshi_ws.py

src/kalshi_stats/paper_broker.py
src/kalshi_stats/paper_snapshot.py

src/kalshi_stats/strategy_zoo.py
src/kalshi_stats/tail_zoo.py
src/kalshi_stats/challenger_generator.py

src/kalshi_stats/historical_replay.py
src/kalshi_stats/historical_adaptive.py
src/kalshi_stats/historical_adaptive_v2.py

src/kalshi_stats/reporting.py
src/kalshi_stats/health.py

See:

AGENTS.md
docs/RESEARCH_SYSTEM.md

for the detailed scientific and agent-development rules.

---

## Safety / Credentials

Never commit:

- private keys,
- API secrets,
- `.env` credentials.

Read-only and write-capable Kalshi access should remain
architecturally separate.

Real-money automated execution is currently disabled.

---

## Research Status

The system is actively accumulating prospective evidence.

No strategy should currently be interpreted as guaranteed
profitable.

A valid research outcome is:

No demonstrated edge under the tested execution constraints.

The purpose of this project is to discover whether robust edge
exists, not to force a profitable conclusion.
