# Kalshi BTC 15-Min Statistics Manager — User Guide

## 1. What This Tool Does

This project analyzes historical Kalshi KXBTC15M markets and compares the current market state with similar historical states.

The goal is to answer questions such as:

- When a YES or NO contract traded around 20–29¢ with 8 minutes remaining, what happened afterward historically?
- How often did that contract subsequently rise another 5¢, 10¢, 15¢, or 20¢?
- How often did it eventually settle as the winner?
- How high did the contract usually get afterward?
- How far did it usually move against the position first?
- How did similar setups behave at different points in the 15-minute market?

The dashboard is a historical research tool.

It does NOT prove that a setup is profitable, predict the next market with certainty, or account for every real-world execution factor such as spreads, slippage, fees, latency, and available liquidity.

---

# 2. Kalshi Price Basics

A Kalshi binary contract has two sides:

- YES
- NO

Prices range from approximately 0¢ to 100¢.

A YES contract trading at 25¢ can be interpreted as the market pricing YES at roughly 25%, although the actual market mechanics, spread, and fees mean price should not be treated as a perfect probability estimate.

If YES settles as correct, a YES contract pays $1.

If NO settles as correct, a NO contract pays $1.

YES and NO are complementary sides of the same binary outcome.

This dashboard analyzes the historical behavior of whichever side is being studied.

---

# 3. Data Coverage / Health

Always check the coverage numbers before interpreting the statistics.

## Total Markets

Number of KXBTC15M markets stored in the database.

## Settled Markets

Markets whose final outcome is known.

These markets can contribute to settlement statistics such as Win Rate.

## Settled With Trade History

Settled markets for which detailed Kalshi trade history is available.

Trade data can provide more precise timing than 1-minute candles.

## Settled With Candle History

Settled markets with Kalshi 1-minute candle history.

This is currently the primary large historical dataset used for many path statistics.

## BTC-Covered Markets

Markets for which corresponding BTC market data has been collected.

This becomes particularly important as BTC indicators and contextual features are added.

## Kalshi Trades

Total individual Kalshi trade observations stored.

## BTC 1s Rows

Total one-second BTC observations stored.

## Last Live Sync

Timestamp of the most recent live synchronization.

If this timestamp is stale, the Current Market section may not represent the latest market.

---

# 4. The Most Important Sample-Size Concepts

Three different sample-size measurements appear in the dashboard.

Understanding the difference is critical.

## N

N is the number of qualifying historical observations or scenario occurrences.

For settlement statistics such as Win Rate, N tells you how many historical observations contributed to the outcome analysis.

Large N generally gives more stable estimates than tiny N.

However, N alone does NOT mean every observation contains enough future price data for path analysis.

## Path N

Path N is the number of observations that actually have valid subsequent price-path data.

Path statistics include metrics such as:

- subsequent maximum price
- subsequent minimum price
- +5¢ reach
- +10¢ reach
- +15¢ reach
- +20¢ reach
- MFE
- MAE

Example:

N = 6,000  
Path N = 6,000

This is strong path coverage.

But:

N = 6,000  
Path N = 1

means settlement statistics may still use thousands of observations, while subsequent-price statistics effectively have almost no usable sample.

Always check Path N before trusting path-based metrics.

## Unique Markets

Number of distinct KXBTC15M markets represented.

One market can sometimes produce multiple qualifying observations depending on the analysis.

Unique Markets helps reveal how broadly distributed a pattern is.

A statistic supported by thousands of independent markets is generally more interesting than one generated repeatedly from only a few markets.

---

# 5. Current Market

The Current Market section is designed to answer:

"What is happening right now, and what happened historically in comparable states?"

For each active side, the dashboard can show:

## Current Price

The latest stored Kalshi price for that side.

Example:

23¢

## Time Remaining

How much time remains before the 15-minute market closes.

Example:

08:42

## Historical State

The price bucket and time bucket corresponding to the current market.

Example:

20–29¢ · 5–10m left

The dashboard then looks at historical observations belonging to that same general state.

## Historical N

Number of comparable historical observations.

## Eventual Win

Percentage of comparable historical observations that eventually settled as the winning side.

This is NOT the same as the probability of making money from buying at the current price.

