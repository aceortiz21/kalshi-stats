from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Sequence

from .ml_baselines import (
    TREE_MODEL_CONFIGURATION,
    TREE_MODEL_RATIONALE,
    build_fixed_hist_gradient_boosting_classifier,
    build_logistic_pipeline,
)
from .ml_dataset import EXCLUDED_LEAKAGE_COLUMNS, MLDataset, build_ml_dataset
from .ml_residual_walkforward import (
    MARKET_LOGIT_EPSILON,
    MODEL_FEATURES,
    STATE_ONLY_FORBIDDEN_FIELDS,
    STATIONARY_STATE_FEATURES,
    _band_summary,
    _comparison,
    _fold_boundaries,
    _indices_for,
    _score_summary,
    model_matrix,
)
from .ml_walkforward import build_chronological_folds


TREE_MODEL_FEATURES = {
    "TREE_STATE_ONLY": STATIONARY_STATE_FEATURES,
    "TREE_MARKET_PLUS_STATE": ("market_logit",) + STATIONARY_STATE_FEATURES,
}

LINEAR_MODEL_FEATURES = {
    "MARKET_ONLY": MODEL_FEATURES["MARKET_ONLY"],
    "LINEAR_STATE_ONLY": MODEL_FEATURES["STATE_ONLY"],
    "LINEAR_MARKET_PLUS_STATE": MODEL_FEATURES["MARKET_PLUS_STATE"],
}

if TREE_MODEL_FEATURES["TREE_STATE_ONLY"] != STATIONARY_STATE_FEATURES:
    raise AssertionError("TREE_STATE_ONLY must use the frozen stationary feature set")
if TREE_MODEL_FEATURES["TREE_MARKET_PLUS_STATE"] != (
    "market_logit",
) + TREE_MODEL_FEATURES["TREE_STATE_ONLY"]:
    raise AssertionError(
        "TREE_MARKET_PLUS_STATE must use only market_logit plus frozen state"
    )
if set(TREE_MODEL_FEATURES["TREE_STATE_ONLY"]) & STATE_ONLY_FORBIDDEN_FIELDS:
    raise AssertionError("forbidden market price, level, or label in TREE_STATE_ONLY")


def _fit_predict_tree_fold(
    dataset: MLDataset,
    train_indices: Sequence[int],
    test_indices: Sequence[int],
    feature_names: Sequence[str],
    *,
    estimator=None,
) -> list[float]:
    """Fit only training rows and predict the disjoint held-out rows."""
    if set(train_indices) & set(test_indices):
        raise ValueError("training and test row indices overlap")
    model = estimator or build_fixed_hist_gradient_boosting_classifier()
    train_x = model_matrix(dataset, train_indices, feature_names)
    train_y = [dataset.targets[index] for index in train_indices]
    test_x = model_matrix(dataset, test_indices, feature_names)
    model.fit(train_x, train_y)
    probabilities = model.predict_proba(test_x)[:, 1].tolist()
    if any(not 0.0 <= float(value) <= 1.0 for value in probabilities):
        raise ValueError("tree emitted a probability outside [0, 1]")
    return probabilities


def _probability_distribution(probabilities: Sequence[float]) -> dict:
    ordered = sorted(float(value) for value in probabilities)
    if not ordered:
        raise ValueError("probability distribution requires observations")

    def quantile(fraction: float) -> float:
        return ordered[round((len(ordered) - 1) * fraction)]

    return {
        "minimum": ordered[0],
        "p01": quantile(0.01),
        "p05": quantile(0.05),
        "median": quantile(0.50),
        "p95": quantile(0.95),
        "p99": quantile(0.99),
        "maximum": ordered[-1],
        "count_at_or_below_0_001": sum(value <= 0.001 for value in ordered),
        "count_at_or_below_0_01": sum(value <= 0.01 for value in ordered),
        "count_at_or_above_0_99": sum(value >= 0.99 for value in ordered),
        "count_at_or_above_0_999": sum(value >= 0.999 for value in ordered),
    }


