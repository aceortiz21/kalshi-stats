import unittest

from kalshi_stats.models import ExitStrategy, Observation
from kalshi_stats.strategies import simulate_exit_strategy


def candle(ts, close, low, high):
    return Observation(
        observed_ts=ts,
        seconds_remaining=max(0, 900 - ts),
        elapsed_seconds=ts,
        yes_close=close,
        yes_low=low,
        yes_high=high,
        source="candle",
    )


class ExitStrategyTests(unittest.TestCase):

    def test_take_profit_hits(self):
        strategy = ExitStrategy(
            id="tp10",
            name="TP +10c",
            take_profit_cents=10,
        )

        result = simulate_exit_strategy(
            strategy=strategy,
            market_ticker="TEST",
            traded_side="yes",
            entry_ts=0,
            entry_price=0.20,
            eventual_win=False,
            future=[candle(60, 0.27, 0.18, 0.31)],
        )

        self.assertEqual(result.exit_reason, "TAKE_PROFIT")
        self.assertAlmostEqual(result.exit_price, 0.30)
        self.assertAlmostEqual(result.profit, 0.10)

    def test_stop_loss_hits(self):
        strategy = ExitStrategy(
            id="tp10_sl5",
            name="TP +10 / SL -5",
            take_profit_cents=10,
            stop_loss_cents=5,
        )

        result = simulate_exit_strategy(
            strategy=strategy,
            market_ticker="TEST",
            traded_side="yes",
            entry_ts=0,
            entry_price=0.20,
            eventual_win=True,
            future=[candle(60, 0.16, 0.14, 0.25)],
        )

        self.assertEqual(result.exit_reason, "STOP_LOSS")
        self.assertAlmostEqual(result.exit_price, 0.15)
        self.assertAlmostEqual(result.profit, -0.05)

    def test_same_candle_conservative_uses_stop(self):
        strategy = ExitStrategy(
            id="tp10_sl5",
            name="TP +10 / SL -5",
            take_profit_cents=10,
            stop_loss_cents=5,
        )

        result = simulate_exit_strategy(
            strategy=strategy,
            market_ticker="TEST",
            traded_side="yes",
            entry_ts=0,
            entry_price=0.20,
            eventual_win=True,
            future=[candle(60, 0.22, 0.14, 0.31)],
            ambiguity_mode="conservative",
        )

        self.assertTrue(result.ambiguous)
        self.assertEqual(result.exit_reason, "STOP_LOSS")
        self.assertAlmostEqual(result.profit, -0.05)

    def test_same_candle_optimistic_uses_take_profit(self):
        strategy = ExitStrategy(
            id="tp10_sl5",
            name="TP +10 / SL -5",
            take_profit_cents=10,
            stop_loss_cents=5,
        )

        result = simulate_exit_strategy(
            strategy=strategy,
            market_ticker="TEST",
            traded_side="yes",
            entry_ts=0,
            entry_price=0.20,
            eventual_win=True,
            future=[candle(60, 0.22, 0.14, 0.31)],
            ambiguity_mode="optimistic",
        )

        self.assertTrue(result.ambiguous)
        self.assertEqual(result.exit_reason, "TAKE_PROFIT")
        self.assertAlmostEqual(result.profit, 0.10)

    def test_same_candle_exclude_marks_ambiguous(self):
        strategy = ExitStrategy(
            id="tp10_sl5",
            name="TP +10 / SL -5",
            take_profit_cents=10,
            stop_loss_cents=5,
        )

        result = simulate_exit_strategy(
            strategy=strategy,
            market_ticker="TEST",
            traded_side="yes",
            entry_ts=0,
            entry_price=0.20,
            eventual_win=True,
            future=[candle(60, 0.22, 0.14, 0.31)],
            ambiguity_mode="exclude",
        )

        self.assertEqual(result.exit_reason, "AMBIGUOUS")
        self.assertTrue(result.ambiguous)

    def test_no_side_price_conversion(self):
        strategy = ExitStrategy(
            id="tp10",
            name="TP +10",
            take_profit_cents=10,
        )

        # YES falls to 0.60, meaning NO rises to 0.40.
        result = simulate_exit_strategy(
            strategy=strategy,
            market_ticker="TEST",
            traded_side="no",
            entry_ts=0,
            entry_price=0.30,
            eventual_win=False,
            future=[candle(60, 0.62, 0.60, 0.72)],
        )

        self.assertEqual(result.exit_reason, "TAKE_PROFIT")
        self.assertAlmostEqual(result.exit_price, 0.40)

    def test_time_exit_uses_close(self):
        strategy = ExitStrategy(
            id="exit60",
            name="Exit 60s",
            time_exit_seconds=60,
            hold_to_settlement=False,
        )

        result = simulate_exit_strategy(
            strategy=strategy,
            market_ticker="TEST",
            traded_side="yes",
            entry_ts=0,
            entry_price=0.20,
            eventual_win=False,
            future=[candle(60, 0.24, 0.18, 0.26)],
        )

        self.assertEqual(result.exit_reason, "TIME_EXIT")
        self.assertAlmostEqual(result.exit_price, 0.24)
        self.assertAlmostEqual(result.profit, 0.04)

    def test_hold_to_settlement(self):
        strategy = ExitStrategy(
            id="settlement",
            name="Settlement",
        )

        result = simulate_exit_strategy(
            strategy=strategy,
            market_ticker="TEST",
            traded_side="yes",
            entry_ts=0,
            entry_price=0.20,
            eventual_win=True,
            future=[],
        )

        self.assertEqual(result.exit_reason, "SETTLEMENT")
        self.assertAlmostEqual(result.exit_price, 1.0)
        self.assertAlmostEqual(result.profit, 0.80)


    def test_impossible_take_profit_is_ineligible(self):
        strategy = ExitStrategy(
            id="tp10",
            name="TP +10",
            take_profit_cents=10,
        )

        result = simulate_exit_strategy(
            strategy=strategy,
            market_ticker="TEST",
            traded_side="yes",
            entry_ts=0,
            entry_price=0.95,
            eventual_win=True,
            future=[],
        )

        self.assertEqual(result.exit_reason, "INELIGIBLE")

    def test_impossible_stop_loss_is_ineligible(self):
        strategy = ExitStrategy(
            id="sl10",
            name="SL -10",
            stop_loss_cents=10,
        )

        result = simulate_exit_strategy(
            strategy=strategy,
            market_ticker="TEST",
            traded_side="yes",
            entry_ts=0,
            entry_price=0.05,
            eventual_win=False,
            future=[],
        )

        self.assertEqual(result.exit_reason, "INELIGIBLE")


