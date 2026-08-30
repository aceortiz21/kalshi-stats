from __future__ import annotations

import hashlib
import random


EXCLUDED_EXIT_REASONS = {
    "AMBIGUOUS",
    "INELIGIBLE",
}


def usable_strategy_outcomes(
    outcomes,
):
    """Match the simulator summary's usable-outcome semantics."""

    return [
        outcome
        for outcome in outcomes
        if outcome.exit_reason
        not in EXCLUDED_EXIT_REASONS
    ]


def _percentile(
    values,
    probability: float,
) -> float | None:
    if not values:
        return None

    ordered = sorted(
        float(value)
        for value in values
    )

    if len(ordered) == 1:
        return ordered[0]

    position = (
        probability
        * (len(ordered) - 1)
    )

    lower = int(position)
    upper = min(
        lower + 1,
        len(ordered) - 1,
    )

    fraction = (
        position - lower
    )

    return (
        ordered[lower]
        * (1.0 - fraction)
        + ordered[upper]
        * fraction
    )


def stable_seed(
    key: str,
) -> int:
    digest = hashlib.sha256(
        key.encode("utf-8")
    ).digest()

    return int.from_bytes(
        digest[:8],
        "big",
    )


def cluster_bootstrap_mean_ci(
    outcomes,
    *,
    iterations: int = 3000,
    confidence: float = 0.95,
    seed: int = 0,
):
    """
    Percentile bootstrap CI clustered by market ticker.

    Entire markets are sampled with replacement, preserving all
    usable strategy observations belonging to the sampled market.
    """

    if iterations < 100:
        raise ValueError(
            "iterations must be at least 100"
        )

    if not (
        0 < confidence < 1
    ):
        raise ValueError(
            "confidence must be between 0 and 1"
        )

    usable = usable_strategy_outcomes(
        outcomes
    )

    clusters = {}

    for outcome in usable:
        ticker = str(
            outcome.market_ticker
        )

        cluster = clusters.setdefault(
            ticker,
            [
                0.0,
                0,
            ],
        )

        cluster[0] += float(
            outcome.profit
        )

        cluster[1] += 1

    cluster_values = list(
        clusters.values()
    )

    cluster_count = len(
        cluster_values
    )

    observation_count = sum(
        count
        for _, count
        in cluster_values
    )

    if (
        cluster_count == 0
        or observation_count == 0
    ):
        return {
            "cluster_count": 0,
            "observations": 0,
            "mean": None,
            "ci_low": None,
            "ci_high": None,
        }

    total_profit = sum(
        profit_sum
        for profit_sum, _
        in cluster_values
    )

    observed_mean = (
        total_profit
        / observation_count
    )

    rng = random.Random(
        seed
    )

    bootstrap_means = []

    for _ in range(
        iterations
    ):
        sample_profit = 0.0
        sample_n = 0

        for _ in range(
            cluster_count
        ):
            profit_sum, count = (
                cluster_values[
                    rng.randrange(
                        cluster_count
                    )
                ]
            )

            sample_profit += (
                profit_sum
            )

            sample_n += count

        if sample_n:
            bootstrap_means.append(
                sample_profit
                / sample_n
            )

    alpha = (
        1.0 - confidence
    )

    return {
        "cluster_count": (
            cluster_count
        ),
        "observations": (
            observation_count
        ),
        "mean": observed_mean,
        "ci_low": _percentile(
            bootstrap_means,
            alpha / 2.0,
        ),
        "ci_high": _percentile(
            bootstrap_means,
            1.0 - alpha / 2.0,
        ),
    }
