from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


@dataclass(frozen=True)
class ProbabilityMetrics:
    sample_count: int
    brier_score: float
    log_loss: float


@dataclass(frozen=True)
class MarketEqualProbabilityMetrics:
    market_count: int
    row_count: int
    brier_score: float
    log_loss: float


def build_logistic_pipeline() -> Pipeline:
    return Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            (
                "logistic",
                LogisticRegression(
                    solver="lbfgs",
                    max_iter=2000,
                    random_state=0,
                ),
            ),
        ]
    )


def probability_metrics(
    targets: Sequence[int], probabilities: Sequence[float]
) -> ProbabilityMetrics:
    if len(targets) != len(probabilities) or not targets:
        raise ValueError("targets and probabilities must have equal non-zero length")
    epsilon = 1e-15
    brier = 0.0
    loss = 0.0
    for target, raw_probability in zip(targets, probabilities, strict=True):
        raw = float(raw_probability)
        if not 0.0 <= raw <= 1.0:
            raise ValueError(f"probability outside [0, 1]: {raw}")
        probability_for_log = min(max(raw, epsilon), 1.0 - epsilon)
        brier += (raw - int(target)) ** 2
        loss -= int(target) * math.log(probability_for_log) + (1 - int(target)) * math.log(
            1.0 - probability_for_log
        )
    count = len(targets)
    return ProbabilityMetrics(
        sample_count=count,
        brier_score=brier / count,
        log_loss=loss / count,
    )


def market_equal_probability_metrics(
    targets: Sequence[int],
    probabilities: Sequence[float],
    market_tickers: Sequence[str],
) -> MarketEqualProbabilityMetrics:
    """Average per-row probability scores within markets, then weight markets equally."""
    if not (len(targets) == len(probabilities) == len(market_tickers)) or not targets:
        raise ValueError("targets, probabilities, and tickers need equal non-zero length")
    by_market: dict[str, tuple[list[int], list[float]]] = {}
    for target, probability, ticker in zip(
        targets, probabilities, market_tickers, strict=True
    ):
        market_targets, market_probabilities = by_market.setdefault(
            str(ticker), ([], [])
        )
        market_targets.append(int(target))
        market_probabilities.append(float(probability))
    per_market = []
    for ticker, (market_targets, market_probabilities) in by_market.items():
        if len(set(market_targets)) != 1:
            raise ValueError(f"market has inconsistent targets: {ticker}")
        per_market.append(probability_metrics(market_targets, market_probabilities))
    return MarketEqualProbabilityMetrics(
        market_count=len(per_market),
        row_count=len(targets),
        brier_score=sum(item.brier_score for item in per_market) / len(per_market),
        log_loss=sum(item.log_loss for item in per_market) / len(per_market),
    )


def calibration_bins(
    targets: Sequence[int], probabilities: Sequence[float], *, bin_count: int = 10
) -> list[dict[str, float | int | None]]:
    if bin_count <= 0:
        raise ValueError("bin_count must be positive")
    buckets: list[list[tuple[int, float]]] = [[] for _ in range(bin_count)]
    for target, raw_probability in zip(targets, probabilities, strict=True):
        probability = min(max(float(raw_probability), 0.0), 1.0)
        index = min(int(probability * bin_count), bin_count - 1)
        buckets[index].append((int(target), probability))
    output = []
    for index, bucket in enumerate(buckets):
        count = len(bucket)
        output.append(
            {
                "lower": index / bin_count,
                "upper": (index + 1) / bin_count,
                "count": count,
                "mean_probability": (
                    sum(item[1] for item in bucket) / count if count else None
                ),
                "observed_yes_rate": (
                    sum(item[0] for item in bucket) / count if count else None
                ),
            }
        )
    return output