if __name__ == "__main__":
    unittest.main()


def test_strategy_summary_profit_confidence_interval():
    """Regression test for the normal-approximation profit CI."""
    from math import isclose

    from kalshi_stats.models import ExitStrategy, StrategyOutcome
    from kalshi_stats.strategies import summarize_strategy

    strategy = ExitStrategy(
        id="ci_test",
        name="CI test",
        time_exit_seconds=60,
        hold_to_settlement=False,
    )

    profits = [0.01, 0.02, 0.03, 0.04]

    outcomes = [
        StrategyOutcome(
            strategy_id=strategy.id,
            market_ticker=f"TEST-{index}",
            traded_side="yes",
            entry_ts=index,
            entry_price=0.50,
            exit_reason="TIME_EXIT",
            exit_price=0.50 + profit,
            profit=profit,
            holding_seconds=60,
            take_profit_hit=False,
            stop_loss_hit=False,
            ambiguous=False,
        )
        for index, profit in enumerate(profits)
    ]

    summary = summarize_strategy(strategy, outcomes)

    assert summary.observations == 4
    assert isclose(summary.avg_profit, 0.025, rel_tol=1e-12)
    assert isclose(
        summary.profit_stddev,
        0.012909944487358056,
        rel_tol=1e-12,
    )
    assert isclose(
        summary.profit_ci_low,
        0.012348254402389106,
        rel_tol=1e-12,
    )
    assert isclose(
        summary.profit_ci_high,
        0.0376517455976109,
        rel_tol=1e-12,
    )
