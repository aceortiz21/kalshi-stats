from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Sequence

from .ml_baselines import build_logistic_pipeline, calibration_bins, probability_metrics
from .ml_dataset import (
    EXCLUDED_LEAKAGE_COLUMNS,
    FEATURE_CLASSIFICATION,
    FEATURE_COLUMNS,
    MLDataset,
    UNAVAILABLE_CANDIDATES,
    build_ml_dataset,
)


@dataclass(frozen=True)
class ChronologicalFold:
    fold: int
    train_markets: tuple[str, ...]
    test_markets: tuple[str, ...]
    train_start_ts: int
    train_end_ts: int
    test_start_ts: int
    test_end_ts: int


def build_chronological_folds(
    dataset: MLDataset, *, fold_count: int = 5, initial_train_fraction: float = 0.5
) -> list[ChronologicalFold]:
    if fold_count < 1 or not 0.0 < initial_train_fraction < 1.0:
        raise ValueError("invalid walk-forward configuration")
    bounds: dict[str, list[int]] = {}
    for ticker, timestamp in zip(
        dataset.market_tickers, dataset.observed_timestamps, strict=True
    ):
        if ticker not in bounds:
            bounds[ticker] = [timestamp, timestamp]
        else:
            bounds[ticker][0] = min(bounds[ticker][0], timestamp)
            bounds[ticker][1] = max(bounds[ticker][1], timestamp)

    markets = sorted(bounds, key=lambda ticker: (bounds[ticker][0], ticker))
    initial_count = max(1, int(len(markets) * initial_train_fraction))
    remaining = len(markets) - initial_count
    if remaining < fold_count:
        raise ValueError("not enough markets for requested folds")

    folds = []
    for fold_index in range(fold_count):
        test_start = initial_count + (remaining * fold_index) // fold_count
        test_end = initial_count + (remaining * (fold_index + 1)) // fold_count
        train_markets = tuple(markets[:test_start])
        test_markets = tuple(markets[test_start:test_end])
        train_end_ts = max(bounds[ticker][1] for ticker in train_markets)
        test_start_ts = min(bounds[ticker][0] for ticker in test_markets)
        if not train_end_ts < test_start_ts:
            raise ValueError(
                "market paths overlap chronological boundary: "
                f"train_end={train_end_ts}, test_start={test_start_ts}"
            )
        if set(train_markets) & set(test_markets):
            raise AssertionError("market crossed a fold boundary")
        folds.append(
            ChronologicalFold(
                fold=fold_index + 1,
                train_markets=train_markets,
                test_markets=test_markets,
                train_start_ts=min(bounds[ticker][0] for ticker in train_markets),
                train_end_ts=train_end_ts,
                test_start_ts=test_start_ts,
                test_end_ts=max(bounds[ticker][1] for ticker in test_markets),
            )
        )
    return folds


def _indices_for(dataset: MLDataset, markets: Sequence[str]) -> list[int]:
    market_set = set(markets)
    return [
        index
        for index, ticker in enumerate(dataset.market_tickers)
        if ticker in market_set
    ]


