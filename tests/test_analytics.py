from __future__ import annotations

import sqlite3
import unittest

from kalshi_stats.analytics import analyze_scenarios, build_probability_matrix
from kalshi_stats.database import connect, init_db
from kalshi_stats.models import ScenarioDefinition


def _market_row(ticker: str, result: str, open_time: str, close_time: str) -> tuple[object, ...]:
    return (
        ticker,
        "KXBTC15M",
        ticker.rsplit("-", 1)[0],
        "BTC price up in next 15 mins?",
        "finalized",
        result,
        "binary",
        open_time,
        close_time,
        close_time,
        close_time,
        close_time,
        "",
        "",
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )


def _insert_market(connection: sqlite3.Connection, ticker: str, result: str, open_time: str, close_time: str) -> None:
    connection.execute(
        """
        INSERT INTO markets (
            ticker, series_ticker, event_ticker, title, status, result, market_type,
            open_time, close_time, expected_expiration_time, settlement_ts, updated_time,
            yes_sub_title, no_sub_title, reference_price, final_price, last_price,
            yes_bid, yes_ask, no_bid, no_ask, volume, open_interest
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _market_row(ticker, result, open_time, close_time),
    )


def _insert_trade(connection: sqlite3.Connection, trade_id: str, ticker: str, created_time: str, yes_price: float) -> None:
    connection.execute(
        """
        INSERT INTO trades (
            trade_id, market_ticker, created_time, yes_price, no_price, count,
            taker_side, taker_book_side, taker_outcome_side
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade_id,
            ticker,
            created_time,
            yes_price,
            1.0 - yes_price,
            1.0,
            "yes",
            "bid",
            "yes",
        ),
    )


class AnalyticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = connect(":memory:")
        init_db(self.connection)
        _insert_market(
            self.connection,
            "MKT-A",
            "yes",
            "2026-08-29T00:00:00Z",
            "2026-08-29T00:15:00Z",
        )
        trades_a = [
            ("a1", "2026-08-29T00:00:30Z", 0.22),
            ("a2", "2026-08-29T00:01:00Z", 0.24),
            ("a3", "2026-08-29T00:02:00Z", 0.27),
            ("a4", "2026-08-29T00:03:00Z", 0.35),
            ("a5", "2026-08-29T00:04:00Z", 0.12),
            ("a6", "2026-08-29T00:05:30Z", 0.23),
            ("a7", "2026-08-29T00:06:00Z", 0.41),
            ("a8", "2026-08-29T00:10:00Z", 0.55),
            ("a9", "2026-08-29T00:10:30Z", 0.44),
            ("a10", "2026-08-29T00:12:30Z", 0.45),
            ("a11", "2026-08-29T00:14:30Z", 0.52),
        ]
        for trade_id, created_time, yes_price in trades_a:
            _insert_trade(self.connection, trade_id, "MKT-A", created_time, yes_price)

        _insert_market(
            self.connection,
            "MKT-B",
            "no",
            "2026-08-29T01:00:00Z",
            "2026-08-29T01:15:00Z",
        )
        trades_b = [
            ("b1", "2026-08-29T01:00:30Z", 0.82),
            ("b2", "2026-08-29T01:01:00Z", 0.81),
            ("b3", "2026-08-29T01:02:00Z", 0.72),
            ("b4", "2026-08-29T01:03:00Z", 0.64),
            ("b5", "2026-08-29T01:05:00Z", 0.45),
            ("b6", "2026-08-29T01:14:30Z", 0.61),
        ]
        for trade_id, created_time, yes_price in trades_b:
            _insert_trade(self.connection, trade_id, "MKT-B", created_time, yes_price)

        _insert_market(
            self.connection,
            "MKT-INCOMPLETE",
            "yes",
            "2026-08-29T02:00:00Z",
            "2026-08-29T02:15:00Z",
        )
        incomplete_trades = [
            ("c1", "2026-08-29T02:04:00Z", 0.21),
            ("c2", "2026-08-29T02:05:00Z", 0.33),
        ]
        for trade_id, created_time, yes_price in incomplete_trades:
            _insert_trade(self.connection, trade_id, "MKT-INCOMPLETE", created_time, yes_price)
        self.connection.commit()

    def tearDown(self) -> None:
        self.connection.close()

    def test_first_occurrence_per_market_deduplicates_consecutive_matches(self) -> None:
        scenario = ScenarioDefinition(
            id="band_20s",
            name="Band 20s",
            description="Test band.",
            trigger_price_min=0.20,
            trigger_price_max=0.29,
            targets=[0.30],
        )
        summaries, occurrences = analyze_scenarios(self.connection, [scenario])

        self.assertEqual(len(occurrences), 2)
        self.assertEqual(summaries[0].occurrences, 2)
        self.assertEqual(summaries[0].unique_markets, 2)

    def test_reentry_mode_counts_second_event_after_cooldown(self) -> None:
        scenario = ScenarioDefinition(
            id="reentry_20s",
            name="Reentry 20s",
            description="Test reentry band.",
            trigger_price_min=0.20,
            trigger_price_max=0.29,
            targets=[0.30],
            occurrence_mode="reentry_after_cooldown",
            cooldown_seconds=60,
        )
        _, occurrences = analyze_scenarios(self.connection, [scenario])

        market_a_occurrences = [item for item in occurrences if item.market_ticker == "MKT-A"]
        self.assertEqual(len(market_a_occurrences), 2)

    def test_target_touch_and_excursion_metrics(self) -> None:
        scenario = ScenarioDefinition(
            id="teens",
            name="Teens",
            description="Test teens band.",
            trigger_price_min=0.10,
            trigger_price_max=0.19,
            targets=[0.20],
        )
        summaries, occurrences = analyze_scenarios(self.connection, [scenario])

        occurrence = next(item for item in occurrences if item.market_ticker == "MKT-A")
        self.assertAlmostEqual(occurrence.entry_price, 0.12)
        self.assertAlmostEqual(occurrence.best_subsequent_price, 0.55)
        self.assertAlmostEqual(occurrence.worst_subsequent_price, 0.12)
        self.assertAlmostEqual(occurrence.max_favorable_excursion, 0.43)
        self.assertAlmostEqual(occurrence.max_favorable_excursion_pct or 0.0, 0.43 / 0.12, places=4)
        self.assertEqual(occurrence.target_hit_seconds[0.20], 90)
        self.assertAlmostEqual(occurrence.price_after_seconds[30] or 0.0, 0.23)
        self.assertAlmostEqual(summaries[0].target_hit_rates[0.20], 1.0)

    def test_yes_no_transformation_and_settlement(self) -> None:
        scenario = ScenarioDefinition(
            id="favorite_fade",
            name="Favorite fade",
            description="Opposite-side scenario.",
            trigger_price_min=0.80,
            trigger_price_max=0.89,
            targets=[0.20],
            trade_side="opposite",
        )
        summaries, occurrences = analyze_scenarios(self.connection, [scenario])

        occurrence = next(item for item in occurrences if item.market_ticker == "MKT-B")
        self.assertEqual(occurrence.trigger_side, "yes")
        self.assertEqual(occurrence.traded_side, "no")
        self.assertTrue(occurrence.eventual_win)
        self.assertGreaterEqual(summaries[0].occurrences, 1)
        self.assertGreaterEqual(summaries[0].unique_markets, 1)

    def test_time_bucket_breakdown_and_matrix_plus_rates(self) -> None:
        scenario = ScenarioDefinition(
            id="late_band",
            name="Late band",
            description="Late underdog test.",
            trigger_price_min=0.10,
            trigger_price_max=0.19,
            targets=[0.20],
            seconds_remaining_max=180,
        )
        summaries, _ = analyze_scenarios(self.connection, [scenario])
        matrix = build_probability_matrix(self.connection)

        self.assertIn("1-2m", summaries[0].time_breakdown)
        target_cell = next(cell for cell in matrix if cell.price_bucket == "20-29c" and cell.time_bucket == "10-15m left")
        self.assertGreaterEqual(target_cell.observations, 1)
        self.assertGreaterEqual(target_cell.unique_markets, 1)
        self.assertIsNotNone(target_cell.plus_5c_rate)

    def test_matrix_time_bucket_boundaries_split_2_to_3_and_3_to_5_minutes(self) -> None:
        matrix = build_probability_matrix(self.connection)

        three_to_five = next(
            cell for cell in matrix if cell.price_bucket == "40-49c" and cell.time_bucket == "3-5m left"
        )
        two_to_three = next(
            cell for cell in matrix if cell.price_bucket == "40-49c" and cell.time_bucket == "2-3m left"
        )

        self.assertGreaterEqual(three_to_five.observations, 1)
        self.assertGreaterEqual(two_to_three.observations, 1)

    def test_incomplete_histories_are_excluded(self) -> None:
        scenario = ScenarioDefinition(
            id="all_20s",
            name="All 20s",
            description="Checks incomplete exclusion.",
            trigger_price_min=0.20,
            trigger_price_max=0.29,
            targets=[0.30],
        )
        _, occurrences = analyze_scenarios(self.connection, [scenario])
        tickers = {item.market_ticker for item in occurrences}

        self.assertNotIn("MKT-INCOMPLETE", tickers)


