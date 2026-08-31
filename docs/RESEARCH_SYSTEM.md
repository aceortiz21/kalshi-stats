# Kalshi BTC15M Research System

## Detailed Research Architecture and Current State

This document preserves the scientific architecture, major
research findings, current assumptions, and future direction of
the repository.

It should be read together with:

README.md
AGENTS.md

The implementation is the final source of truth.

If documentation and code disagree, inspect the code and tests
before making changes.

---

# 1. Research Objective

The project studies Kalshi's 15-minute Bitcoin markets,
primarily the `KXBTC15M` series.

The intended long-term system is not a static bot using one
hard-coded trading strategy.

The goal is a continuously improving research platform that
can:

collect data
    ↓
measure market state
    ↓
generate hypotheses
    ↓
historically screen hypotheses
    ↓
freeze candidate strategy
    ↓
start fresh forward clock
    ↓
prospectively paper trade
    ↓
evaluate execution + edge
    ↓
promote / retain / retire
    ↓
select among validated strategies

The system must also be able to choose:

PASS

when evidence is insufficient.

---

# 2. Scientific Standard

The project should optimize for valid evidence rather than
impressive backtests.

The central rules are:

## No lookahead

A simulated decision can use only information available at
that timestamp.

Do not allow:

- future price observations,
- future feature values,
- settlement results,
- future model state,
- future strategy performance

to influence a historical decision.

## Frozen strategies

Once a strategy begins forward evaluation, its definition is
immutable.

Do not tune the same strategy using data currently evaluating
it.

A modification creates a new strategy version and receives a
new:

- strategy key,
- creation time,
- discovery cutoff,
- forward start.

## Evidence classes must remain separate

Do not mix:

historical discovery evidence
historical holdout evidence
retrospective walk-forward evidence
prospective shadow evidence
paper-execution evidence
real-money execution evidence

Historical success does not automatically become prospective
success.

## Multiple hypothesis testing matters

Hundreds of strategy candidates create many opportunities for
luck.

The best performer out of hundreds is expected to look
impressive even if every strategy has zero true edge.

Evaluation should increasingly consider:

- independent market count,
- correlation between strategies,
- confidence intervals,
- bootstrap methods,
- false-discovery control,
- chronological holdouts,
- prospective evidence,
- execution realism.

---

# 3. Promotion Model

Conceptual strategy lifecycle:

RESEARCH
    ↓
HISTORICAL LEAD
    ↓
OUT-OF-SAMPLE PASS
    ↓
PROSPECTIVE EXECUTION PASS
    ↓
EXECUTION ELIGIBLE
    ↓
ACTIVE
    ↓
DEGRADED / DISABLED

No historical result alone should make a strategy
execution-eligible.

---

# 4. Historical Main Strategy Result

A previously locked historical evaluation exists for the
original main trigger family:

entry region:
60–69c

time remaining:
5–10 minutes

exit:
TP +25c
SL -5c

Historical locked evaluation:

usable markets        1179
trades                 685
average gross P&L      +2.226c
95% CI                 [+1.265c, +3.188c]

win rate               24.1%
TP rate                24.1%
SL rate                75.9%

ambiguity              ~1.3%

unique clusters        605

cluster bootstrap CI   [+1.719c, +3.777c]

This was a **gross historical PASS**.

It did not include full live fees/slippage/execution effects.

Do not tune the original +25/-5 rule using the locked
evaluation data.

---

# 5. Feature Data

Synchronized market feature snapshots are stored approximately
once per second.

Current feature state includes fields in categories such as:

## Contract state

- market ticker,
- timestamp,
- seconds remaining,
- YES bid/ask,
- NO bid/ask.

## BTC threshold state

- market threshold,
- BTC spot/reference,
- threshold distance in dollars,
- threshold distance percent/bps,
- threshold distance normalized by recent volatility.

## Momentum

- return 30 seconds,
- return 60 seconds,
- return 180 seconds,
- return 300 seconds.

## EMA state

- EMA spreads,
- EMA slopes,
- short/medium/long relationships.

## VWAP

- short-window VWAP distance,
- medium-window VWAP distance.

## Volatility / range

- realized volatility,
- rolling ranges.

## Flow

- relative volume,
- trade imbalance,
- order-book imbalance.

## Settlement context

Official Kalshi BRTI is also collected separately and may be
joined into future modeling where timestamp availability is
correctly enforced.

---

# 6. Strategy Families