def _timestamp_iso(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _band_summary(
    targets: Sequence[int],
    logistic: Sequence[float],
    market: Sequence[float],
    values: Sequence[float],
    tickers: Sequence[str],
    bands: Sequence[tuple[str, float, float]],
) -> list[dict]:
    output = []
    for label, lower, upper in bands:
        indices = [
            index
            for index, value in enumerate(values)
            if lower <= value < upper
        ]
        if not indices:
            continue
        y = [targets[index] for index in indices]
        lp = [logistic[index] for index in indices]
        mp = [market[index] for index in indices]
        output.append(
            {
                "band": label,
                "sample_count": len(indices),
                "unique_market_count": len({tickers[index] for index in indices}),
                "logistic": asdict(probability_metrics(y, lp)),
                "kalshi_midpoint": asdict(probability_metrics(y, mp)),
            }
        )
    return output


def run_walkforward(
    connection: sqlite3.Connection, *, fold_count: int = 5
) -> dict:
    dataset = build_ml_dataset(connection)
    if "result" in dataset.feature_names:
        raise AssertionError("settlement result entered the feature matrix")
    folds = build_chronological_folds(dataset, fold_count=fold_count)
    all_targets: list[int] = []
    all_logistic: list[float] = []
    all_market: list[float] = []
    all_prices: list[float] = []
    all_seconds: list[float] = []
    all_tickers: list[str] = []
    per_fold = []
    price_index = dataset.feature_names.index("kalshi_price_close")
    seconds_index = dataset.feature_names.index("seconds_remaining")

    for fold in folds:
        train_indices = _indices_for(dataset, fold.train_markets)
        test_indices = _indices_for(dataset, fold.test_markets)
        train_x, train_y = dataset.take(train_indices)
        test_x, test_y = dataset.take(test_indices)
        model = build_logistic_pipeline()
        model.fit(train_x, train_y)
        logistic_probabilities = model.predict_proba(test_x)[:, 1].tolist()
        market_probabilities = [dataset.market_probabilities[i] for i in test_indices]

        all_targets.extend(test_y)
        all_logistic.extend(logistic_probabilities)
        all_market.extend(market_probabilities)
        all_prices.extend(float(dataset.features[i][price_index]) for i in test_indices)
        all_seconds.extend(float(dataset.features[i][seconds_index]) for i in test_indices)
        all_tickers.extend(dataset.market_tickers[i] for i in test_indices)
        per_fold.append(
            {
                "fold": fold.fold,
                "train_market_count": len(fold.train_markets),
                "test_market_count": len(fold.test_markets),
                "train_row_count": len(train_indices),
                "test_row_count": len(test_indices),
                "boundaries": {
                    "train_start_ts": fold.train_start_ts,
                    "train_start_utc": _timestamp_iso(fold.train_start_ts),
                    "train_end_ts": fold.train_end_ts,
                    "train_end_utc": _timestamp_iso(fold.train_end_ts),
                    "test_start_ts": fold.test_start_ts,
                    "test_start_utc": _timestamp_iso(fold.test_start_ts),
                    "test_end_ts": fold.test_end_ts,
                    "test_end_utc": _timestamp_iso(fold.test_end_ts),
                },
                "logistic": asdict(probability_metrics(test_y, logistic_probabilities)),
                "kalshi_midpoint": asdict(
                    probability_metrics(test_y, market_probabilities)
                ),
                "logistic_calibration": calibration_bins(test_y, logistic_probabilities),
                "kalshi_midpoint_calibration": calibration_bins(
                    test_y, market_probabilities
                ),
            }
        )

    unique_test_markets = len({ticker for fold in folds for ticker in fold.test_markets})
    return {
        "evidence_type": "HISTORICAL_RETROSPECTIVE_WALK_FORWARD_RESEARCH",
        "generated_at_utc": datetime.now(tz=timezone.utc).isoformat(),
        "model": "logistic_regression",
        "feature_whitelist": list(FEATURE_COLUMNS),
        "feature_classification": FEATURE_CLASSIFICATION,
        "unavailable_candidate_features": UNAVAILABLE_CANDIDATES,
        "explicitly_excluded_leakage_columns": list(EXCLUDED_LEAKAGE_COLUMNS),
        "dataset_row_count": len(dataset.targets),
        "dataset_unique_market_count": len(set(dataset.market_tickers)),
        "walkforward_test_row_count": len(all_targets),
        "walkforward_test_unique_market_count": unique_test_markets,
        "chronology": {
            "market_order_timestamp": "minimum observed_ts per market",
            "reason": (
                "It is the first timestamp at which this historical feature source "
                "can represent the market; each boundary also enforces that the "
                "maximum train observation is strictly before the minimum test observation."
            ),
            "design": "five expanding-window market-level folds; oldest 50% initial train",
        },
        "preprocessing": (
            "Per-fold training-only median imputation, then training-only standard "
            "scaling, then logistic regression."
        ),
        "kalshi_baseline": (
            "Arithmetic midpoint of contemporaneous YES bid_close and YES ask_close; "
            "rows missing either quote are excluded before either model is evaluated."
        ),
        "folds": per_fold,
        "aggregate_logistic": asdict(probability_metrics(all_targets, all_logistic)),
        "aggregate_kalshi_midpoint": asdict(
            probability_metrics(all_targets, all_market)
        ),
        "aggregate_comparison": {
            "logistic_beats_kalshi_brier": probability_metrics(
                all_targets, all_logistic
            ).brier_score
            < probability_metrics(all_targets, all_market).brier_score,
            "logistic_beats_kalshi_log_loss": probability_metrics(
                all_targets, all_logistic
            ).log_loss
            < probability_metrics(all_targets, all_market).log_loss,
        },
        "aggregate_logistic_calibration": calibration_bins(all_targets, all_logistic),
        "aggregate_kalshi_midpoint_calibration": calibration_bins(
            all_targets, all_market
        ),
        "by_contract_price_band": _band_summary(
            all_targets,
            all_logistic,
            all_market,
            all_prices,
            all_tickers,
            [
                ("0.00-0.10", 0.0, 0.1),
                ("0.10-0.30", 0.1, 0.3),
                ("0.30-0.70", 0.3, 0.7),
                ("0.70-0.90", 0.7, 0.9),
                ("0.90-1.00", 0.9, 1.0000001),
            ],
        ),
        "by_seconds_remaining_band": _band_summary(
            all_targets,
            all_logistic,
            all_market,
            all_seconds,
            all_tickers,
            [
                ("0-60", 0.0, 60.0),
                ("60-180", 60.0, 180.0),
                ("180-300", 180.0, 300.0),
                ("300-600", 300.0, 600.0),
                ("600-901", 600.0, 901.0),
            ],
        ),
        "warnings": [
            "Historical retrospective evidence only; this is not prospective proof.",
            "Repeated rows within a market are correlated; folds isolate markets, but row-level metrics weight markets with more observations more heavily.",
            "Historical Kalshi data is approximately one-minute candle/quote sampling, not full-depth order-book data.",
            "The same settled label is repeated for each timestamp in a market; unique market counts must accompany row counts.",
            "No BRTI feature is joined in ML Dataset V1.",
            "Probability quality does not establish profitability or executable edge.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Chronological logistic-regression baseline for P(YES settlement)."
    )
    parser.add_argument("--db", required=True)
    parser.add_argument("--out", default="reports/ml_logistic_walkforward.json")
    parser.add_argument("--folds", type=int, default=5)
    args = parser.parse_args()

    connection = sqlite3.connect(args.db)
    try:
        report = run_walkforward(connection, fold_count=args.folds)
    finally:
        connection.close()
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(output),
        "dataset_rows": report["dataset_row_count"],
        "markets": report["dataset_unique_market_count"],
        "aggregate_logistic": report["aggregate_logistic"],
        "aggregate_kalshi_midpoint": report["aggregate_kalshi_midpoint"],
    }, indent=2))


if __name__ == "__main__":
    main()
