from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Sequence


FEATURE_COLUMNS = (
    "kalshi_price_close",
    "kalshi_price_low",
    "kalshi_price_high",
    "yes_bid_close",
    "yes_ask_close",
    "seconds_remaining",
    "threshold",
    "btc_age_ms",
    "spot",
    "threshold_distance_dollars",
    "threshold_distance_pct",
    "threshold_distance_bps",
    "threshold_distance_vol60",
    "return_30s",
    "return_60s",
    "return_180s",
    "return_300s",
    "ema_5s",
    "ema_9s",
    "ema_21s",
    "ema_5s_9s_bps",
    "ema_9s_21s_bps",
    "ema_5s_slope_bps",
    "ema_9s_slope_bps",
    "ema_21s_slope_bps",
    "ema_5m",
    "ema_9m",
    "ema_21m",
    "ema_5m_9m_bps",
    "ema_9m_21m_bps",
    "ema_5m_slope_bps",
    "ema_9m_slope_bps",
    "ema_21m_slope_bps",
    "vwap_60s_proxy",
    "vwap_300s_proxy",
    "vwap_distance_60s_bps",
    "vwap_distance_300s_bps",
    "realized_vol_60s_bps",
    "realized_vol_300s_bps",
    "range_60s_bps",
    "range_300s_bps",
    "btc_volume_60s",
    "btc_volume_300s",
    "relative_volume_60s",
)

FEATURE_CLASSIFICATION = {
    "market_ticker": "IDENTIFIER_ONLY",
    "observed_ts": "IDENTIFIER_ONLY",
    "feature_version": "IDENTIFIER_ONLY",
    "result": "LABEL_ONLY",
    "candle_source": "IDENTIFIER_ONLY",
    "kalshi_price_close": "SAFE_CONTEMPORANEOUS",
    "kalshi_price_low": "SAFE_CONTEMPORANEOUS",
    "kalshi_price_high": "SAFE_CONTEMPORANEOUS",
    "yes_bid_close": "SAFE_CONTEMPORANEOUS",
    "yes_ask_close": "SAFE_CONTEMPORANEOUS",
    "seconds_remaining": "SAFE_CONTEMPORANEOUS",
    "threshold": "SAFE_CONTEMPORANEOUS",
    "btc_source": "IDENTIFIER_ONLY",
    "btc_ts": "IDENTIFIER_ONLY",
    "btc_age_ms": "SAFE_CONTEMPORANEOUS",
    "spot": "SAFE_CONTEMPORANEOUS",
    "threshold_distance_dollars": "SAFE_CONTEMPORANEOUS",
    "threshold_distance_pct": "SAFE_CONTEMPORANEOUS",
    "threshold_distance_bps": "SAFE_CONTEMPORANEOUS",
    "threshold_distance_vol60": "SAFE_CONTEMPORANEOUS",
    "return_30s": "SAFE_CONTEMPORANEOUS",
    "return_60s": "SAFE_CONTEMPORANEOUS",
    "return_180s": "SAFE_CONTEMPORANEOUS",
    "return_300s": "SAFE_CONTEMPORANEOUS",
    "ema_5s": "SAFE_CONTEMPORANEOUS",
    "ema_9s": "SAFE_CONTEMPORANEOUS",
    "ema_21s": "SAFE_CONTEMPORANEOUS",
    "ema_5s_9s_bps": "SAFE_CONTEMPORANEOUS",
    "ema_9s_21s_bps": "SAFE_CONTEMPORANEOUS",
    "ema_5s_slope_bps": "SAFE_CONTEMPORANEOUS",
    "ema_9s_slope_bps": "SAFE_CONTEMPORANEOUS",
    "ema_21s_slope_bps": "SAFE_CONTEMPORANEOUS",
    "ema_5m": "SAFE_CONTEMPORANEOUS",
    "ema_9m": "SAFE_CONTEMPORANEOUS",
    "ema_21m": "SAFE_CONTEMPORANEOUS",
    "ema_5m_9m_bps": "SAFE_CONTEMPORANEOUS",
    "ema_9m_21m_bps": "SAFE_CONTEMPORANEOUS",
    "ema_5m_slope_bps": "SAFE_CONTEMPORANEOUS",
    "ema_9m_slope_bps": "SAFE_CONTEMPORANEOUS",
    "ema_21m_slope_bps": "SAFE_CONTEMPORANEOUS",
    "vwap_60s_proxy": "SAFE_CONTEMPORANEOUS",
    "vwap_300s_proxy": "SAFE_CONTEMPORANEOUS",
    "vwap_distance_60s_bps": "SAFE_CONTEMPORANEOUS",
    "vwap_distance_300s_bps": "SAFE_CONTEMPORANEOUS",
    "realized_vol_60s_bps": "SAFE_CONTEMPORANEOUS",
    "realized_vol_300s_bps": "SAFE_CONTEMPORANEOUS",
    "range_60s_bps": "SAFE_CONTEMPORANEOUS",
    "range_300s_bps": "SAFE_CONTEMPORANEOUS",
    "btc_volume_60s": "SAFE_CONTEMPORANEOUS",
    "btc_volume_300s": "SAFE_CONTEMPORANEOUS",
    "relative_volume_60s": "SAFE_CONTEMPORANEOUS",
}