if __name__ == "__main__":
    unittest.main()

class ChronologicalSplitTests(unittest.TestCase):
    def test_chronological_market_split_preserves_time_order(self):
        from kalshi_stats.analytics import chronological_market_split

        markets = [
            {"ticker": "m3", "close_time": "2026-08-03T00:00:00Z"},
            {"ticker": "m1", "close_time": "2026-08-01T00:00:00Z"},
            {"ticker": "m5", "close_time": "2026-08-05T00:00:00Z"},
            {"ticker": "m2", "close_time": "2026-08-02T00:00:00Z"},
            {"ticker": "m4", "close_time": "2026-08-04T00:00:00Z"},
        ]

        discovery, holdout = chronological_market_split(
            markets,
            discovery_fraction=0.80,
        )

        self.assertEqual(
            [market["ticker"] for market in discovery],
            ["m1", "m2", "m3", "m4"],
        )
        self.assertEqual(
            [market["ticker"] for market in holdout],
            ["m5"],
        )

        self.assertLess(
            discovery[-1]["close_time"],
            holdout[0]["close_time"],
        )

    def test_relative_target_ceiling_marks_impossible_moves_ineligible(self):
        from kalshi_stats.analytics import _relative_target_hit

        # 85c entry:
        # +5 -> 90c, +10 -> 95c, +15 -> 100c are possible.
        # +20 -> 105c is impossible.
        self.assertTrue(_relative_target_hit(0.85, 1.00, 0.05, True))
        self.assertTrue(_relative_target_hit(0.85, 1.00, 0.10, True))
        self.assertTrue(_relative_target_hit(0.85, 1.00, 0.15, True))
        self.assertIsNone(_relative_target_hit(0.85, 1.00, 0.20, True))

        # 95c entry:
        # +5 -> 100c is possible; larger targets are impossible.
        self.assertTrue(_relative_target_hit(0.95, 1.00, 0.05, True))
        self.assertIsNone(_relative_target_hit(0.95, 1.00, 0.10, True))
        self.assertIsNone(_relative_target_hit(0.95, 1.00, 0.15, True))
        self.assertIsNone(_relative_target_hit(0.95, 1.00, 0.20, True))

        # Even a mathematically possible target is ineligible without
        # a subsequent price path.
        self.assertIsNone(_relative_target_hit(0.50, 1.00, 0.10, False))

    def test_chronological_market_split_rejects_invalid_fraction(self):
        from kalshi_stats.analytics import chronological_market_split

        with self.assertRaises(ValueError):
            chronological_market_split([], discovery_fraction=1.0)


