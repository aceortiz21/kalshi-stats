import math

import pytest

from kalshi_stats.ml_baselines import (
    build_logistic_pipeline,
    market_equal_probability_metrics,
    probability_metrics,
)
from kalshi_stats.ml_dataset import (
    EXCLUDED_LEAKAGE_COLUMNS,
    FEATURE_COLUMNS,
    MLDataset,
    market_midpoint,
)
from kalshi_stats.ml_residual_walkforward import (
    MARKET_LOGIT_EPSILON,
    MODEL_FEATURES,
    STATE_ONLY_FORBIDDEN_FIELDS,
    STATIONARY_STATE_FEATURES,
    market_logit,
    model_matrix,
)
from kalshi_stats.ml_walkforward import build_chronological_folds


def _dataset() -> MLDataset:
    tickers = tuple(f"M{market}" for market in range(8) for _ in range(2))
    timestamps = tuple(
        timestamp
        for market in range(8)
        for timestamp in (market * 10, market * 10 + 1)
    )
    features = []
    for row in range(16):
        features.append(
            tuple(float(row + column) for column, _ in enumerate(FEATURE_COLUMNS))
        )
    return MLDataset(
        feature_names=FEATURE_COLUMNS,
        features=tuple(features),
        targets=tuple(market % 2 for market in range(8) for _ in range(2)),
        market_tickers=tickers,
        observed_timestamps=timestamps,
        market_probabilities=tuple(0.25 + 0.5 * (row % 2) for row in range(16)),
    )


def test_market_midpoint_uses_v1_bid_ask_definition():
    assert market_midpoint(0.20, 0.40) == pytest.approx(0.30)
    assert market_midpoint(0.0, 0.0) == 0.0
    assert market_midpoint(1.0, 1.0) == 1.0


def test_market_logit_is_finite_at_boundaries_and_does_not_clip_interior():
    assert math.isfinite(market_logit(0.0))
    assert math.isfinite(market_logit(1.0))
    assert market_logit(0.0) == pytest.approx(math.log(MARKET_LOGIT_EPSILON / (1 - MARKET_LOGIT_EPSILON)))
    assert market_logit(1.0) == pytest.approx(math.log((1 - MARKET_LOGIT_EPSILON) / MARKET_LOGIT_EPSILON))
    assert market_logit(0.25) == pytest.approx(math.log(0.25 / 0.75))


def test_state_only_excludes_kalshi_prices_absolute_levels_and_result():
    assert not set(STATIONARY_STATE_FEATURES) & STATE_ONLY_FORBIDDEN_FIELDS
    assert not set(STATIONARY_STATE_FEATURES) & set(EXCLUDED_LEAKAGE_COLUMNS)
    assert all("kalshi_price" not in name for name in STATIONARY_STATE_FEATURES)
    assert all(not name.startswith("yes_bid") for name in STATIONARY_STATE_FEATURES)
    assert all(not name.startswith("yes_ask") for name in STATIONARY_STATE_FEATURES)


def test_market_plus_state_reuses_exact_stationary_feature_set():
    assert MODEL_FEATURES["STATE_ONLY"] == STATIONARY_STATE_FEATURES
    assert MODEL_FEATURES["MARKET_PLUS_STATE"] == (
        "market_logit",
    ) + MODEL_FEATURES["STATE_ONLY"]


def test_all_models_use_same_fold_membership_and_no_result_feature():
    dataset = _dataset()
    folds = build_chronological_folds(
        dataset, fold_count=2, initial_train_fraction=0.5
    )
    membership_by_model = {
        model: [
            (fold.train_markets, fold.test_markets)
            for fold in folds
        ]
        for model in MODEL_FEATURES
    }
    assert len({repr(membership) for membership in membership_by_model.values()}) == 1
    assert all(
        "result" not in feature_names for feature_names in MODEL_FEATURES.values()
    )


def test_residual_preprocessing_is_fit_only_on_training_rows():
    dataset = _dataset()
    feature_names = MODEL_FEATURES["MARKET_PLUS_STATE"]
    train_indices = list(range(8))
    test_indices = list(range(8, 16))
    train_x = model_matrix(dataset, train_indices, feature_names)
    test_x = model_matrix(dataset, test_indices, feature_names)
    model = build_logistic_pipeline()
    model.fit(train_x, [0, 0, 1, 1, 0, 0, 1, 1])
    imputation_before = model.named_steps["imputer"].statistics_.copy()
    scaling_before = model.named_steps["scaler"].mean_.copy()

    model.predict_proba(test_x)

    assert model.named_steps["imputer"].statistics_.tolist() == imputation_before.tolist()
    assert model.named_steps["scaler"].mean_.tolist() == scaling_before.tolist()


def test_market_equal_metrics_do_not_overweight_markets_with_more_rows():
    targets = [0, 0, 0, 1]
    probabilities = [0.0, 0.0, 0.0, 0.0]
    tickers = ["MANY", "MANY", "MANY", "ONE"]

    row_weighted = probability_metrics(targets, probabilities)
    market_equal = market_equal_probability_metrics(targets, probabilities, tickers)

    assert row_weighted.brier_score == pytest.approx(0.25)
    assert market_equal.market_count == 2
    assert market_equal.row_count == 4
    assert market_equal.brier_score == pytest.approx(0.5)
