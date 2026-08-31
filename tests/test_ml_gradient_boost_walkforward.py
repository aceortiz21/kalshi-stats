import numpy as np

from kalshi_stats.ml_baselines import (
    TREE_MODEL_CONFIGURATION,
    build_fixed_hist_gradient_boosting_classifier,
)
from kalshi_stats.ml_dataset import EXCLUDED_LEAKAGE_COLUMNS, FEATURE_COLUMNS, MLDataset
from kalshi_stats.ml_gradient_boost_walkforward import (
    LINEAR_MODEL_FEATURES,
    TREE_MODEL_FEATURES,
    _fit_predict_tree_fold,
    _phase2_comparisons,
    _stability_summary,
)
from kalshi_stats.ml_residual_walkforward import (
    STATE_ONLY_FORBIDDEN_FIELDS,
    STATIONARY_STATE_FEATURES,
)
from kalshi_stats.ml_walkforward import build_chronological_folds


EXPECTED_TREE_CONFIGURATION = {
    "loss": "log_loss",
    "learning_rate": 0.05,
    "max_iter": 200,
    "max_leaf_nodes": 15,
    "max_depth": None,
    "min_samples_leaf": 50,
    "l2_regularization": 1.0,
    "max_features": 1.0,
    "max_bins": 255,
    "categorical_features": None,
    "early_stopping": False,
    "random_state": 0,
}