def test_classify_validated_strategy_statuses():
    from types import SimpleNamespace

    from kalshi_stats.analytics import classify_validated_strategy

    strong = SimpleNamespace(
        observations=500,
        avg_profit=0.01,
        profit_ci_low=0.002,
    )

    promising = SimpleNamespace(
        observations=500,
        avg_profit=0.01,
        profit_ci_low=-0.002,
    )

    failed = SimpleNamespace(
        observations=500,
        avg_profit=-0.01,
        profit_ci_low=-0.02,
    )

    insufficient = SimpleNamespace(
        observations=99,
        avg_profit=0.50,
        profit_ci_low=0.40,
    )

    assert classify_validated_strategy(strong) == "STRONG"
    assert classify_validated_strategy(promising) == "PROMISING"
    assert classify_validated_strategy(failed) == "FAILED"
    assert classify_validated_strategy(insufficient) == "INSUFFICIENT"



def test_expanding_walk_forward_splits_are_future_and_nonoverlapping():
    from kalshi_stats.analytics import (
        expanding_walk_forward_splits,
    )

    markets = [
        {
            "ticker": f"M{i}",
            "close_time": (
                f"2026-01-{i + 1:02d}T00:00:00Z"
            ),
        }
        for i in range(10)
    ]

    splits = expanding_walk_forward_splits(
        markets,
        fold_count=5,
        initial_train_fraction=0.50,
    )

    assert len(splits) == 5

    seen_test = set()

    for train, test in splits:
        train_tickers = {
            market["ticker"]
            for market in train
        }

        test_tickers = {
            market["ticker"]
            for market in test
        }

        assert train_tickers.isdisjoint(
            test_tickers
        )

        assert seen_test.isdisjoint(
            test_tickers
        )

        assert (
            train[-1]["close_time"]
            < test[0]["close_time"]
        )

        seen_test.update(
            test_tickers
        )

    assert seen_test == {
        "M5",
        "M6",
        "M7",
        "M8",
        "M9",
    }


def test_walk_forward_persistence_classification():
    from types import SimpleNamespace

    from kalshi_stats.analytics import (
        classify_walk_forward_persistence,
    )

    robust = SimpleNamespace(
        observations=500,
        avg_profit=0.01,
        profit_ci_low=0.002,
    )

    mixed = SimpleNamespace(
        observations=500,
        avg_profit=0.01,
        profit_ci_low=-0.002,
    )

    failed = SimpleNamespace(
        observations=500,
        avg_profit=-0.01,
        profit_ci_low=-0.02,
    )

    assert (
        classify_walk_forward_persistence(
            total_folds=5,
            qualified_folds=5,
            evaluated_folds=5,
            positive_folds=4,
            aggregate_summary=robust,
        )
        == "ROBUST"
    )

    assert (
        classify_walk_forward_persistence(
            total_folds=5,
            qualified_folds=5,
            evaluated_folds=5,
            positive_folds=3,
            aggregate_summary=mixed,
        )
        == "MIXED"
    )

    assert (
        classify_walk_forward_persistence(
            total_folds=5,
            qualified_folds=5,
            evaluated_folds=5,
            positive_folds=2,
            aggregate_summary=failed,
        )
        == "UNSTABLE"
    )

    assert (
        classify_walk_forward_persistence(
            total_folds=5,
            qualified_folds=2,
            evaluated_folds=2,
            positive_folds=2,
            aggregate_summary=robust,
        )
        == "INSUFFICIENT"
    )



def test_walk_forward_requires_repeated_qualification_for_robust():
    from types import SimpleNamespace

    from kalshi_stats.analytics import (
        classify_walk_forward_persistence,
    )

    summary = SimpleNamespace(
        observations=500,
        avg_profit=0.02,
        profit_ci_low=0.01,
    )

    assert (
        classify_walk_forward_persistence(
            total_folds=5,
            qualified_folds=3,
            evaluated_folds=3,
            positive_folds=3,
            aggregate_summary=summary,
        )
        == "MIXED"
    )