def _phase2_comparisons(metrics: dict) -> dict[str, bool]:
    comparisons: dict[str, bool] = {}
    pairs = (
        (
            "TREE_STATE_ONLY",
            "LINEAR_STATE_ONLY",
            "tree_state_only_beats_linear_state_only",
        ),
        (
            "TREE_MARKET_PLUS_STATE",
            "LINEAR_MARKET_PLUS_STATE",
            "tree_market_plus_state_beats_linear_market_plus_state",
        ),
        (
            "TREE_MARKET_PLUS_STATE",
            "RAW_KALSHI_MIDPOINT",
            "tree_market_plus_state_beats_raw_kalshi",
        ),
        (
            "TREE_MARKET_PLUS_STATE",
            "MARKET_ONLY",
            "tree_market_plus_state_beats_linear_market_only",
        ),
    )
    for left, right, prefix in pairs:
        comparisons.update(_comparison(metrics[left], metrics[right], prefix))
    return comparisons


def _stability_summary(per_fold: Sequence[dict], *, required_fold_count: int = 5) -> dict:
    wins = sum(
        bool(
            fold["comparisons"][
                "tree_market_plus_state_beats_raw_kalshi_both"
            ]
        )
        for fold in per_fold
    )
    stable = len(per_fold) == required_fold_count and wins == required_fold_count
    return {
        "predeclared_rule": (
            "TREE_MARKET_PLUS_STATE must beat RAW_KALSHI_MIDPOINT on both "
            "row-weighted Brier score and row-weighted log loss in all five folds."
        ),
        "required_fold_count": required_fold_count,
        "evaluated_fold_count": len(per_fold),
        "folds_beating_raw_kalshi_on_both_metrics": wins,
        "stable_improvement_exists": stable,
    }