## Subsequent Price Reach

Shows how frequently comparable historical states subsequently moved:

- +5¢
- +10¢
- +15¢
- +20¢

above the historical entry state.

Example:

Current price: 23¢  
Historical +10¢ rate: 64%

Interpretation:

Historically, qualifying observations around this state subsequently reached approximately 33¢ or higher 64% of the time.

This does NOT mean there is a 64% chance that the current contract will do so.

## Median Subsequent Max

Median highest subsequent contract price among comparable observations.

The median is often more useful than the average because extreme moves have less influence on it.

## Average Subsequent Max

Mean highest subsequent contract price.

Large moves toward 100¢ can pull this number upward.

## Matching Scenarios

Named historical scenarios that the current market state satisfies.

---

# 6. Live Scenario Board

The Live Scenario Board only displays active markets satisfying one of the configured named scenarios.

This is more specific than the generic Current Market comparison.

If nothing appears here, it simply means the current market does not satisfy a configured scenario trigger.

It does NOT mean there is no interesting market state.

---

# 7. Historical Scenario Statistics

This section analyzes specifically defined historical situations.

Each scenario has a trigger definition.

When that trigger occurred historically, the tool studies what happened afterward.

---

# 8. Named Scenarios

## Early 20s Rebound

A side trades in the 20-cent range during the first five minutes of the market.

The tool follows that same side afterward.

Purpose:

Study whether an early underdog priced in the 20s tends to rebound later during the contract.

---

## Teens Comeback

A side trades in the teens.

The tool follows that same side afterward.

Purpose:

Measure how often a contract priced roughly 10–19¢ later recovers, how large those recoveries are, and how often the side ultimately wins.

---

## Single-Digit Resurrection

A side trades below 10¢.

The tool follows that side.

Purpose:

Study very low-priced contracts and determine how often they experience meaningful rebounds.

These setups can show extremely large percentage MFE values because moving from something like 5¢ to 15¢ is a 200% increase even though the absolute move is only 10¢.

For low-priced contracts, absolute cents are generally easier to interpret than percentage MFE.

---

## 30s Recovery

A side trades in the 30-cent range.

The tool studies the subsequent price path.

Purpose:

Measure how often a contract in the 30s recovers into higher price levels.

---

## Full Flip From 10s

A side trades at 10¢ or lower.

The tool tracks whether the low-priced side later experiences a major reversal.

Purpose:

Study dramatic reversals from extreme underdog territory.

---

## Favorite Fade From 80s

One side trades between approximately 80¢ and 90¢ early in the market.

The tool studies the OPPOSITE side.

Example:

YES = 82¢  
NO ≈ 18¢

The scenario studies the cheaper NO side.

Purpose:

Measure how often a strong early favorite subsequently weakens enough for the underdog side to appreciate.

---

## Favorite Fade From 70s

One side trades in the 70-cent range.

The tool studies the opposite side.

Purpose:

Measure historical reversals when the market has established a meaningful but less extreme favorite.

---

## Favorite Fade From 90s

One side trades at 90¢ or higher.

The tool studies the opposite side.

Purpose:

Measure how frequently apparently overwhelming favorites experience significant reversals.

This is closely related mathematically to studying extremely low-priced underdogs.

---

## Full Flip From 20s

A side trades at 20¢ or lower.

The tool follows that same side.

Purpose:

Study whether substantial underdogs later make large reversals.

---

## Full Flip From 30s

A side trades at 30¢ or lower.

The tool follows that side and studies whether it later clears important higher price levels.

Purpose:

Measure larger reversals from moderate underdog territory.

---

## Late Underdog Comeback

A side trades at 20¢ or lower with approximately three minutes or less remaining.

Purpose:

Study low-priced contracts late in the market when little time remains for BTC movement to change the result.

Because timing is short, these statistics should be interpreted differently from an identical price early in the contract.

---

## Ultra-Late Single Digits

A side trades at 10¢ or lower with less than one minute remaining.

Purpose:

Study extreme late-market underdogs.

IMPORTANT:

The current 1-minute candle dataset provides very little subsequent path information after an observation occurring in the final minute.

Therefore this scenario may have a large N but extremely small Path N.

