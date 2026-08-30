from __future__ import annotations

import argparse
from datetime import datetime
import time

from .btc_features import (
    BTCFeatureEngine,
    BTCSecond,
)
from .database import (
    connect,
    init_db,
)


FEATURE_VERSION = 2
DEFAULT_BTC_SOURCE = "binance_1s"



class MinuteEMAEngine:
    """EMA state based on completed one-minute BTC bars."""

    def __init__(self):
        self.ema_5 = None
        self.ema_9 = None
        self.ema_21 = None

        self.previous_ema_5 = None
        self.previous_ema_9 = None
        self.previous_ema_21 = None

        self.last_minute_end_ms = None

    @staticmethod
    def _next_ema(
        previous,
        price: float,
        period: int,
    ) -> float:
        if previous is None:
            return float(price)

        alpha = (
            2.0
            / (period + 1.0)
        )

        return (
            alpha * float(price)
            + (1.0 - alpha)
            * float(previous)
        )

    @staticmethod
    def _spread_bps(
        faster,
        slower,
    ):
        if (
            faster is None
            or slower is None
            or slower == 0
        ):
            return None

        return (
            (faster - slower)
            / slower
            * 10000.0
        )

    @staticmethod
    def _slope_bps(
        current,
        previous,
    ):
        if (
            current is None
            or previous is None
            or previous == 0
        ):
            return None

        return (
            (current - previous)
            / previous
            * 10000.0
        )

    def add_completed_minute(
        self,
        *,
        minute_end_ms: int,
        close: float,
    ) -> None:
        if (
            self.last_minute_end_ms
            == minute_end_ms
        ):
            return

        self.previous_ema_5 = self.ema_5
        self.previous_ema_9 = self.ema_9
        self.previous_ema_21 = self.ema_21

        self.ema_5 = self._next_ema(
            self.ema_5,
            close,
            5,
        )

        self.ema_9 = self._next_ema(
            self.ema_9,
            close,
            9,
        )

        self.ema_21 = self._next_ema(
            self.ema_21,
            close,
            21,
        )

        self.last_minute_end_ms = (
            minute_end_ms
        )

    def snapshot(self):
        return {
            "ema_5m": self.ema_5,
            "ema_9m": self.ema_9,
            "ema_21m": self.ema_21,

            "ema_5m_9m_bps": (
                self._spread_bps(
                    self.ema_5,
                    self.ema_9,
                )
            ),

            "ema_9m_21m_bps": (
                self._spread_bps(
                    self.ema_9,
                    self.ema_21,
                )
            ),

            "ema_5m_slope_bps": (
                self._slope_bps(
                    self.ema_5,
                    self.previous_ema_5,
                )
            ),

            "ema_9m_slope_bps": (
                self._slope_bps(
                    self.ema_9,
                    self.previous_ema_9,
                )
            ),

            "ema_21m_slope_bps": (
                self._slope_bps(
                    self.ema_21,
                    self.previous_ema_21,
                )
            ),
        }


INSERT_COLUMNS = (
    "market_ticker",
    "observed_ts",
    "feature_version",

    "result",
    "candle_source",

    "kalshi_price_close",
    "kalshi_price_low",
    "kalshi_price_high",

    "yes_bid_close",
    "yes_ask_close",

    "seconds_remaining",

    "threshold",

    "btc_source",
    "btc_ts",
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


def _iso_to_ts(
    value: str,
) -> int:
    return int(
        datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00",
            )
        ).timestamp()
    )


def load_historical_observations(
    connection,
    *,
    series_ticker: str = "KXBTC15M",
):
    """
    Return one Kalshi observation per market/minute.

    Kalshi one-minute candle timestamps represent the end
    of the interval.
    """

    rows = connection.execute(
        """
        SELECT
            c.market_ticker,
            c.end_period_ts,
            c.source AS candle_source,

            c.price_close,
            c.price_low,
            c.price_high,

            c.yes_bid_close,
            c.yes_ask_close,

            m.result,
            m.open_time,
            m.close_time,
            m.reference_price

        FROM candles c

        JOIN markets m
          ON m.ticker = c.market_ticker

        WHERE
            c.period_interval = 1

            AND m.series_ticker = ?

            AND m.result IN (
                'yes',
                'no'
            )

            AND m.reference_price
                IS NOT NULL

        ORDER BY
            c.end_period_ts,
            c.market_ticker,
            c.source
        """,
        (
            series_ticker,
        ),
    ).fetchall()

    # Defensive dedupe in case the same minute was
    # ingested from more than one candle source.
    deduped = {}

    for row in rows:
        observed_ts = int(
            row["end_period_ts"]
        )

        open_ts = _iso_to_ts(
            row["open_time"]
        )

        close_ts = _iso_to_ts(
            row["close_time"]
        )

        # Same semantics used by the historical
        # strategy engine:
        #
        # open < observation <= close
        if not (
            open_ts
            < observed_ts
            <= close_ts
        ):
            continue

        key = (
            str(
                row[
                    "market_ticker"
                ]
            ),
            observed_ts,
        )

        deduped[key] = {
            "market_ticker": key[0],
            "observed_ts": (
                observed_ts
            ),
            "candle_source": (
                row[
                    "candle_source"
                ]
            ),
            "price_close": float(
                row["price_close"]
            ),
            "price_low": float(
                row["price_low"]
            ),
            "price_high": float(
                row["price_high"]
            ),
            "yes_bid_close": (
                None
                if row[
                    "yes_bid_close"
                ]
                is None
                else float(
                    row[
                        "yes_bid_close"
                    ]
                )
            ),
            "yes_ask_close": (
                None
                if row[
                    "yes_ask_close"
                ]
                is None
                else float(
                    row[
                        "yes_ask_close"
                    ]
                )
            ),
            "result": str(
                row["result"]
            ),
            "close_ts": close_ts,
            "threshold": float(
                row[
                    "reference_price"
                ]
            ),
        }

    return sorted(
        deduped.values(),
        key=lambda row: (
            row["observed_ts"],
            row["market_ticker"],
        ),
    )