EXCLUDED_LEAKAGE_COLUMNS = ("result",)

UNAVAILABLE_CANDIDATES = {
    "trade_imbalance": "UNAVAILABLE",
    "book_imbalance": "UNAVAILABLE",
    "timestamp_safe_historical_brti_join": "UNAVAILABLE",
}


def market_midpoint(yes_bid_close: float, yes_ask_close: float) -> float:
    """Return the fixed V1 contemporaneous Kalshi YES midpoint."""
    midpoint = (float(yes_bid_close) + float(yes_ask_close)) / 2.0
    if not 0.0 <= midpoint <= 1.0:
        raise ValueError(f"invalid YES quote midpoint: {midpoint}")
    return midpoint


@dataclass(frozen=True)
class MLDataset:
    feature_names: tuple[str, ...]
    features: tuple[tuple[float | None, ...], ...]
    targets: tuple[int, ...]
    market_tickers: tuple[str, ...]
    observed_timestamps: tuple[int, ...]
    market_probabilities: tuple[float, ...]

    def take(self, indices: Sequence[int]) -> tuple[list[list[float | None]], list[int]]:
        return (
            [list(self.features[index]) for index in indices],
            [self.targets[index] for index in indices],
        )


def _validate_schema(connection: sqlite3.Connection) -> None:
    actual = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(historical_market_features)"
        )
    }
    classified = set(FEATURE_CLASSIFICATION)
    if actual != classified:
        missing = sorted(classified - actual)
        unclassified = sorted(actual - classified)
        raise ValueError(
            "historical_market_features schema does not match the explicit "
            f"classification; missing={missing}, unclassified={unclassified}"
        )
    if set(FEATURE_COLUMNS) & set(EXCLUDED_LEAKAGE_COLUMNS):
        raise AssertionError("leakage column entered the feature whitelist")


def build_ml_dataset(connection: sqlite3.Connection) -> MLDataset:
    """Load timestamp-safe features and settlement labels through separate queries."""
    _validate_schema(connection)
    connection.row_factory = sqlite3.Row

    feature_sql = f"""
        SELECT
            market_ticker,
            observed_ts,
            {", ".join(FEATURE_COLUMNS)}
        FROM historical_market_features
        WHERE yes_bid_close IS NOT NULL
          AND yes_ask_close IS NOT NULL
        ORDER BY observed_ts, market_ticker
    """
    feature_rows = connection.execute(feature_sql).fetchall()

    label_rows = connection.execute(
        """
        SELECT market_ticker, result
        FROM historical_market_features
        GROUP BY market_ticker, result
        ORDER BY market_ticker
        """
    ).fetchall()
    labels: dict[str, int] = {}
    for row in label_rows:
        ticker = str(row["market_ticker"])
        result = str(row["result"]).lower()
        if result not in {"yes", "no"}:
            continue
        if ticker in labels:
            raise ValueError(f"market has inconsistent settlement labels: {ticker}")
        labels[ticker] = int(result == "yes")

    features: list[tuple[float | None, ...]] = []
    targets: list[int] = []
    tickers: list[str] = []
    timestamps: list[int] = []
    market_probabilities: list[float] = []

    for row in feature_rows:
        ticker = str(row["market_ticker"])
        if ticker not in labels:
            continue
        midpoint = market_midpoint(row["yes_bid_close"], row["yes_ask_close"])
        features.append(
            tuple(None if row[name] is None else float(row[name]) for name in FEATURE_COLUMNS)
        )
        targets.append(labels[ticker])
        tickers.append(ticker)
        timestamps.append(int(row["observed_ts"]))
        market_probabilities.append(midpoint)

    return MLDataset(
        feature_names=FEATURE_COLUMNS,
        features=tuple(features),
        targets=tuple(targets),
        market_tickers=tuple(tickers),
        observed_timestamps=tuple(timestamps),
        market_probabilities=tuple(market_probabilities),
    )