def _dataset() -> MLDataset:
    rows = 16
    return MLDataset(
        feature_names=FEATURE_COLUMNS,
        features=tuple(
            tuple(float(row * 100 + column) for column in range(len(FEATURE_COLUMNS)))
            for row in range(rows)
        ),
        targets=tuple((row // 2) % 2 for row in range(rows)),
        market_tickers=tuple(f"M{row // 2}" for row in range(rows)),
        observed_timestamps=tuple((row // 2) * 10 + row % 2 for row in range(rows)),
        market_probabilities=tuple(0.25 if row % 2 == 0 else 0.75 for row in range(rows)),
    )


def _metrics(brier: float, log_loss: float) -> dict:
    return {"row_weighted": {"brier_score": brier, "log_loss": log_loss}}


def test_exact_fixed_hist_gradient_boosting_configuration():
    assert TREE_MODEL_CONFIGURATION == EXPECTED_TREE_CONFIGURATION
    model = build_fixed_hist_gradient_boosting_classifier()
    parameters = model.get_params(deep=False)
    assert {name: parameters[name] for name in EXPECTED_TREE_CONFIGURATION} == (
        EXPECTED_TREE_CONFIGURATION
    )
    assert model.early_stopping is False


def test_tree_models_share_identical_existing_chronological_fold_membership():
    dataset = _dataset()
    existing_folds = build_chronological_folds(
        dataset, fold_count=2, initial_train_fraction=0.5
    )
    memberships = {
        name: [(fold.train_markets, fold.test_markets) for fold in existing_folds]
        for name in (*LINEAR_MODEL_FEATURES, *TREE_MODEL_FEATURES)
    }
    assert len({repr(value) for value in memberships.values()}) == 1
    assert memberships["TREE_MARKET_PLUS_STATE"] == memberships[
        "LINEAR_MARKET_PLUS_STATE"
    ]


def test_tree_state_only_cannot_receive_market_price_or_logit():
    features = TREE_MODEL_FEATURES["TREE_STATE_ONLY"]
    assert features == STATIONARY_STATE_FEATURES
    assert not set(features) & STATE_ONLY_FORBIDDEN_FIELDS
    assert "market_logit" not in features
    assert not set(features) & set(EXCLUDED_LEAKAGE_COLUMNS)


def test_tree_market_plus_state_uses_only_logit_plus_frozen_state():
    assert TREE_MODEL_FEATURES["TREE_MARKET_PLUS_STATE"] == (
        "market_logit",
    ) + STATIONARY_STATE_FEATURES


def test_tree_fit_receives_training_rows_only_and_predicts_test_rows_only():
    dataset = _dataset()

    class SpyEstimator:
        def fit(self, features, targets):
            self.fit_features = features
            self.fit_targets = targets
            return self

        def predict_proba(self, features):
            self.predict_features = features
            return np.array([[0.4, 0.6] for _ in features])

    spy = SpyEstimator()
    probabilities = _fit_predict_tree_fold(
        dataset,
        train_indices=(0, 1, 2, 3),
        test_indices=(12, 13, 14, 15),
        feature_names=TREE_MODEL_FEATURES["TREE_STATE_ONLY"],
        estimator=spy,
    )

    assert len(spy.fit_features) == 4
    assert len(spy.fit_targets) == 4
    assert len(spy.predict_features) == 4
    assert spy.fit_features[0][0] != spy.predict_features[0][0]
    assert probabilities == [0.6, 0.6, 0.6, 0.6]


def test_native_missing_value_handling_is_deterministic_and_bounded():
    model = build_fixed_hist_gradient_boosting_classifier()
    features = [[0.0, None], [1.0, 1.0], [2.0, None], [3.0, 3.0]] * 30
    targets = [0, 0, 1, 1] * 30
    model.fit(features, targets)
    first = model.predict_proba([[1.5, None], [2.5, 2.0]])[:, 1]
    second = model.predict_proba([[1.5, None], [2.5, 2.0]])[:, 1]
    assert np.array_equal(first, second)
    assert np.all((0.0 <= first) & (first <= 1.0))


def test_report_comparison_and_five_fold_stability_logic():
    metrics = {
        "RAW_KALSHI_MIDPOINT": _metrics(0.20, 0.50),
        "MARKET_ONLY": _metrics(0.21, 0.51),
        "LINEAR_STATE_ONLY": _metrics(0.30, 0.60),
        "LINEAR_MARKET_PLUS_STATE": _metrics(0.19, 0.49),
        "TREE_STATE_ONLY": _metrics(0.29, 0.59),
        "TREE_MARKET_PLUS_STATE": _metrics(0.18, 0.48),
    }
    comparisons = _phase2_comparisons(metrics)
    assert comparisons["tree_state_only_beats_linear_state_only_both"] is True
    assert comparisons[
        "tree_market_plus_state_beats_linear_market_plus_state_both"
    ] is True
    assert comparisons["tree_market_plus_state_beats_raw_kalshi_both"] is True

    winning_fold = {"comparisons": comparisons}
    stable = _stability_summary([winning_fold] * 5)
    assert stable["folds_beating_raw_kalshi_on_both_metrics"] == 5
    assert stable["stable_improvement_exists"] is True

    unstable = _stability_summary([winning_fold] * 4)
    assert unstable["folds_beating_raw_kalshi_on_both_metrics"] == 4
    assert unstable["stable_improvement_exists"] is False


def test_brier_only_win_does_not_count_as_both_or_stable():
    metrics = {
        "RAW_KALSHI_MIDPOINT": _metrics(0.20, 0.50),
        "MARKET_ONLY": _metrics(0.20, 0.50),
        "LINEAR_STATE_ONLY": _metrics(0.30, 0.60),
        "LINEAR_MARKET_PLUS_STATE": _metrics(0.20, 0.50),
        "TREE_STATE_ONLY": _metrics(0.30, 0.60),
        "TREE_MARKET_PLUS_STATE": _metrics(0.19, 0.51),
    }
    comparisons = _phase2_comparisons(metrics)
    assert comparisons["tree_market_plus_state_beats_raw_kalshi_brier"] is True
    assert comparisons["tree_market_plus_state_beats_raw_kalshi_log_loss"] is False
    assert comparisons["tree_market_plus_state_beats_raw_kalshi_both"] is False
    assert _stability_summary([{"comparisons": comparisons}] * 5)[
        "stable_improvement_exists"
    ] is False