def build_historical_feature_row(
    *,
    observation,
    btc_snapshot,
    minute_ema_snapshot,
    btc_ts: int,
    btc_source: str,
):
    threshold = float(
        observation["threshold"]
    )

    spot = float(
        btc_snapshot["spot"]
    )

    distance_dollars = (
        spot - threshold
    )

    distance_pct = (
        distance_dollars
        / threshold
    )

    distance_bps = (
        distance_pct
        * 10000.0
    )

    realized_vol_60 = (
        btc_snapshot[
            "realized_vol_60s_bps"
        ]
    )

    distance_vol60 = None

    if (
        realized_vol_60 is not None
        and float(
            realized_vol_60
        ) > 0
    ):
        distance_vol60 = (
            distance_bps
            / float(
                realized_vol_60
            )
        )

    observed_ms = (
        int(
            observation[
                "observed_ts"
            ]
        )
        * 1000
    )

    return {
        "market_ticker": (
            observation[
                "market_ticker"
            ]
        ),

        "observed_ts": (
            observation[
                "observed_ts"
            ]
        ),

        "feature_version": (
            FEATURE_VERSION
        ),

        "result": (
            observation[
                "result"
            ]
        ),

        "candle_source": (
            observation[
                "candle_source"
            ]
        ),

        "kalshi_price_close": (
            observation[
                "price_close"
            ]
        ),

        "kalshi_price_low": (
            observation[
                "price_low"
            ]
        ),

        "kalshi_price_high": (
            observation[
                "price_high"
            ]
        ),

        "yes_bid_close": (
            observation[
                "yes_bid_close"
            ]
        ),

        "yes_ask_close": (
            observation[
                "yes_ask_close"
            ]
        ),

        "seconds_remaining": max(
            0,
            int(
                observation[
                    "close_ts"
                ]
                - observation[
                    "observed_ts"
                ]
            ),
        ),

        "threshold": threshold,

        "btc_source": (
            btc_source
        ),

        "btc_ts": (
            int(
                btc_ts
            )
        ),

        "btc_age_ms": (
            observed_ms
            - int(
                btc_ts
            )
        ),

        "spot": spot,

        "threshold_distance_dollars": (
            distance_dollars
        ),

        "threshold_distance_pct": (
            distance_pct
        ),

        "threshold_distance_bps": (
            distance_bps
        ),

        "threshold_distance_vol60": (
            distance_vol60
        ),

        "return_30s": (
            btc_snapshot[
                "return_30s"
            ]
        ),

        "return_60s": (
            btc_snapshot[
                "return_60s"
            ]
        ),

        "return_180s": (
            btc_snapshot[
                "return_180s"
            ]
        ),

        "return_300s": (
            btc_snapshot[
                "return_300s"
            ]
        ),

        # These are deliberately named with "s".
        # The existing live feature engine is based
        # on completed 1-second bars.
        "ema_5s": (
            btc_snapshot[
                "ema_5"
            ]
        ),

        "ema_9s": (
            btc_snapshot[
                "ema_9"
            ]
        ),

        "ema_21s": (
            btc_snapshot[
                "ema_21"
            ]
        ),

        "ema_5s_9s_bps": (
            btc_snapshot[
                "ema_5_9_bps"
            ]
        ),

        "ema_9s_21s_bps": (
            btc_snapshot[
                "ema_9_21_bps"
            ]
        ),

        "ema_5s_slope_bps": (
            btc_snapshot[
                "ema_5_slope_bps"
            ]
        ),

        "ema_9s_slope_bps": (
            btc_snapshot[
                "ema_9_slope_bps"
            ]
        ),

        "ema_21s_slope_bps": (
            btc_snapshot[
                "ema_21_slope_bps"
            ]
        ),

        "ema_5m": (
            minute_ema_snapshot[
                "ema_5m"
            ]
        ),

        "ema_9m": (
            minute_ema_snapshot[
                "ema_9m"
            ]
        ),

        "ema_21m": (
            minute_ema_snapshot[
                "ema_21m"
            ]
        ),

        "ema_5m_9m_bps": (
            minute_ema_snapshot[
                "ema_5m_9m_bps"
            ]
        ),

        "ema_9m_21m_bps": (
            minute_ema_snapshot[
                "ema_9m_21m_bps"
            ]
        ),

        "ema_5m_slope_bps": (
            minute_ema_snapshot[
                "ema_5m_slope_bps"
            ]
        ),

        "ema_9m_slope_bps": (
            minute_ema_snapshot[
                "ema_9m_slope_bps"
            ]
        ),

        "ema_21m_slope_bps": (
            minute_ema_snapshot[
                "ema_21m_slope_bps"
            ]
        ),

        # Historical Binance archives give us OHLCV,
        # not individual trades. We feed each completed
        # second's close/volume into the same rolling
        # machinery. Therefore these are explicitly
        # labelled VWAP proxies.
        "vwap_60s_proxy": (
            btc_snapshot[
                "vwap_60s"
            ]
        ),

        "vwap_300s_proxy": (
            btc_snapshot[
                "vwap_300s"
            ]
        ),

        "vwap_distance_60s_bps": (
            btc_snapshot[
                "vwap_distance_60s_bps"
            ]
        ),

        "vwap_distance_300s_bps": (
            btc_snapshot[
                "vwap_distance_300s_bps"
            ]
        ),

        "realized_vol_60s_bps": (
            btc_snapshot[
                "realized_vol_60s_bps"
            ]
        ),

        "realized_vol_300s_bps": (
            btc_snapshot[
                "realized_vol_300s_bps"
            ]
        ),

        "range_60s_bps": (
            btc_snapshot[
                "range_60s_bps"
            ]
        ),

        "range_300s_bps": (
            btc_snapshot[
                "range_300s_bps"
            ]
        ),

        "btc_volume_60s": (
            btc_snapshot[
                "trade_volume_60s"
            ]
        ),

        "btc_volume_300s": (
            btc_snapshot[
                "trade_volume_300s"
            ]
        ),

        "relative_volume_60s": (
            btc_snapshot[
                "relative_volume_60s"
            ]
        ),
    }


