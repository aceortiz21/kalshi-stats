# Kalshi Stats — Agent Instructions

## Mission

This repository is a research system for Kalshi KXBTC15M
15-minute Bitcoin markets.

The long-term goal is to develop a continuously learning,
strictly validated trading research system that can:

1. collect live and historical market data,
2. generate frozen strategy candidates,
3. replay them chronologically,
4. paper trade them under realistic execution assumptions,
5. measure prospective performance,
6. discover feature-conditioned challengers,
7. dynamically select among sufficiently validated strategies,
8. eventually support guarded real execution.

Profitability must NEVER be assumed or guaranteed.

If tested methods do not demonstrate an edge, report that clearly.

## Critical scientific rules

Never introduce lookahead leakage.

Historical decisions may use only information that existed at
the simulated timestamp.

Settlement outcomes may only be consumed after the simulated
market path reaches settlement.

Do not mutate a strategy using the same forward data currently
judging it.

Every newly generated strategy must have:

- immutable definition,
- unique version/key,
- creation timestamp,
- discovery cutoff,
- fresh forward-only start timestamp.

Historical replay is discovery/screening evidence only.

Historical success is NOT prospective proof.

Promotion should conceptually follow:

RESEARCH
-> HISTORICAL LEAD
-> OUT-OF-SAMPLE PASS
-> PROSPECTIVE EXECUTION PASS
-> EXECUTION ELIGIBLE
-> ACTIVE
-> DEGRADED / DISABLED

## Execution rules

Current real-money execution is disabled.

Do not add or enable live order submission unless the user
explicitly requests a final execution switch.

Never request or print private keys.

Keep public/read-only market access separate from future
write-capable broker credentials.

Paper execution should remain conservative.

Known limitations:

- historical replay lacks true historical full-depth books,
- historical replay lacks real IOC latency,
- queue priority is unknown,
- current fee model is approximate,
- top-of-book is insufficient for larger sizing.

Do not silently weaken execution realism to improve results.

## Current paper architecture

Each strategy has an independent virtual paper account.

Current baseline:

- starting cash: $10 per experimental strategy account
- trade notional: about $1 per signal

These accounts are research experiments.

Their aggregate P&L MUST NOT be interpreted as one deployable
portfolio.

A future adaptive portfolio must use one finite bankroll and
must be able to PASS.

## Existing strategy families

- MAIN_TRIGGER
- MAIN_CONTEXT
- MICRO_MULTIPLIER
- GRID_V1
- TAIL_V1

GRID_V1 contains broad price/time/exit strategies.

TAIL_V1 covers extreme contract prices.

The challenger generator may add new frozen strategies.

Do not edit existing frozen definitions merely because new
results are unfavorable.

## Historical replay

`historical_replay.py` performs chronological retrospective
screening.

Current replay has processed roughly:

- 5,891 historical markets
- 643k strategy-level simulated trades
- hundreds of strategy definitions

Historical replay uses approximately $1 fixed trade sizing and
fee estimates.

Replay results do not model historical depth, queue position,
or real IOC latency.

## Adaptive research

Adaptive V1 failed catastrophically because raw mean ROI was
dominated by asymmetric cheap-contract outcomes.

Adaptive V2 uses Bayesian settlement probability estimates and
fee-adjusted breakeven probabilities.

At 10% bankroll risk:

- BAYES_FAST historically grew strongly but suffered roughly
  80% maximum drawdown.
- BAYES_STRICT lost money.

At 1% bankroll risk:

- BAYES_FAST finished around $12.18 from $10,
  about +21.8%, with roughly 23.4% max drawdown.
- BAYES_STRICT finished around $9.62,
  about -3.8%, with roughly 10.2% max drawdown.

These are historical walk-forward research results, not proof.

FAST's gains were heavily concentrated in a later regime,
which motivates regime-aware modeling.

## Live PaperBroker observations

The live experiment currently has 700+ strategy accounts and
over 1,000 strategy-level signals.

Many trades are correlated because numerous strategies act on
the same underlying market/side episode.

Always distinguish:

- strategy rows
- unique markets
- unique market/side episodes

Many NO_FILL rows are duplicates across strategy variants.

Observed NO_FILL behavior has overwhelmingly been:

PRICE_MOVED_ABOVE_IOC_LIMIT

Typical miss:

- strict signal ask used as IOC limit,
- first executable book roughly 400 ms later,
- ask moved about one cent.

Do not simply loosen limits.

Future research should compare execution policies
prospectively.

## Snapshot workflow

Run:

./snapshot.sh

This writes:

reports/paper_engine_snapshot.json

Use that file for complete PaperBroker evaluation.

## Current ML direction

Do NOT jump directly to deep RL.

Recommended sequence:

1. Build supervised probability dataset.
2. Predict P(YES)/P(NO) from only contemporaneous features.
3. Benchmark logistic regression first.
4. Benchmark gradient-boosted trees second.
5. Use chronological walk-forward validation only.
6. Evaluate Brier score, log loss, calibration, and
   fee-adjusted market edge.
7. Add regime/change-point features.
8. Explore contextual bandits / expert weighting for strategy
   selection.
9. Train a separate fill/slippage model from prospective
   PaperBroker execution observations.
10. Consider RL only after execution simulation fidelity is
    substantially stronger.

Relevant features already collected include:

- contract price
- seconds remaining
- BTC distance to threshold
- 30s / 60s / 180s / 300s returns
- EMA relationships and slopes
- VWAP distances
- realized volatility
- ranges
- relative volume
- trade imbalance
- book imbalance
- BRTI context where available

## Development behavior

Prefer the smallest safe change.

Before modifying code:

1. inspect the relevant implementation,
2. inspect related tests,
3. understand schema dependencies.

After every meaningful patch run:

PYTHONPATH=src .venv/bin/python -m compileall -q src tests
PYTHONPATH=src .venv/bin/python -m pytest -q
git diff --check

Do not commit if tests fail.

Do not invent expected test counts.

Before a commit:

git status --short

Commit only the intended files.

Never delete historical/live data merely to make metrics look
better.

## Git

Repository:
aceortiz21/kalshi-stats

Working branch has historically been:
tonight-stabilize

Check the actual current branch before making assumptions.

## Immediate next research direction

Do not add more giant hand-authored grids yet.

The next major research layer should be:

- ML dataset generation
- chronological probability-model benchmark
- regime-aware adaptive selector
- expert-weighted/contextual strategy selection
- continued prospective PaperBroker evidence
- eventually full-depth orderbook reconstruction

Keep all of this research-only until prospective evidence is
strong enough to justify promotion.