## MAIN_TRIGGER

Original trigger family based on the primary historical
research rule and confirmation variants.

Several trigger confirmation profiles exist.

## MAIN_CONTEXT

Frozen child strategies created by subdividing main-trigger
behavior into specific price/time regions.

These are forward-only experiments.

## MICRO_MULTIPLIER

Very-low-price contract experiments involving multiplier-like
targets.

These have specialized research semantics and should not be
blindly generalized using ordinary strategy assumptions.

## GRID_V1

Broad predeclared strategy zoo.

Current structure:

9 contract-price bands
7 time-remaining bands
6 exit rules

Total:

378 strategies

Approximate price coverage:

5c–94c

Exit rules include combinations such as:

TP +5 / SL -5
TP +10 / SL -5
TP +15 / SL -5
TP +20 / SL -10
TP +25 / SL -10
settlement

All GRID_V1 strategies were declared before collecting their
prospective evidence.

## TAIL_V1

Extends the strategy population into very cheap and very
expensive contracts.

Approximately:

322 strategies

Low-price bands include approximately:

0.1–0.4c
0.5–0.9c
1.0–1.9c
2.0–2.9c
3.0–4.9c

High-price bands cover approximately:

95c–99.9c

TAIL_V1 includes:

- multiplier targets,
- settlement,
- small fixed TP/SL rules on expensive contracts.

Combined base zoo:

GRID_V1  378
TAIL_V1  322
----------------
          700

Additional main/micro/challenger strategies bring the live
registry above 700 total experiments.

---

# 7. Challenger Generator

The challenger generator searches existing evidence for
interesting subdivisions.

Its job is to create **new immutable strategies**, not mutate
parents.

A valid challenger:

parent evidence
      ↓
research detects interesting subset
      ↓
new strategy definition
      ↓
new strategy key/version
      ↓
new discovery cutoff
      ↓
new prospective start

The generator should eventually evolve beyond simple
price/time subdivisions and use feature-conditioned
hypotheses.

Potential future challenger conditions include:

- BTC threshold distance,
- short-term momentum,
- medium-term momentum,
- VWAP state,
- EMA state,
- realized volatility,
- volume,
- trade imbalance,
- book imbalance,
- BRTI state,
- regime classification.

Avoid blindly crossing every feature threshold with every
strategy.

That would create a severe multiple-testing problem.

---

# 8. PaperBroker Design

The PaperBroker is intended to approximate the future real
execution path.

Conceptually:

STRATEGY
   ↓
TRADE INTENT
   ↓
EXECUTION ENGINE
   ↓
PaperBroker / future KalshiBroker

The broker implementation should remain separate from strategy
logic.

## Current experimental bankroll

Each strategy receives approximately:

starting cash    $10
trade notional   $1

These are independent research accounts.

Example:

721 strategies
×
$10 virtual account

does **not** mean the system has or should deploy $7,210.

The combined experimental P&L is not a portfolio return.

---

# 9. Paper Entry Model

Current entries approximate an IOC marketable limit order.

The strategy records an entry limit near the signal ask.

Execution then observes available market data.

Possible outcomes include:

FILLED
PARTIAL FILL
NO_FILL
NO_CAPITAL

Observed live no-fills have overwhelmingly been:

PRICE_MOVED_ABOVE_IOC_LIMIT

Typical behavior:

signal at ask = 56c

~400 ms later

ask = 57c

strict 56c IOC
→ NO_FILL

Do not simply loosen entry limits because no-fill statistics
look high.

Execution-policy changes should themselves become prospective
experiments.

Potential future variants:

STRICT
original signal ask

+1 TICK
allow one adverse tick

+2 TICKS
allow two adverse ticks

PASSIVE
rest briefly at original signal price

Evaluate each based on:

- fill rate,
- slippage,
- net P&L,
- missed winners,
- avoided losers,
- fees.

---

# 10. Paper Exit Model

## Take profit

TP approximates a resting limit.

A TP is considered executable when the actual same-side bid
reaches/exceeds the target.

Queue position cannot currently be known.

## Stop

A stop triggers when the observed executable bid crosses the
stop condition.

The simulated exit occurs at the actual observed bid rather
than assuming the requested stop price.

This allows adverse gap/slippage.

## Settlement

Residual settlement positions use actual completed market
outcomes.

---

# 11. Fill / Liquidity Limitations

Top-of-book observations are currently available.