Its settlement Win Rate can still be informative.

Its path statistics should NOT be trusted when Path N is tiny.

---

# 9. Occurrence Mode

Scenario definitions may specify how repeated triggers are counted.

## first_per_market

Only the first qualifying occurrence in a market is used.

This helps prevent one market from dominating the dataset by repeatedly triggering the same scenario.

## Cooldown

Some scenario configurations include a cooldown such as:

60s

This controls how quickly another qualifying event may be considered when re-entry counting is enabled.

---

# 10. Reliability

Reliability is a dashboard warning/status indicator based on whether enough observations exist to make the summary reasonably interpretable.

"OK" does NOT mean:

- profitable
- predictive
- safe
- statistically proven

It primarily means the sample is not obviously too small under the current dashboard rules.

---

# 11. Win Rate

Win Rate is the percentage of qualifying historical observations whose studied side eventually settled as the winner.

Example:

Entry state around 20¢  
Win Rate = 30%

means roughly 30% of those historical occurrences eventually settled at $1.

Win Rate and rebound probability are very different.

A contract can rise from 20¢ to 40¢ and still ultimately lose.

For trading-style analysis, subsequent price movement may therefore be more relevant than settlement alone.

---

# 12. 95% Confidence Interval

The 95% CI gives a statistical uncertainty range around the observed Win Rate.

Example:

Win Rate = 30.1%  
95% CI = 28.8% to 31.4%

This does NOT guarantee that the true future probability lies in that interval.

It describes sampling uncertainty under the assumptions of the calculation.

It also does not account for market regime changes or every form of dependence between observations.

---

# 13. Trigger Price

## Avg Trigger

Average contract price when the scenario triggered.

## Median Trigger

Middle trigger price across all occurrences.

Median is less sensitive to extreme observations.

---

# 14. Subsequent Maximum Price

## Avg Max Price

Average highest price reached after the trigger.

## Median Max Price

Median highest subsequent price.

Example:

Trigger ≈ 27¢  
Median Max = 57¢

means half of qualifying path observations had a subsequent maximum at or above roughly 57¢ and half below it.

It does NOT mean the typical trade would automatically realize that price.

Actually exiting at the maximum is unrealistic without perfect foresight.

---

# 15. Subsequent Minimum Price

## Avg Min Price

Average lowest subsequent price after the trigger.

This helps show how far the contract commonly moved against the studied side.

---

# 16. MFE — Maximum Favorable Excursion

MFE measures the largest favorable price move after the trigger.

Example:

Entry = 25¢  
Highest later price = 45¢

MFE = +20¢

## Avg MFE

Average maximum favorable excursion.

## Median MFE

Median maximum favorable excursion.

These are useful for thinking about historical take-profit distances.

They do NOT tell you what take-profit level will be optimal in the future.

---

# 17. MAE — Maximum Adverse Excursion

MAE measures the largest unfavorable move after the trigger.

Example:

Entry = 25¢  
Lowest later price = 15¢

MAE = 10¢

## Avg MAE

Average maximum adverse excursion.

## Median MAE

Median maximum adverse excursion.

MAE can help show how much adverse movement historically occurred before or during a setup's subsequent path.

It should NOT automatically be converted into a stop-loss rule without separate testing.

---

# 18. MFE % and MAE %

These express favorable/adverse movement relative to the trigger price.

For low-priced contracts these percentages can become enormous.

Example:

Entry = 5¢  
Price rises to 15¢

Absolute MFE = 10¢

Percentage MFE = 200%

The 200% number sounds dramatic, but the actual Kalshi move was 10¢.

For very cheap contracts, prioritize absolute cents over percentage excursion metrics.

---

# 19. Median Time to Max

Median amount of time between the trigger and the subsequent maximum.

Example:

Median Time to Max = 180s

means the median maximum occurred approximately three minutes after the trigger.

This is descriptive, not a prediction that the next setup will peak after exactly three minutes.

---

# 20. Median Time to Min

Median amount of time until the subsequent minimum.

This can help describe whether adverse movement tends to occur quickly or later.

---

# 21. Average Price After

This reports average contract prices at fixed time offsets after the trigger.

Examples:

30s  
60s  
120s  
180s  
300s

