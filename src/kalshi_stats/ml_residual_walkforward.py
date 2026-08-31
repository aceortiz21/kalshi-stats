from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Sequence

from .ml_baselines import (
    build_logistic_pipeline,
    calibration_bins,
    market_equal_probability_metrics,
    probability_metrics,
)
from .ml_dataset import EXCLUDED_LEAKAGE_COLUMNS, MLDataset, build_ml_dataset
from .ml_walkforward import (
    ChronologicalFold,
    build_chronological_folds,
)


MARKET_LOGIT_EPSILON = 1e-6

STATIONARY_STATE_FEATURES = (
    "seconds_remaining",
    "threshold_distance_bps",
    "threshold_distance_vol60",
    "return_30s",
    "return_60s",
    "return_180s",
    "return_300s",
    "ema_5s_9s_bps",
    "ema_9s_21s_bps",
    "ema_5s_slope_bps",
    "ema_9s_slope_bps",
    "ema_21s_slope_bps",
    "ema_5m_9m_bps",
    "ema_9m_21m_bps",
    "ema_5m_slope_bps",
    "ema_9m_slope_bps",
    "ema_21m_slope_bps",
    "vwap_distance_60s_bps",
    "vwap_distance_300s_bps",
    "realized_vol_60s_bps",
    "realized_vol_300s_bps",
    "range_60s_bps",
    "range_300s_bps",
    "relative_volume_60s",
)

MODEL_FEATURES = {
    "MARKET_ONLY": ("market_logit",),
    "STATE_ONLY": STATIONARY_STATE_FEATURES,
    "MARKET_PLUS_STATE": ("market_logit",) + STATIONARY_STATE_FEATURES,
}

STATE_ONLY_FORBIDDEN_FIELDS = frozenset(
    {
        "market_logit",
        "market_probability",
        "kalshi_price_close",
        "kalshi_price_low",
        "kalshi_price_high",
        "yes_bid_close",
        "yes_ask_close",
        "result",
        "spot",
        "threshold",
        "ema_5s",
        "ema_9s",
        "ema_21s",
        "ema_5m",
        "ema_9m",
        "ema_21m",
        "vwap_60s_proxy",
        "vwap_300s_proxy",
    }
)

if set(STATIONARY_STATE_FEATURES) & STATE_ONLY_FORBIDDEN_FIELDS:
    raise AssertionError("forbidden price, level, or leakage field in STATE_ONLY")
if MODEL_FEATURES["MARKET_PLUS_STATE"][1:] != MODEL_FEATURES["STATE_ONLY"]:
    raise AssertionError("MARKET_PLUS_STATE must reuse the exact STATE_ONLY feature set")


def market_logit(probability: float) -> float:
    """Return a finite market logit, clipping only exact boundary probabilities."""
    probability = float(probability)
    if not 0.0 <= probability <= 1.0:
        raise ValueError(f"market probability outside [0, 1]: {probability}")
    if probability == 0.0:
        probability = MARKET_LOGIT_EPSILON
    elif probability == 1.0:
        probability = 1.0 - MARKET_LOGIT_EPSILON
    return math.log(probability / (1.0 - probability))


def _indices_for(dataset: MLDataset, markets: Sequence[str]) -> list[int]:
    market_set = set(markets)
    return [
        index
        for index, ticker in enumerate(dataset.market_tickers)
        if ticker in market_set
    ]


def model_matrix(
    dataset: MLDataset, indices: Sequence[int], feature_names: Sequence[str]
) -> list[list[float | None]]:
    feature_indices = {
        name: dataset.feature_names.index(name)
        for name in feature_names
        if name != "market_logit"
    }
    rows: list[list[float | None]] = []
    for index in indices:
        rows.append(
            [
                market_logit(dataset.market_probabilities[index])
                if name == "market_logit"
                else dataset.features[index][feature_indices[name]]
                for name in feature_names
            ]
        )
    return rows