Full historical/live depth reconstruction is not yet complete.

As trade size increases, the following become more important:

- best-level available size,
- multiple price levels,
- partial fills,
- average execution price,
- queue position,
- adverse movement during execution.

At approximately $1 trade size, partial fills already occur in
some market episodes.

A future $10 position cannot be assumed to execute like ten
independent $1 positions.

Before serious scaling, reconstruct full order-book depth.

---

# 12. Fee Model

PaperBroker includes an estimated Kalshi fee model.

Fees can materially affect small-notional and mid-price
strategies.

The model is an estimate, not guaranteed exact production fee
accounting.

Evaluation should prefer:

NET performance

rather than gross payoff.

---

# 13. Historical Replay

Historical replay currently processes stored historical market
features chronologically.

Recent runs contained approximately:

5,891 historical markets
643,295 simulated strategy trades
608 strategies with usable replay evidence

A 70/30 chronological split was used for one screening view.

Historical replay discovered a small number of strategies that
remained positive in both early and late periods.

Many strong historical leads were settlement strategies.

Examples included regions such as:

65–74c / ~30 sec–2m / settle
85–94c / 10–12m / settle
75–84c / ~30 sec–2m / settle

These are research leads, not deployment instructions.

---

# 14. Historical Adaptive V1

The first adaptive simulator:

- maintained one $10 portfolio,
- observed completed hypothetical shadow results,
- selected strategies based largely on past ROI,
- used only already-completed results.

It obeyed chronological information rules.

Nevertheless it failed catastrophically.

The failure was statistical, not lookahead leakage.

Cheap settlement contracts have highly asymmetric return
distributions.

A few large historical winners can create enormous average ROI
while ordinary losses still destroy the bankroll.

This demonstrated that:

mean ROI

is a poor primary selector statistic for binary contracts.

---

# 15. Adaptive V2

Adaptive V2 changed the selection model.

For settlement strategies it estimates:

P(contract wins)

using Bayesian/Beta-Binomial style evidence.

It then compares conservative probability estimates against
the fee-adjusted breakeven probability implied by the entry
cost.

Conceptually:

posterior win probability
          ↓
conservative lower bound
          ↓
compare with break-even probability
          ↓
estimated conservative edge

V2 also uses recent evidence and independent-market
requirements.

---

# 16. Position Sizing Research

Adaptive V2 was tested under several risk levels.

At high risk, the portfolio could grow substantially but also
experienced extreme drawdown.

At approximately 1% bankroll risk per trade:

BAYES_FAST

starting cash    ~$10
ending cash      ~$12.18
return           ~+21.8%
max drawdown     ~23.4%
trades           ~361

BAYES_STRICT was safer but slightly negative:

ending cash      ~$9.62
return           ~-3.8%
max drawdown     ~10.2%

The FAST gains were heavily concentrated in the later
historical regime.

That strongly motivates regime-aware selection.

None of these retrospective results counts as prospective
proof.

---

# 17. Live Prospective Evidence

The PaperBroker continues collecting prospective evidence.

Recent snapshots contained:

700+ strategy accounts
1000+ strategy-level signals
hundreds of closed trades
hundreds of no-fills

However, many strategy trades come from the same underlying
market event.

For valid evidence always count:

strategy observations
unique markets
unique market/side episodes

A strategy with:

N = 10 trades

may still have only:

3 unique markets

and therefore remain extremely weak evidence.

---

# 18. Snapshot Export

The entire PaperBroker state is exported with:

./snapshot.sh

Output:

reports/paper_engine_snapshot.json

This snapshot should be preferred over manually copying
dashboard rows.

It includes:

- strategy registry,
- strategy accounts,
- trades,
- fills,
- no-fill diagnoses,
- score snapshots,
- feature context,
- market outcomes,
- scan cursors,
- paper dashboard state.

---

# 19. Health Semantics

System health is separated conceptually into:

## LIVE

Current execution-feed health.

Uses things such as:

- websocket state,
- event latency.

## RESEARCH

Rolling historical data quality.

Uses things such as:

- expected recent markets,
- candle completion,
- high-resolution quote coverage,
- pending markets,
- model age.

A historical quote gap must not falsely imply that the current
live execution feed is dead.

---

# 20. Historical Data Gaps

Some high-resolution historical quote gaps have been observed.

These gaps should remain visible in research health.

Do not lower health standards merely to make the dashboard
green.