def insert_rows(
    connection,
    rows,
) -> int:
    if not rows:
        return 0

    placeholders = ", ".join(
        "?"
        for _ in INSERT_COLUMNS
    )

    connection.executemany(
        f"""
        INSERT OR REPLACE INTO
        historical_market_features (
            {", ".join(INSERT_COLUMNS)}
        )
        VALUES (
            {placeholders}
        )
        """,
        [
            [
                row.get(column)
                for column
                in INSERT_COLUMNS
            ]
            for row in rows
        ],
    )

    return len(
        rows
    )


def materialize_historical_features(
    connection,
    *,
    series_ticker: str = "KXBTC15M",
    btc_source: str = DEFAULT_BTC_SOURCE,
    max_btc_age_ms: int = 3000,
    batch_size: int = 5000,
):
    observations = (
        load_historical_observations(
            connection,
            series_ticker=(
                series_ticker
            ),
        )
    )

    if not observations:
        return {
            "observations": 0,
            "saved": 0,
            "skipped_no_btc": 0,
        }

    first_observed_ms = (
        observations[0][
            "observed_ts"
        ]
        * 1000
    )

    last_observed_ms = (
        observations[-1][
            "observed_ts"
        ]
        * 1000
    )

    # Six hours of warmup makes the seed effect on
    # the 21-minute EMA negligible while remaining
    # very cheap relative to the full BTC history.
    btc_start_ms = (
        first_observed_ms
        - 6 * 60 * 60 * 1000
    )

    # STRICT ANTI-LOOKAHEAD:
    #
    # Binance 1s kline timestamps denote
    # the START of their second. A row
    # timestamped T contains information
    # from [T, T+1s).
    #
    # Therefore an observation at T can
    # use at most the bar starting T-1s.
    btc_end_ms = (
        last_observed_ms
        - 1000
    )

    cursor = connection.execute(
        """
        SELECT
            ts,
            open,
            high,
            low,
            close,
            volume

        FROM btc_1s

        WHERE source = ?
          AND ts >= ?
          AND ts <= ?

        ORDER BY ts
        """,
        (
            btc_source,
            btc_start_ms,
            btc_end_ms,
        ),
    )

    engine = BTCFeatureEngine(
        max_window_seconds=900
    )

    minute_engine = (
        MinuteEMAEngine()
    )

    next_btc = cursor.fetchone()

    last_btc_ts = None
    last_btc_close = None

    pending = []

    saved = 0
    skipped_no_btc = 0

    started = time.perf_counter()

    for index, observation in enumerate(
        observations,
        start=1,
    ):
        observed_ms = (
            observation[
                "observed_ts"
            ]
            * 1000
        )

        cutoff_ms = (
            observed_ms
            - 1000
        )

        while (
            next_btc is not None
            and int(
                next_btc["ts"]
            )
            <= cutoff_ms
        ):
            ts_ms = int(
                next_btc["ts"]
            )

            close = float(
                next_btc["close"]
            )

            volume = float(
                next_btc["volume"]
                or 0.0
            )

            engine.add_second(
                BTCSecond(
                    ts_ms=ts_ms,
                    open=float(
                        next_btc[
                            "open"
                        ]
                    ),
                    high=float(
                        next_btc[
                            "high"
                        ]
                    ),
                    low=float(
                        next_btc[
                            "low"
                        ]
                    ),
                    close=close,
                    volume=volume,
                )
            )

            # Synthetic bar-level trade used only
            # for historical VWAP/volume proxies.
            #
            # "unknown" deliberately prevents
            # fabricated aggressor imbalance.
            engine.add_trade(
                ts_ms=ts_ms,
                price=close,
                size=volume,
                aggressor="unknown",
            )

            # A Binance second beginning at XX:XX:59
            # completes the UTC minute at the next
            # exact minute boundary.
            if (
                ts_ms % 60_000
                == 59_000
            ):
                minute_engine.add_completed_minute(
                    minute_end_ms=(
                        ts_ms + 1000
                    ),
                    close=close,
                )

            last_btc_ts = ts_ms
            last_btc_close = close

            next_btc = (
                cursor.fetchone()
            )

        if (
            last_btc_ts is None
            or last_btc_close is None
        ):
            skipped_no_btc += 1
            continue

        btc_age_ms = (
            observed_ms
            - last_btc_ts
        )

        if (
            btc_age_ms < 1000
            or btc_age_ms
            > max_btc_age_ms
        ):
            skipped_no_btc += 1
            continue

        snapshot = engine.snapshot(
            ts_ms=last_btc_ts,
            spot=last_btc_close,
        )

        row = (
            build_historical_feature_row(
                observation=(
                    observation
                ),
                btc_snapshot=(
                    snapshot
                ),
                minute_ema_snapshot=(
                    minute_engine.snapshot()
                ),
                btc_ts=(
                    last_btc_ts
                ),
                btc_source=(
                    btc_source
                ),
            )
        )

        pending.append(
            row
        )

        if (
            len(pending)
            >= batch_size
        ):
            saved += insert_rows(
                connection,
                pending,
            )

            connection.commit()

            pending.clear()

        if (
            index % 10000
            == 0
        ):
            elapsed = (
                time.perf_counter()
                - started
            )

            print(
                "Historical features | "
                f"{index:,}/"
                f"{len(observations):,} "
                f"observations | "
                f"saved={saved:,} | "
                f"skipped={skipped_no_btc:,} | "
                f"{elapsed:.1f}s"
            )

    if pending:
        saved += insert_rows(
            connection,
            pending,
        )

        connection.commit()

    return {
        "observations": (
            len(
                observations
            )
        ),
        "saved": saved,
        "skipped_no_btc": (
            skipped_no_btc
        ),
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Materialize historical "
            "Kalshi + BTC financial features."
        )
    )

    parser.add_argument(
        "--db",
        required=True,
    )

    parser.add_argument(
        "--series",
        default="KXBTC15M",
    )

    parser.add_argument(
        "--btc-source",
        default=DEFAULT_BTC_SOURCE,
    )

    args = parser.parse_args()

    connection = connect(
        args.db
    )

    try:
        init_db(
            connection
        )

        started = (
            time.perf_counter()
        )

        result = (
            materialize_historical_features(
                connection,
                series_ticker=(
                    args.series
                ),
                btc_source=(
                    args.btc_source
                ),
            )
        )

        elapsed = (
            time.perf_counter()
            - started
        )

        print()
        print(
            "=" * 72
        )

        print(
            "HISTORICAL FINANCIAL "
            "FEATURE MATERIALIZATION"
        )

        print(
            "=" * 72
        )

        print(
            "observations:",
            f"{result['observations']:,}",
        )

        print(
            "saved:",
            f"{result['saved']:,}",
        )

        print(
            "skipped no/stale BTC:",
            f"{result['skipped_no_btc']:,}",
        )

        print(
            "elapsed:",
            f"{elapsed:.2f}s",
        )

    finally:
        connection.close()


if __name__ == "__main__":
    main()
