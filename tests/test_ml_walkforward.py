import pytest

from kalshi_stats.ml_baselines import (
    build_logistic_pipeline,
    calibration_bins,
    probability_metrics,
)
from kalshi_stats.ml_dataset import MLDataset
from kalshi_stats.ml_walkforward import build_chronological_folds


def _dataset(*, overlapping=False):
    tickers = []
    timestamps = []
    features = []
    targets = []
    probabilities = []
    for market_index in range(8):
        ticker = f"M{market_index}"
        start = market_index * 10
        market_timestamps = (start, start + (15 if overlapping else 1))
        for timestamp in market_timestamps:
            tickers.append(ticker)
            timestamps.append(timestamp)
            features.append((float(market_index),))
            targets.append(market_index % 2)
            probabilities.append(0.5)
    return MLDataset(
        feature_names=("safe",),
        features=tuple(features),
        targets=tuple(targets),
        market_tickers=tuple(tickers),
        observed_timestamps=tuple(timestamps),
        market_probabilities=tuple(probabilities),
    )


def test_market_level_walkforward_has_no_overlap_and_respects_chronology():
    dataset = _dataset()
    folds = build_chronological_folds(
        dataset, fold_count=2, initial_train_fraction=0.5
    )
    assert len(folds) == 2
    for fold in folds:
        assert not set(fold.train_markets) & set(fold.test_markets)
        assert fold.train_end_ts < fold.test_start_ts
    assert folds[0].train_markets == ("M0", "M1", "M2", "M3")
    assert folds[0].test_markets == ("M4", "M5")
    assert folds[1].train_markets == ("M0", "M1", "M2", "M3", "M4", "M5")
    assert folds[1].test_markets == ("M6", "M7")


def test_overlapping_market_paths_are_rejected_at_fold_boundary():
    with pytest.raises(ValueError, match="overlap chronological boundary"):
        build_chronological_folds(
            _dataset(overlapping=True), fold_count=2, initial_train_fraction=0.5
        )


def test_preprocessing_is_fit_only_on_training_rows_and_handles_missing_values():
    train_x = [[1.0, None], [3.0, 10.0], [2.0, 20.0], [4.0, 30.0]]
    train_y = [0, 1, 0, 1]
    test_x = [[1000.0, -999.0], [2000.0, None]]
    model = build_logistic_pipeline()
    model.fit(train_x, train_y)
    before_imputation = model.named_steps["imputer"].statistics_.copy()
    before_scaling = model.named_steps["scaler"].mean_.copy()

    first = model.predict_proba(test_x)
    second = model.predict_proba(test_x)

    assert before_imputation.tolist() == [2.5, 20.0]
    assert before_scaling.tolist() == [2.5, 20.0]
    assert model.named_steps["imputer"].statistics_.tolist() == [2.5, 20.0]
    assert model.named_steps["scaler"].mean_.tolist() == [2.5, 20.0]
    assert first.tolist() == second.tolist()


def test_probability_metrics_and_calibration_on_known_example():
    metrics = probability_metrics([0, 1], [0.0, 1.0])
    assert metrics.sample_count == 2
    assert metrics.brier_score == 0.0
    assert metrics.log_loss == pytest.approx(1e-15)

    bins = calibration_bins([0, 1], [0.05, 0.95], bin_count=2)
    assert bins[0]["count"] == 1
    assert bins[0]["observed_yes_rate"] == 0.0
    assert bins[1]["count"] == 1
    assert bins[1]["observed_yes_rate"] == 1.0