Data-quality limitations should influence whether an
experiment is trustworthy.

---

# 21. Current ML Direction

The next major research layer should model probability rather
than only static strategy membership.

## Primary supervised problem

Predict:

P(YES settlement)

using only data observable at the prediction timestamp.

Example input:

contract ask
seconds remaining
threshold distance
BTC returns
EMA state
VWAP state
realized volatility
volume
trade imbalance
book imbalance
BRTI context

Output:

P(YES)

Then compare:

model probability
        vs
Kalshi implied probability
        vs
fees/execution cost

The interesting quantity becomes conservative estimated net
edge.

---

# 22. ML Baselines

Start simple.

## Model 1

Logistic regression.

Why:

- interpretable,
- difficult to overfit compared with deep models,
- gives a useful probability baseline,
- easy to inspect calibration.

## Model 2

Gradient-boosted decision trees.

Why:

- handles nonlinear relationships,
- handles interactions,
- strong baseline for tabular data,
- generally more appropriate than a neural network for this
  feature structure.

Do not assume the nonlinear model is better.

If it cannot reliably outperform logistic regression
out-of-sample, prefer the simpler model.

---

# 23. ML Validation

Do not use random train/test splitting for claims about trading
performance.

Use chronological evaluation.

Possible structure:

TRAIN
oldest block

VALIDATE
next block

TEST
future untouched block

ROLL FORWARD
repeat

The training pipeline must ensure that settlement labels do not
become accessible to the model until the relevant historical
market has completed.

---

# 24. ML Metrics

Classification accuracy is not the primary metric.

Evaluate:

- Brier score,
- log loss,
- calibration curves,
- expected calibration error,
- probability bias,
- market-relative probability edge,
- fee-adjusted expected value,
- stability across time blocks,
- stability across price ranges,
- stability across time-remaining ranges.

A model that predicts probability well may still not generate
an executable trading edge.

---

# 25. Regime Modeling

The current adaptive evidence appears strongly
regime-dependent.

Potential regime approaches include:

- rolling statistics,
- exponentially decayed evidence,
- change-point detection,
- volatility regimes,
- momentum regimes,
- market calibration regimes,
- hidden-state models,
- clustering.

A strategy's lifetime evidence should not necessarily carry
equal weight forever.

Old observations can become stale.

---

# 26. Contextual Selection

The next generation selector should not simply choose whichever
strategy currently has the highest historical score.

Possible formulation:

context:
current BTC/Kalshi state

arms:
eligible strategies

reward:
realized net return

This resembles a contextual bandit.

Because the research system often paper trades many strategies
simultaneously, it may observe outcomes for many candidate
actions.

This makes expert-weighting/full-information online-learning
methods particularly interesting.

Potential methods:

- Hedge,
- exponential weights,
- Bayesian model averaging,
- contextual bandits,
- online logistic models.

---

# 27. Reinforcement Learning

RL is conceptually relevant.

Possible MDP mapping:

STATE
market features
account state
open positions
execution state

ACTION
PASS
BUY YES
BUY NO
select strategy
select size

TRANSITION
market evolves

REWARD
net realized P&L

However, RL should not be an early implementation priority.

An RL agent can learn to exploit simulator inaccuracies.

Current execution simulation still lacks:

- true historical full depth,
- real historical queueing,
- exact historical IOC latency.

Therefore deep RL/Q-learning should remain a later research
experiment.

---

# 28. Execution Prediction

A separate future ML model should estimate execution quality.

Prospective PaperBroker data can provide examples containing:

signal price
spread
quote age
recent price velocity
volatility
displayed depth
book imbalance
time remaining
order size
entry policy

Targets could include:

P(fill)
fill percentage
entry slippage
time to fill

Eventually expected trade value should consider:

model edge
×
fill probability
-
fees
-
slippage
-
execution risk

This becomes especially important for $5/$10+ order sizes.

---

# 29. Research Agent

A future repository-level AI agent may coordinate the research
loop.

Suggested responsibilities:

inspect new data
       ↓
run snapshot analysis
       ↓
detect interesting changes
       ↓
propose experiment
       ↓
generate frozen challenger
       ↓
run historical replay
       ↓
run walk-forward test
       ↓
run tests
       ↓
create research report
       ↓
start prospective paper clock

The agent may help:

- manage experiments,
- generate reports,
- train models,
- propose challenger strategies,
- detect degradation,
- maintain documentation,
- run tests.