These values answer:

"Across historical occurrences, what was the average contract price approximately X seconds after the trigger?"

For 1-minute candle observations, precision is limited by candle resolution.

---

# 22. Target Touch Summary

This section uses ABSOLUTE contract-price targets.

Example:

30c 88.1% (4361/4952), med 60s

means:

- Target price = 30¢
- 4,361 of 4,952 qualifying occurrences reached 30¢ afterward
- Historical touch rate = 88.1%
- Median time among successful touches = 60 seconds

IMPORTANT:

These are absolute targets.

They are different from relative targets such as +10¢.

If the entry price is already above an absolute target, that target may not be meaningful for the scenario.

---

# 23. Relative Reach Metrics

The Price × Time Matrix and Setup Finder use relative movement:

- +5¢
- +10¢
- +15¢
- +20¢

Example:

Price bucket = 20–29¢  
Reach +10¢ = 67%

means qualifying observations subsequently appreciated by at least approximately 10 cents from their entry state 67% of the time.

Relative reach is particularly useful when comparing different price buckets.

---

# 24. Price Ceiling

Kalshi contracts cannot rise above $1.00 / 100¢.

Therefore some relative targets are impossible.

Example:

Starting price = 85¢

+5¢ is possible.  
+10¢ is possible up to 95¢.  
+15¢ may reach approximately 100¢ depending on exact entry.  
+20¢ is impossible.

A 0% value can therefore represent a mechanically impossible target rather than evidence of poor historical behavior.

Always consider the 100¢ ceiling.

---

# 25. Time-Remaining Breakdown

This divides scenario occurrences according to how much time remained when they occurred.

Typical buckets include:

- 10m+
- 5–10m
- 3–5m
- 2–3m
- 1–2m
- <1m

This is extremely important.

A 15¢ contract with 12 minutes remaining is fundamentally different from a 15¢ contract with 30 seconds remaining.

The breakdown shows:

- N
- Win Rate
- Median Max

for each timing group.

---

# 26. Overlap Markets

Scenarios can describe related states.

For example:

- Single-Digit Resurrection
- Full Flip From 10s
- Favorite Fade From 90s

can frequently describe mathematically related market situations.

Overlap Markets shows how many markets are shared between scenario definitions.

Large overlap means two scenarios should NOT be treated as independent pieces of evidence.

---

# 27. Historical Setup Finder

The Setup Finder automatically searches the Price × Time Matrix for high-sample historical states.

Current candidate requirements include:

Path N >= 500

Candidates are currently ranked primarily by:

1. +10¢ historical reach rate
2. +15¢ reach rate
3. sample size

The Setup Finder is intentionally simple and transparent.

It is NOT yet a validated predictive model.

---

# 28. How to Read the Setup Finder

Suppose a row says:

Price: 20–29¢  
Time Left: 10–15m  
Path N: 2,986  
Win Rate: 24%  
+5¢: 79%  
+10¢: 68%  
+15¢: 58%  
+20¢: 51%

A reasonable interpretation is:

"When a side historically appeared in this general price/time state, a substantial fraction of observations experienced meaningful upward movement before settlement."

An incorrect interpretation would be:

"If I buy every 20–29¢ contract with 10–15 minutes left, I have a guaranteed profitable strategy."

The historical statistics alone do not establish that.

---

# 29. Price × Time Matrix

This is the most general historical reference table.

Instead of relying on named scenarios, it groups observations according to:

1. contract price
2. time remaining

Example price buckets:

- 0–9¢
- 10–19¢
- 20–29¢
- 30–39¢
- etc.

Example time buckets:

- 10–15m
- 5–10m
- 3–5m
- 2–3m
- 1–2m
- <1m

For each combination the matrix reports:

- N
- Path N
- Unique Markets
- Win Rate
- Avg Max Price
- Median Max Price
- Reach +5¢
- Reach +10¢
- Reach +15¢
- Reach +20¢
- selected absolute target touch rates

This allows the current market to be mapped to a broad historical state even when no named scenario applies.

---

# 30. Worked Example

Imagine the live market shows:

YES = 23¢  
Time remaining = 8:42

This maps approximately to:

Price bucket = 20–29¢  
Time bucket = 5–10m

Suppose the historical matrix reports:

Path N = 3,000+  
Win Rate = 23%  
+5¢ = 75%  
+10¢ = 63%  
+15¢ = 55%  
+20¢ = 48%  
Median Max = 42¢

You might read this as:

"Historically, comparable states often experienced some rebound even though the side ultimately won much less frequently."

That distinction matters.

The historical probability of reaching 33¢ from a 23¢ entry can be much higher than the probability of ultimately settling at $1.

This is why Win Rate alone does not describe the entire historical opportunity.

---

# 31. Why Average Max Is Not an Expected Exit Price

Suppose:

Average Max = 60¢

That does NOT mean buying at 25¢ historically produced an average 35¢ profit.

Maximum price is calculated with hindsight.

A trader would not know in real time which observed price will become the maximum.

Max-price statistics are useful for describing historical opportunity, not realized strategy returns.

---

# 32. One-Minute Candle Limitation

Much of the large historical dataset uses 1-minute Kalshi candles.

A candle contains information such as:

- open
- high
- low
- close

but does NOT reveal the exact sequence of events inside that minute.

Example:

Open = 28¢  
High = 35¢  
Low = 15¢  
Close = 18¢

The candle does not tell us whether price moved:

28 -> 35 -> 15 -> 18

or:

28 -> 15 -> 35 -> 18

This creates intrabar ambiguity.

The analytics therefore use conservative handling designed to avoid counting price movement from earlier in the trigger candle as movement that occurred after the trigger.

For candle-based triggers, subsequent path analysis begins with the next candle.

This reduces look-ahead bias but also sacrifices some information.

---

# 33. Final-Minute Path Limitation

The 1-minute candle design becomes especially important during the final minute.

If a scenario triggers during the last candle, there may be no later candle before settlement.

Therefore:

N may be large

while:

Path N may be zero or extremely small.

This is expected.

Do not use final-minute path metrics without checking Path N.

---

# 34. Trade Data vs Candle Data

Where detailed individual trade data exists, timing can be more precise.

Trade observations may legitimately show a target being reached very quickly or even within the same timestamp resolution.

Candle observations are treated differently because intrabar event ordering is unknown.

The system intentionally avoids applying one identical timing rule to both data sources.

---

# 35. Historical Frequency Is Not Automatically an Edge

Suppose the dashboard says:

+10¢ reached 80% historically.

That sounds attractive, but profitability still depends on factors such as:

- exact entry price
- exact exit price
- bid/ask spread
- Kalshi fees
- liquidity
- slippage
- whether the target could actually be filled
- losses when the target is not reached
- stop-loss behavior
- market regime changes
- selection bias
- repeated observations from related market states

A high touch rate alone is not sufficient evidence of positive expected value.

---

# 36. Avoiding Data Mining

If we search enough historical combinations, some will look excellent purely by chance.

Therefore candidate discovery and candidate validation should be separated.

The intended workflow is:

1. Discovery dataset:
   Find potentially interesting historical patterns.

2. Holdout dataset:
   Test those patterns on markets that were NOT used to choose them.

3. Compare:
   Determine whether the pattern persists.

A setup that looks excellent during discovery but collapses during holdout validation is likely not robust.

---

# 37. Current Setup Finder Status

The Setup Finder currently performs DISCOVERY.

Its rankings should be interpreted as:

"These historical states deserve further investigation."

NOT:

"These are the best trades."

Holdout validation is the next major statistical step.

---

# 38. Using the Dashboard During a Live Market

A practical workflow is:

1. Check the active YES and NO prices.
2. Check time remaining.
3. Find the corresponding historical price/time state.
4. Check N and Path N.
5. Look at +5¢, +10¢, +15¢, and +20¢ reach rates.
6. Check Win Rate separately.
7. Check Median Subsequent Max.
8. Check whether a named scenario also matches.
9. Use external market context and indicators separately.
10. Make your own decision.

The dashboard provides historical context.

It should not be the only input to a decision.

---

# 39. Interpreting Indicators

The current historical dashboard primarily analyzes Kalshi market behavior.

External BTC indicators such as:

- VWAP
- EMA
- momentum
- BTC distance from the Kalshi strike
- BTC price velocity

are not yet fully integrated into the large historical scenario engine.

Until they are, use those indicators as separate contextual information rather than assuming the historical tables already account for them.

---

# 40. Example of Combining Context

Suppose:

YES = 23¢  
Time remaining = 8:42  
Historical +10¢ reach = 63%  
Historical Win Rate = 23%

Separately, you observe:

BTC above VWAP  
short-term EMA momentum turning upward  
BTC close to the Kalshi strike

The historical dashboard tells you what similar Kalshi price/time states did.

The BTC indicators tell you something about the CURRENT underlying market.

Those are two different sources of information.

Future versions of the project can test whether combining them historically improves discrimination.

---

# 41. What We Eventually Want to Learn

The long-term research question is not simply:

"Did cheap contracts rebound?"

It is closer to:

"Under which combinations of contract price, time remaining, BTC position relative to strike, momentum, VWAP/EMA state, and recent Kalshi price behavior did historical contracts behave differently from the broad baseline?"

That requires progressively richer data and careful validation.

---

# 42. Quick Reference

## N
Number of qualifying observations.

## Path N
Number with usable subsequent price paths.

## Unique Markets
Number of distinct markets represented.

## Win Rate
Percentage eventually settling as the winning side.

## 95% CI
Sampling uncertainty interval around Win Rate.

## Avg Trigger
Average scenario entry/trigger price.

## Median Trigger
Median scenario entry/trigger price.

## Avg Max
Average highest subsequent price.

## Median Max
Median highest subsequent price.

## Avg Min
Average lowest subsequent price.

## MFE
Maximum Favorable Excursion.

## MAE
Maximum Adverse Excursion.

## MFE %
Favorable excursion relative to trigger price.

## MAE %
Adverse excursion relative to trigger price.

## Median Time to Max
Median time until highest subsequent price.

## Median Time to Min
Median time until lowest subsequent price.

## Avg Price After
Average price at fixed intervals after trigger.

## Absolute Target
A fixed contract price such as 30¢ or 50¢.

## Relative Target
A movement relative to entry such as +10¢.

## Time Breakdown
Scenario statistics separated by time remaining.

## Overlap Markets
Markets shared with other scenario definitions.

## Setup Finder
Automatic high-sample candidate discovery.

## Price × Time Matrix
Generic historical behavior grouped by contract price and time remaining.

---

# 43. Most Important Rules

If you remember only a few things:

1. ALWAYS check Path N before trusting path statistics.

2. Win Rate and rebound probability are NOT the same thing.

3. +10¢ means a relative move; "Touch 30¢" means an absolute price.

4. Average/Median Max describe historical opportunity, not a realizable automatic exit.

5. Low-price percentage MFE can look enormous; absolute cents are usually easier to interpret.

6. Price and time remaining should be considered together.

7. A high historical percentage is not automatically a profitable trading edge.

8. Setup Finder results are discovery candidates until they survive holdout validation.

9. Final-minute candle path statistics have limited coverage.

10. Historical statistics are context for a decision, not a guaranteed prediction.

---

# 44. Development Roadmap

Current major completed pieces:

- Historical KXBTC15M market database
- Large recent 1-minute candle backfill
- Historical named scenarios
- Price × Time Matrix
- Path N tracking
- Current Market UI
- Historical Setup Finder
- Conservative candle-path handling

Next major steps:

1. Holdout validation
2. Improve Setup Finder ranking
3. Add dashboard-integrated help/documentation
4. Improve scenario presentation
5. Verify continuous live synchronization
6. Add richer BTC context
7. Investigate VWAP / EMA / momentum features
8. Test whether richer features improve out-of-sample discrimination

---

# 45. Final Interpretation

Think of the dashboard as a historical lookup and research system.

It answers:

"When the market looked approximately like this before, what happened afterward?"

It does not answer with certainty:

"What will happen next?"

The usefulness of the project comes from combining:

- large historical samples
- correct time-aware analysis
- transparent statistics
- careful sample-size interpretation
- live market context
- eventually, out-of-sample validation

As the project evolves, the priority should remain statistical correctness and interpretability rather than simply finding the highest-looking percentage.