def run_gradient_boost_walkforward(
    connection: sqlite3.Connection, *, fold_count: int = 5
) -> dict:
    dataset = build_ml_dataset(connection)
    all_model_features = set().union(
        *TREE_MODEL_FEATURES.values(), *LINEAR_MODEL_FEATURES.values()
    )
    if set(EXCLUDED_LEAKAGE_COLUMNS) & all_model_features:
        raise AssertionError("settlement result entered a model feature matrix")
    folds = build_chronological_folds(dataset, fold_count=fold_count)

    all_targets: list[int] = []
    all_tickers: list[str] = []
    all_prices: list[float] = []
    all_seconds: list[float] = []
    model_names = (
        "RAW_KALSHI_MIDPOINT",
        *LINEAR_MODEL_FEATURES,
        *TREE_MODEL_FEATURES,
    )
    all_probabilities: dict[str, list[float]] = {name: [] for name in model_names}
    per_fold = []
    price_index = dataset.feature_names.index("kalshi_price_close")
    seconds_index = dataset.feature_names.index("seconds_remaining")

    for fold in folds:
        train_indices = _indices_for(dataset, fold.train_markets)
        test_indices = _indices_for(dataset, fold.test_markets)
        if set(train_indices) & set(test_indices):
            raise AssertionError("model fitting rows overlap held-out rows")
        train_targets = [dataset.targets[index] for index in train_indices]
        test_targets = [dataset.targets[index] for index in test_indices]
        test_tickers = [dataset.market_tickers[index] for index in test_indices]
        fold_probabilities: dict[str, list[float]] = {
            "RAW_KALSHI_MIDPOINT": [
                dataset.market_probabilities[index] for index in test_indices
            ]
        }

        for model_name, feature_names in LINEAR_MODEL_FEATURES.items():
            model = build_logistic_pipeline()
            model.fit(model_matrix(dataset, train_indices, feature_names), train_targets)
            fold_probabilities[model_name] = model.predict_proba(
                model_matrix(dataset, test_indices, feature_names)
            )[:, 1].tolist()

        for model_name, feature_names in TREE_MODEL_FEATURES.items():
            fold_probabilities[model_name] = _fit_predict_tree_fold(
                dataset, train_indices, test_indices, feature_names
            )

        metrics = {
            name: _score_summary(test_targets, probabilities, test_tickers)
            for name, probabilities in fold_probabilities.items()
        }
        per_fold.append(
            {
                "fold": fold.fold,
                "train_market_count": len(fold.train_markets),
                "test_market_count": len(fold.test_markets),
                "train_row_count": len(train_indices),
                "test_row_count": len(test_indices),
                "boundaries": _fold_boundaries(fold),
                "train_market_tickers": list(fold.train_markets),
                "test_market_tickers": list(fold.test_markets),
                "metrics": metrics,
                "probability_distributions": {
                    name: _probability_distribution(probabilities)
                    for name, probabilities in fold_probabilities.items()
                },
                "comparisons": _phase2_comparisons(metrics),
            }
        )

        all_targets.extend(test_targets)
        all_tickers.extend(test_tickers)
        all_prices.extend(
            float(dataset.features[index][price_index]) for index in test_indices
        )
        all_seconds.extend(
            float(dataset.features[index][seconds_index]) for index in test_indices
        )
        for name, probabilities in fold_probabilities.items():
            all_probabilities[name].extend(probabilities)

    aggregate_metrics = {
        name: _score_summary(all_targets, probabilities, all_tickers)
        for name, probabilities in all_probabilities.items()
    }
    aggregate_probability_distributions = {
        name: _probability_distribution(probabilities)
        for name, probabilities in all_probabilities.items()
    }
    price_band_results = _band_summary(
        all_targets,
        all_probabilities,
        all_prices,
        all_tickers,
        [
            ("0.00-0.10", 0.0, 0.1),
            ("0.10-0.30", 0.1, 0.3),
            ("0.30-0.70", 0.3, 0.7),
            ("0.70-0.90", 0.7, 0.9),
            ("0.90-1.00", 0.9, 1.0000001),
        ],
    )
    time_band_results = _band_summary(
        all_targets,
        all_probabilities,
        all_seconds,
        all_tickers,
        [
            ("0-60", 0.0, 60.0),
            ("60-180", 60.0, 180.0),
            ("180-300", 180.0, 300.0),
            ("300-600", 300.0, 600.0),
            ("600-901", 600.0, 901.0),
        ],
    )
    aggregate_comparisons = _phase2_comparisons(aggregate_metrics)
    stability = _stability_summary(per_fold)
    tree_vs_raw_brier = aggregate_comparisons[
        "tree_market_plus_state_beats_raw_kalshi_brier"
    ]
    tree_vs_raw_log = aggregate_comparisons[
        "tree_market_plus_state_beats_raw_kalshi_log_loss"
    ]
    mixed_metric_warning = tree_vs_raw_brier != tree_vs_raw_log
    suspicious_findings = []
    if mixed_metric_warning:
        suspicious_findings.append(
            "TREE_MARKET_PLUS_STATE improves only one of Brier and log loss versus "
            "raw Kalshi; if Brier is better but log loss is worse, some probability "
            "errors became dangerously confident."
        )
    if not stability["stable_improvement_exists"]:
        suspicious_findings.append(
            "TREE_MARKET_PLUS_STATE does not beat raw Kalshi on both metrics in all "
            "five folds, so any aggregate improvement is chronologically unstable."
        )
    mixed_bands = []
    for dimension, bands in (
        ("contract price", price_band_results),
        ("seconds remaining", time_band_results),
    ):
        for band in bands:
            raw = band["metrics"]["RAW_KALSHI_MIDPOINT"]["row_weighted"]
            tree = band["metrics"]["TREE_MARKET_PLUS_STATE"]["row_weighted"]
            if (
                tree["brier_score"] < raw["brier_score"]
                and tree["log_loss"] > raw["log_loss"]
            ):
                mixed_bands.append(f"{dimension} {band['band']}")
    if mixed_bands:
        suspicious_findings.append(
            "TREE_MARKET_PLUS_STATE has better Brier but worse log loss than raw "
            "Kalshi in these bands, consistent with some dangerously confident "
            f"errors: {', '.join(mixed_bands)}."
        )
    tree_distribution = aggregate_probability_distributions[
        "TREE_MARKET_PLUS_STATE"
    ]
    raw_distribution = aggregate_probability_distributions[
        "RAW_KALSHI_MIDPOINT"
    ]
    if (
        tree_distribution["minimum"] < raw_distribution["minimum"]
        or tree_distribution["maximum"] > raw_distribution["maximum"]
    ):
        suspicious_findings.append(
            "TREE_MARKET_PLUS_STATE extends beyond the raw midpoint probability "
            "range at one or both extremes; inspect extreme errors alongside log loss."
        )
    if (
        aggregate_metrics["TREE_MARKET_PLUS_STATE"]["calibration"]
        ["expected_calibration_error_10_bins"]
        > aggregate_metrics["RAW_KALSHI_MIDPOINT"]["calibration"]
        ["expected_calibration_error_10_bins"]
    ):
        suspicious_findings.append(
            "TREE_MARKET_PLUS_STATE has worse aggregate 10-bin ECE than raw Kalshi."
        )

    return {
        "evidence_type": "HISTORICAL_RETROSPECTIVE_WALK_FORWARD_RESEARCH",
        "generated_at_utc": datetime.now(tz=timezone.utc).isoformat(),
        "experiment": "ML_PHASE_2_FIXED_NONLINEAR_GRADIENT_BOOSTED_TREE_BASELINE",
        "market_probability_definition": (
            "market_p = (yes_bid_close + yes_ask_close) / 2"
        ),
        "market_logit_definition": "log(market_p / (1 - market_p))",
        "market_logit_clipping": {
            "epsilon": MARKET_LOGIT_EPSILON,
            "rule": (
                "Replace exactly 0 with epsilon and exactly 1 with 1-epsilon; "
                "do not clip interior probabilities."
            ),
        },
        "fixed_tree_model_configuration": {
            "estimator": "sklearn.ensemble.HistGradientBoostingClassifier",
            **TREE_MODEL_CONFIGURATION,
        },
        "fixed_tree_model_rationale": TREE_MODEL_RATIONALE,
        "configuration_predeclared_before_results": True,
        "hyperparameter_search_performed": False,
        "stationary_state_feature_list": list(STATIONARY_STATE_FEATURES),
        "model_feature_lists": {
            **{
                name: list(features)
                for name, features in LINEAR_MODEL_FEATURES.items()
            },
            **{
                name: list(features) for name, features in TREE_MODEL_FEATURES.items()
            },
        },
        "explicitly_excluded_leakage_columns": list(EXCLUDED_LEAKAGE_COLUMNS),
        "tree_state_only_forbidden_fields": sorted(STATE_ONLY_FORBIDDEN_FIELDS),
        "dataset_row_count": len(dataset.targets),
        "dataset_unique_market_count": len(set(dataset.market_tickers)),
        "walkforward_test_row_count": len(all_targets),
        "walkforward_test_unique_market_count": len(set(all_tickers)),
        "folds_shared_by_all_models": True,
        "chronology": {
            "market_order_timestamp": "minimum observed_ts per market",
            "design": (
                "five expanding-window market-level folds; oldest 50% initial train"
            ),
            "boundary_rule": (
                "maximum train observation is strictly before minimum test observation"
            ),
            "all_rows_from_market_stay_together": True,
            "all_model_fitting_uses_training_rows_only": True,
        },
        "missing_values": (
            "HistGradientBoostingClassifier native deterministic missing-value "
            "routing is learned from each fold's training rows; no tree imputer is "
            "fitted. Linear comparators retain training-only median imputation."
        ),
        "folds": per_fold,
        "aggregate_metrics": aggregate_metrics,
        "aggregate_probability_distributions": aggregate_probability_distributions,
        "aggregate_comparisons": aggregate_comparisons,
        "stability": stability,
        "interpretation": {
            "does_tree_state_only_beat_linear_state_only_on_both_aggregate_metrics": aggregate_comparisons[
                "tree_state_only_beats_linear_state_only_both"
            ],
            "does_tree_market_plus_state_beat_linear_market_plus_state_on_both_aggregate_metrics": aggregate_comparisons[
                "tree_market_plus_state_beats_linear_market_plus_state_both"
            ],
            "does_tree_market_plus_state_beat_raw_kalshi_on_brier": tree_vs_raw_brier,
            "does_tree_market_plus_state_beat_raw_kalshi_on_log_loss": tree_vs_raw_log,
            "does_tree_market_plus_state_beat_raw_kalshi_on_both_aggregate_metrics": aggregate_comparisons[
                "tree_market_plus_state_beats_raw_kalshi_both"
            ],
            "folds_beating_raw_kalshi_on_both_metrics": stability[
                "folds_beating_raw_kalshi_on_both_metrics"
            ],
            "chronologically_stable_improvement_exists": stability[
                "stable_improvement_exists"
            ],
            "trading_edge_demonstrated": False,
        },
        "by_contract_price_band": price_band_results,
        "by_seconds_remaining_band": time_band_results,
        "feature_importance": {
            "reported": False,
            "reason": (
                "HistGradientBoostingClassifier has no clean intrinsic feature_importances_ "
                "attribute. Test-label permutation importance is intentionally omitted to "
                "avoid turning evaluation folds into a feature-selection surface."
            ),
        },
        "warnings": [
            "Historical retrospective evidence only; this is not prospective proof.",
            "No hyperparameter search was performed; the sole tree configuration was frozen before viewing Phase 2 outcomes.",
            "Repeated observations within a market are correlated; market-equal metrics reduce row weighting but do not make timestamps independent.",
            "Probability quality does not establish a fee-adjusted or executable trading edge.",
            "Any probability improvement requires independent confirmation and prospective, fee, and execution evidence.",
            "A better Brier score with worse log loss would indicate dangerously confident errors and must not be described as an unqualified improvement.",
        ],
        "suspicious_findings": suspicious_findings,
        "limitations": [
            "The five chronological test periods were inspected in prior ML phases and are evaluation evidence, not a tuning surface.",
            "Historical Kalshi observations are approximately one-minute samples and lack full-depth books, true IOC latency, and queue position.",
            "Historical BRTI, trade imbalance, and book imbalance are unavailable in timestamp-safe form and are omitted.",
            "The fixed configuration may underfit or overfit; this phase intentionally does not search alternatives.",
            "No independent untouched confirmation period or prospective evidence is included.",
            "Fee and execution analyses are outside this probability-baseline phase.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fixed nonlinear chronological gradient-boosted-tree baseline."
    )
    parser.add_argument("--db", required=True)
    parser.add_argument(
        "--out", default="reports/ml_gradient_boost_walkforward.json"
    )
    parser.add_argument("--folds", type=int, default=5)
    args = parser.parse_args()

    connection = sqlite3.connect(f"file:{Path(args.db).resolve()}?mode=ro", uri=True)
    try:
        report = run_gradient_boost_walkforward(connection, fold_count=args.folds)
    finally:
        connection.close()
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "dataset_rows": report["dataset_row_count"],
                "markets": report["dataset_unique_market_count"],
                "aggregate_metrics": report["aggregate_metrics"],
                "aggregate_comparisons": report["aggregate_comparisons"],
                "stability": report["stability"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