It should not have unilateral authority to:

- weaken risk controls,
- rewrite promotion rules,
- delete unfavorable evidence,
- access real-money write credentials,
- activate live execution.

The deterministic research pipeline should remain authoritative.

---

# 30. Future Live Execution Architecture

Real execution is not currently enabled.

When eventually developed, use distinct modes such as:

SHADOW
PAPER
DEMO
LIVE

Future safeguards should include:

- separate write credentials,
- explicit live switch,
- stale-data block,
- maximum position size,
- maximum loss,
- maximum concurrent exposure,
- idempotent client order IDs,
- account reconciliation,
- partial-fill handling,
- kill switch,
- execution logging,
- restart recovery.

The live broker should be a separate write-capable component,
not a hidden extension of the read-only API client.

---

# 31. Full Depth Requirement

Before serious scaling or real capital, build full order-book
reconstruction from authenticated websocket data.

Requirements should include:

initial snapshot
        ↓
ordered deltas
        ↓
sequence validation
        ↓
rebuild after reconnect/gap
        ↓
normalized full book

Entries should then be able to walk asks through a specified
limit.

Stops should be able to walk bids.

TP queue position will still remain uncertain and should be
handled conservatively.

---

# 32. Trade Size Scaling

The current paper research notional is approximately:

$1

Future testing may include:

$1
$5
$10

Do not simply multiply historical P&L by ten.

Larger sizes may change:

- fill probability,
- partial-fill rate,
- average execution price,
- slippage,
- market impact.

Parallel execution-size simulations are preferable.

Possible future research:

same signal
    ↓
$1 simulation
$5 simulation
$10 simulation
    ↓
compare:
fill %
partial fill %
slippage
fees
net EV

---

# 33. Current Recommended Development Sequence

The current preferred research direction is:

1. Keep live PaperBroker collecting forward evidence.

2. Build ML Dataset V1.

3. Train chronological logistic-regression probability
   baseline.

4. Train chronological gradient-boosted-tree baseline.

5. Measure probability calibration and fee-adjusted edge.

6. Build regime-aware Adaptive V3.

7. Explore expert weighting/contextual selection.

8. Continue collecting prospective fill/slippage evidence.

9. Build execution-quality model.

10. Build full-depth order-book reconstruction.

11. Introduce parallel position-size simulations.

12. Consider RL only after simulator fidelity improves.

13. Eventually build the research-agent orchestration layer.

14. Real-money execution remains a separate later project.

---

# 34. Development Workflow

Before modifying important code:

inspect implementation
inspect schema
inspect tests
understand dependencies

After meaningful changes:

PYTHONPATH=src .venv/bin/python \
  -m compileall -q src tests

PYTHONPATH=src .venv/bin/python \
  -m pytest -q

bash -n start.sh

if [ -f snapshot.sh ]; then
    bash -n snapshot.sh
fi

git diff --check

Before committing:

git status --short
git diff

Do not commit unrelated files merely because they are modified.

---

# 35. Git / Repository

Repository:

aceortiz21/kalshi-stats

A development branch named:

tonight-stabilize

has previously been used.

Do not assume the current branch.

Always check:

git branch --show-current
git status --short

---

# 36. Data Integrity

Do not delete or rewrite historical/live outcomes simply to
produce cleaner statistics.

When starting a truly new experiment, create:

- a new table,
- a new strategy version,
- a new experiment ID,
- or a new forward start.

Preserve old evidence.

Negative results are valuable research results.

---

# 37. Interpretation Standard

Do not state:

"We found a profitable bot."

because a historical backtest is positive.

Prefer statements such as:

"This strategy is a historical lead."

"This strategy has positive retrospective walk-forward
evidence."

"This strategy currently has N prospective markets."

"This execution policy has not yet demonstrated a live net
edge."

"Our methods have not demonstrated an edge under the tested
execution assumptions."

Evidence strength matters more than headline P&L.

---

# 38. Immediate Codex Handoff Goal

The immediate next phase should begin with repository
inspection.

Before implementing ML, Codex should verify:

- current schema,
- historical feature table structure,
- feature names,
- historical outcome labels,
- timestamps and units,
- replay mechanics,
- PaperBroker semantics,
- current tests,
- existing dependencies,
- any mismatch between code and this documentation.

Only after inspection should the ML dataset/model architecture
be finalized.

Do not code based solely on assumptions in this document.

The actual implementation and database schema are the source
of truth.