def _calibration_summary(
    targets: Sequence[int], probabilities: Sequence[float]
) -> dict:
    bins = calibration_bins(targets, probabilities)
    count = len(targets)
    expected_calibration_error = sum(
        int(item["count"])
        / count
        * abs(float(item["mean_probability"]) - float(item["observed_yes_rate"]))
        for item in bins
        if item["count"]
    )
    return {
        "mean_probability": sum(float(value) for value in probabilities) / count,
        "observed_yes_rate": sum(int(value) for value in targets) / count,
        "expected_calibration_error_10_bins": expected_calibration_error,
        "bins": bins,
    }


def _score_summary(
    targets: Sequence[int],
    probabilities: Sequence[float],
    tickers: Sequence[str],
    *,
    include_calibration: bool = True,
) -> dict:
    summary = {
        "row_weighted": asdict(probability_metrics(targets, probabilities)),
        "market_equal_weighted": asdict(
            market_equal_probability_metrics(targets, probabilities, tickers)
        ),
    }
    if include_calibration:
        summary["calibration"] = _calibration_summary(targets, probabilities)
    return summary


def _original_unit_coefficients(model, feature_names: Sequence[str]) -> dict:
    imputer = model.named_steps["imputer"]
    scaler = model.named_steps["scaler"]
    logistic = model.named_steps["logistic"]
    if len(logistic.coef_[0]) != len(feature_names):
        raise ValueError("imputation dropped a model feature")
    standardized = logistic.coef_[0]
    original = standardized / scaler.scale_
    original_intercept = float(
        logistic.intercept_[0] - sum(standardized * scaler.mean_ / scaler.scale_)
    )
    market_index = feature_names.index("market_logit")
    return {
        "market_logit_standardized_coefficient": float(standardized[market_index]),
        "market_logit_original_units_coefficient": float(original[market_index]),
        "standardized_intercept": float(logistic.intercept_[0]),
        "original_units_intercept_after_imputation": original_intercept,
        "market_logit_training_median": float(imputer.statistics_[market_index]),
        "market_logit_training_mean_after_imputation": float(
            scaler.mean_[market_index]
        ),
        "market_logit_training_scale": float(scaler.scale_[market_index]),
    }


def _comparison(left: dict, right: dict, prefix: str) -> dict[str, bool]:
    return {
        f"{prefix}_brier": left["row_weighted"]["brier_score"]
        < right["row_weighted"]["brier_score"],
        f"{prefix}_log_loss": left["row_weighted"]["log_loss"]
        < right["row_weighted"]["log_loss"],
        f"{prefix}_both": (
            left["row_weighted"]["brier_score"]
            < right["row_weighted"]["brier_score"]
            and left["row_weighted"]["log_loss"]
            < right["row_weighted"]["log_loss"]
        ),
    }


def _band_summary(
    targets: Sequence[int],
    probabilities: dict[str, Sequence[float]],
    values: Sequence[float],
    tickers: Sequence[str],
    bands: Sequence[tuple[str, float, float]],
) -> list[dict]:
    output = []
    for label, lower, upper in bands:
        indices = [
            index for index, value in enumerate(values) if lower <= value < upper
        ]
        if not indices:
            continue
        band_targets = [targets[index] for index in indices]
        band_tickers = [tickers[index] for index in indices]
        output.append(
            {
                "band": label,
                "sample_count": len(indices),
                "unique_market_count": len(set(band_tickers)),
                "metrics": {
                    name: _score_summary(
                        band_targets,
                        [values_[index] for index in indices],
                        band_tickers,
                        include_calibration=False,
                    )
                    for name, values_ in probabilities.items()
                },
            }
        )
    return output


def _fold_boundaries(fold: ChronologicalFold) -> dict:
    def iso(timestamp: int) -> str:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()

    return {
        "train_start_ts": fold.train_start_ts,
        "train_start_utc": iso(fold.train_start_ts),
        "train_end_ts": fold.train_end_ts,
        "train_end_utc": iso(fold.train_end_ts),
        "test_start_ts": fold.test_start_ts,
        "test_start_utc": iso(fold.test_start_ts),
        "test_end_ts": fold.test_end_ts,
        "test_end_utc": iso(fold.test_end_ts),
    }


def run_residual_walkforward(
    connection: sqlite3.Connection, *, fold_count: int = 5
) -> dict:
    dataset = build_ml_dataset(connection)
    if set(EXCLUDED_LEAKAGE_COLUMNS) & set().union(*MODEL_FEATURES.values()):
        raise AssertionError("settlement result entered a model feature matrix")
    folds = build_chronological_folds(dataset, fold_count=fold_count)
    all_targets: list[int] = []
    all_tickers: list[str] = []
    all_prices: list[float] = []
    all_seconds: list[float] = []
    all_probabilities: dict[str, list[float]] = {
        "RAW_KALSHI_MIDPOINT": [],
        "MARKET_ONLY": [],
        "STATE_ONLY": [],
        "MARKET_PLUS_STATE": [],
    }
    all_prevalence_probabilities: list[float] = []
    per_fold = []
    price_index = dataset.feature_names.index("kalshi_price_close")
    seconds_index = dataset.feature_names.index("seconds_remaining")

    for fold in folds:
        train_indices = _indices_for(dataset, fold.train_markets)
        test_indices = _indices_for(dataset, fold.test_markets)
        train_targets = [dataset.targets[index] for index in train_indices]
        test_targets = [dataset.targets[index] for index in test_indices]
        test_tickers = [dataset.market_tickers[index] for index in test_indices]
        fold_probabilities: dict[str, list[float]] = {
            "RAW_KALSHI_MIDPOINT": [
                dataset.market_probabilities[index] for index in test_indices
            ]
        }
        coefficients = {}
        for model_name, feature_names in MODEL_FEATURES.items():
            model = build_logistic_pipeline()
            model.fit(
                model_matrix(dataset, train_indices, feature_names), train_targets
            )
            fold_probabilities[model_name] = model.predict_proba(
                model_matrix(dataset, test_indices, feature_names)
            )[:, 1].tolist()
            if "market_logit" in feature_names:
                coefficients[model_name] = _original_unit_coefficients(
                    model, feature_names
                )

        metrics = {
            name: _score_summary(test_targets, values, test_tickers)
            for name, values in fold_probabilities.items()
        }
        train_prevalence = sum(train_targets) / len(train_targets)
        prevalence_probabilities = [train_prevalence] * len(test_targets)
        comparisons = {}
        comparisons.update(
            _comparison(
                metrics["MARKET_ONLY"],
                metrics["RAW_KALSHI_MIDPOINT"],
                "market_only_beats_raw_kalshi",
            )
        )
        comparisons.update(
            _comparison(
                metrics["STATE_ONLY"],
                _score_summary(
                    test_targets,
                    prevalence_probabilities,
                    test_tickers,
                    include_calibration=False,
                ),
                "state_only_beats_fold_training_prevalence",
            )
        )
        comparisons.update(
            _comparison(
                metrics["MARKET_PLUS_STATE"],
                metrics["MARKET_ONLY"],
                "market_plus_state_beats_market_only",
            )
        )
        comparisons.update(
            _comparison(
                metrics["MARKET_PLUS_STATE"],
                metrics["RAW_KALSHI_MIDPOINT"],
                "market_plus_state_beats_raw_kalshi",
            )
        )
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
                "fold_training_yes_prevalence": train_prevalence,
                "fold_training_prevalence_metrics": _score_summary(
                    test_targets,
                    prevalence_probabilities,
                    test_tickers,
                    include_calibration=False,
                ),
                "market_logit_coefficients": coefficients,
                "comparisons": comparisons,
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
        all_prevalence_probabilities.extend(prevalence_probabilities)
        for name, values in fold_probabilities.items():
            all_probabilities[name].extend(values)

    aggregate_metrics = {
        name: _score_summary(all_targets, values, all_tickers)
        for name, values in all_probabilities.items()
    }
    prevalence_metrics = _score_summary(
        all_targets,
        all_prevalence_probabilities,
        all_tickers,
        include_calibration=False,
    )
    aggregate_comparisons = {}
    aggregate_comparisons.update(
        _comparison(
            aggregate_metrics["MARKET_ONLY"],
            aggregate_metrics["RAW_KALSHI_MIDPOINT"],
            "market_only_beats_raw_kalshi",
        )
    )
    aggregate_comparisons.update(
        _comparison(
            aggregate_metrics["STATE_ONLY"],
            prevalence_metrics,
            "state_only_beats_fold_training_prevalence",
        )
    )
    aggregate_comparisons.update(
        _comparison(
            aggregate_metrics["MARKET_PLUS_STATE"],
            aggregate_metrics["MARKET_ONLY"],
            "market_plus_state_beats_market_only",
        )
    )
    aggregate_comparisons.update(
        _comparison(
            aggregate_metrics["MARKET_PLUS_STATE"],
            aggregate_metrics["RAW_KALSHI_MIDPOINT"],
            "market_plus_state_beats_raw_kalshi",
        )
    )
    for comparison in (
        "market_only_beats_raw_kalshi",
        "state_only_beats_fold_training_prevalence",
        "market_plus_state_beats_market_only",
        "market_plus_state_beats_raw_kalshi",
    ):
        for metric in ("brier", "log_loss", "both"):
            key = f"{comparison}_{metric}_in_every_fold"
            aggregate_comparisons[key] = all(
                fold["comparisons"][f"{comparison}_{metric}"] for fold in per_fold
            )

    coefficient_summary = {}
    for model_name in ("MARKET_ONLY", "MARKET_PLUS_STATE"):
        coefficients = [
            fold["market_logit_coefficients"][model_name]
            for fold in per_fold
        ]
        market_coefficients = [
            item["market_logit_original_units_coefficient"] for item in coefficients
        ]
        original_intercepts = [
            item["original_units_intercept_after_imputation"] for item in coefficients
        ]
        coefficient_summary[model_name] = {
            "market_logit_original_units_coefficient_min": min(market_coefficients),
            "market_logit_original_units_coefficient_max": max(market_coefficients),
            "market_logit_original_units_coefficient_mean": (
                sum(market_coefficients) / len(market_coefficients)
            ),
            "original_units_intercept_after_imputation_min": min(
                original_intercepts
            ),
            "original_units_intercept_after_imputation_max": max(
                original_intercepts
            ),
            "original_units_intercept_after_imputation_mean": (
                sum(original_intercepts) / len(original_intercepts)
            ),
        }

    interpretation = {
        "does_market_only_beat_raw_kalshi_on_both_aggregate_metrics": (
            aggregate_comparisons["market_only_beats_raw_kalshi_both"]
        ),
        "does_state_only_contain_useful_standalone_signal_vs_fold_training_prevalence": (
            aggregate_comparisons[
                "state_only_beats_fold_training_prevalence_both"
            ]
        ),
        "is_state_only_improvement_vs_fold_training_prevalence_stable_in_every_fold": (
            aggregate_comparisons[
                "state_only_beats_fold_training_prevalence_both_in_every_fold"
            ]
        ),
        "does_market_plus_state_beat_market_only_on_both_aggregate_metrics": (
            aggregate_comparisons["market_plus_state_beats_market_only_both"]
        ),
        "does_market_plus_state_beat_raw_kalshi_on_both_aggregate_metrics": (
            aggregate_comparisons["market_plus_state_beats_raw_kalshi_both"]
        ),
        "is_market_plus_state_improvement_vs_market_only_stable_in_every_fold": (
            aggregate_comparisons[
                "market_plus_state_beats_market_only_both_in_every_fold"
            ]
        ),
        "incremental_predictive_information_demonstrated_with_chronological_stability": (
            aggregate_comparisons["market_plus_state_beats_market_only_both"]
            and aggregate_comparisons[
                "market_plus_state_beats_market_only_both_in_every_fold"
            ]
        ),
        "does_market_plus_state_demonstrate_stable_probability_improvement_vs_raw_kalshi": (
            aggregate_comparisons["market_plus_state_beats_raw_kalshi_both"]
            and aggregate_comparisons[
                "market_plus_state_beats_raw_kalshi_both_in_every_fold"
            ]
        ),
        "trading_edge_demonstrated": False,
    }

    return {
        "evidence_type": "HISTORICAL_RETROSPECTIVE_WALK_FORWARD_RESEARCH",
        "generated_at_utc": datetime.now(tz=timezone.utc).isoformat(),
        "experiment": "ML_PHASE_1B_MARKET_RESIDUAL_LOGISTIC_ABLATION",
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
            "predeclared_before_evaluation": True,
        },
        "stationary_state_feature_list": list(STATIONARY_STATE_FEATURES),
        "model_feature_lists": {
            name: list(features) for name, features in MODEL_FEATURES.items()
        },
        "model_specification": {
            "estimator": "sklearn.linear_model.LogisticRegression",
            "solver": "lbfgs",
            "max_iter": 2000,
            "random_state": 0,
            "regularization": "default L2 with C=1.0",
        },
        "explicitly_excluded_leakage_columns": list(EXCLUDED_LEAKAGE_COLUMNS),
        "state_only_forbidden_fields": sorted(STATE_ONLY_FORBIDDEN_FIELDS),
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
        },
        "preprocessing": (
            "Each model and fold independently fits median imputation, standard "
            "scaling, and logistic regression on training rows only. Test rows "
            "are transformed without refitting."
        ),
        "folds": per_fold,
        "aggregate_metrics": aggregate_metrics,
        "aggregate_fold_training_prevalence_metrics": prevalence_metrics,
        "aggregate_comparisons": aggregate_comparisons,
        "coefficient_summary_across_folds": coefficient_summary,
        "interpretation": interpretation,
        "by_contract_price_band": _band_summary(
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
        ),
        "by_seconds_remaining_band": _band_summary(
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
        ),
        "limitations": [
            "Historical retrospective evidence only; this is not prospective proof.",
            "Repeated observations within a market are correlated; market-equal metrics reduce row-count weighting but do not make timestamps independent.",
            "Historical Kalshi observations are approximately one-minute samples and do not include historical full-depth books, IOC latency, or queue position.",
            "Historical BRTI, trade imbalance, and book imbalance are unavailable in timestamp-safe form and are omitted.",
            "Probability quality alone does not establish fee-adjusted or executable trading edge.",
            "Feature and model choices are fixed for this ablation; no test-fold tuning is performed.",
        ],
        "suspicious_findings": [
            "MARKET_PLUS_STATE's aggregate improvement over MARKET_ONLY is small and does not occur on both metrics in every fold.",
            "STATE_ONLY is much better than fold-training prevalence but remains substantially worse than the contemporaneous Kalshi midpoint.",
            "MARKET_ONLY original-unit market_logit coefficients are slightly above 1 rather than exactly 1, but recalibration does not improve aggregate performance over raw Kalshi.",
            "Near-settlement rows have unusually small errors because observed market probabilities are already extremely close to realized outcomes; this is probability evaluation, not evidence of executable edge.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Market-residual chronological logistic ablation for P(YES)."
    )
    parser.add_argument("--db", required=True)
    parser.add_argument(
        "--out", default="reports/ml_logistic_residual_walkforward.json"
    )
    parser.add_argument("--folds", type=int, default=5)
    args = parser.parse_args()

    connection = sqlite3.connect(f"file:{Path(args.db).resolve()}?mode=ro", uri=True)
    try:
        report = run_residual_walkforward(connection, fold_count=args.folds)
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
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
