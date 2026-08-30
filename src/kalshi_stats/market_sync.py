from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)
import time

from .database import (
    connect,
    init_db,
)


BTC_COLUMNS = (
    "spread_bps",
    "return_30s",
    "return_60s",
    "return_180s",
    "return_300s",
    "ema_5",
    "ema_9",
    "ema_21",
    "ema_5_9_bps",
    "ema_9_21_bps",
    "ema_5_slope_bps",
    "ema_9_slope_bps",
    "ema_21_slope_bps",
    "vwap_60s",
    "vwap_300s",
    "vwap_distance_60s_bps",
    "vwap_distance_300s_bps",
    "realized_vol_60s_bps",
    "realized_vol_300s_bps",
    "range_60s_bps",
    "range_300s_bps",
    "trade_volume_60s",
    "trade_volume_300s",
    "relative_volume_60s",
    "trade_imbalance_60s",
    "trade_imbalance_300s",
    "book_imbalance_top10",
)


def _iso_to_ms(
    value: str | None,
) -> int | None:
    if not value:
        return None

    text = str(value)

    try:
        return int(
            datetime.fromisoformat(
                text.replace(
                    "Z",
                    "+00:00",
                )
            ).timestamp()
            * 1000
        )
    except ValueError:
        return None


def _utc_text(
    ts_ms: int,
) -> str:
    return (
        datetime.fromtimestamp(
            ts_ms / 1000.0,
            tz=timezone.utc,
        )
        .replace(
            microsecond=0
        )
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )


def select_current_market_row(
    connection,
    *,
    series_ticker: str,
    now_ms: int,
):
    now_text = _utc_text(
        now_ms
    )

    return connection.execute(
        """
        SELECT *
        FROM markets
        WHERE series_ticker = ?
          AND open_time <= ?
          AND close_time > ?
          AND reference_price IS NOT NULL
        ORDER BY close_time
        LIMIT 1
        """,
        (
            series_ticker,
            now_text,
            now_text,
        ),
    ).fetchone()


def latest_quote(
    connection,
    *,
    market_ticker: str,
):
    return connection.execute(
        """
        SELECT *
        FROM quote_snapshots
        WHERE market_ticker = ?
        ORDER BY collected_at DESC
        LIMIT 1
        """,
        (
            market_ticker,
        ),
    ).fetchone()


def latest_btc_features(
    connection,
    *,
    now_ms: int,
    max_age_ms: int = 3000,
):
    row = connection.execute(
        """
        SELECT *
        FROM btc_feature_snapshots
        WHERE source = 'coinbase_ws'
          AND ts <= ?
        ORDER BY ts DESC
        LIMIT 1
        """,
        (
            int(now_ms),
        ),
    ).fetchone()

    if row is None:
        return None

    age_ms = (
        int(now_ms)
        - int(row["ts"])
    )

    if (
        age_ms < 0
        or age_ms > max_age_ms
    ):
        return None

    return row


def build_market_feature_snapshot(
    *,
    market,
    quote,
    btc,
    now_ms: int,
):
    threshold = float(
        market[
            "reference_price"
        ]
    )

    spot = float(
        btc["spot"]
    )

    close_ms = _iso_to_ms(
        market["close_time"]
    )

    if close_ms is None:
        raise ValueError(
            "Market close_time is invalid"
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
        btc[
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

    quote_ms = _iso_to_ms(
        quote[
            "collected_at"
        ]
    )

    quote_age_ms = None

    if quote_ms is not None:
        quote_age_ms = max(
            0,
            int(now_ms)
            - quote_ms,
        )

    btc_ts = int(
        btc["ts"]
    )

    row = {
        "market_ticker": str(
            market["ticker"]
        ),

        # One synchronized row per UTC second.
        "ts": (
            int(now_ms)
            // 1000
            * 1000
        ),

        "quote_collected_at": (
            quote[
                "collected_at"
            ]
        ),

        "quote_age_ms": (
            quote_age_ms
        ),

        "btc_ts": btc_ts,

        "btc_age_ms": max(
            0,
            int(now_ms)
            - btc_ts,
        ),

        "threshold": threshold,

        # Verified KXBTC15M semantics:
        # floor_strike / reference_price,
        # greater-or-equal settlement condition.
        "threshold_rule": (
            "greater_or_equal"
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

        "seconds_remaining": max(
            0.0,
            (
                close_ms
                - int(now_ms)
            )
            / 1000.0,
        ),

        "yes_bid": float(
            quote["yes_bid"]
        ),

        "yes_ask": float(
            quote["yes_ask"]
        ),

        "no_bid": float(
            quote["no_bid"]
        ),

        "no_ask": float(
            quote["no_ask"]
        ),
    }

    for column in BTC_COLUMNS:
        output_name = (
            "btc_spread_bps"
            if column
            == "spread_bps"
            else column
        )

        row[
            output_name
        ] = btc[column]

    return row


INSERT_COLUMNS = (
    "market_ticker",
    "ts",
    "quote_collected_at",
    "quote_age_ms",
    "btc_ts",
    "btc_age_ms",
    "threshold",
    "threshold_rule",
    "spot",
    "threshold_distance_dollars",
    "threshold_distance_pct",
    "threshold_distance_bps",
    "threshold_distance_vol60",
    "seconds_remaining",
    "yes_bid",
    "yes_ask",
    "no_bid",
    "no_ask",
    "btc_spread_bps",
    "return_30s",
    "return_60s",
    "return_180s",
    "return_300s",
    "ema_5",
    "ema_9",
    "ema_21",
    "ema_5_9_bps",
    "ema_9_21_bps",
    "ema_5_slope_bps",
    "ema_9_slope_bps",
    "ema_21_slope_bps",
    "vwap_60s",
    "vwap_300s",
    "vwap_distance_60s_bps",
    "vwap_distance_300s_bps",
    "realized_vol_60s_bps",
    "realized_vol_300s_bps",
    "range_60s_bps",
    "range_300s_bps",
    "trade_volume_60s",
    "trade_volume_300s",
    "relative_volume_60s",
    "trade_imbalance_60s",
    "trade_imbalance_300s",
    "book_imbalance_top10",
)


def insert_market_feature_snapshot(
    connection,
    row,
) -> None:
    placeholders = ", ".join(
        "?"
        for _ in INSERT_COLUMNS
    )

    connection.execute(
        f"""
        INSERT OR REPLACE INTO
        market_feature_snapshots (
            {", ".join(INSERT_COLUMNS)}
        )
        VALUES (
            {placeholders}
        )
        """,
        [
            row.get(
                column
            )
            for column
            in INSERT_COLUMNS
        ],
    )


def sync_once(
    connection,
    *,
    series_ticker: str = "KXBTC15M",
    now_ms: int | None = None,
    max_btc_age_ms: int = 3000,
):
    now_ms = (
        int(
            time.time()
            * 1000
        )
        if now_ms is None
        else int(
            now_ms
        )
    )

    market = (
        select_current_market_row(
            connection,
            series_ticker=(
                series_ticker
            ),
            now_ms=now_ms,
        )
    )

    if market is None:
        return {
            "status": "NO_MARKET"
        }

    ticker = str(
        market["ticker"]
    )

    quote = latest_quote(
        connection,
        market_ticker=ticker,
    )

    if quote is None:
        return {
            "status": "NO_KALSHI_QUOTE",
            "ticker": ticker,
        }

    btc = latest_btc_features(
        connection,
        now_ms=now_ms,
        max_age_ms=(
            max_btc_age_ms
        ),
    )

    if btc is None:
        return {
            "status": "BTC_STALE",
            "ticker": ticker,
        }

    row = (
        build_market_feature_snapshot(
            market=market,
            quote=quote,
            btc=btc,
            now_ms=now_ms,
        )
    )

    insert_market_feature_snapshot(
        connection,
        row,
    )

    connection.commit()

    return {
        "status": "SAVED",
        "ticker": ticker,
        "row": row,
    }


def run_loop(
    *,
    db_path: str,
    series_ticker: str = "KXBTC15M",
):
    connection = connect(
        db_path
    )

    init_db(
        connection
    )

    saved = 0
    last_second = None
    last_log = 0.0
    last_wait_status = None

    try:
        while True:
            now_ms = int(
                time.time()
                * 1000
            )

            second = (
                now_ms // 1000
            )

            if second == last_second:
                time.sleep(
                    0.05
                )
                continue

            last_second = second

            result = sync_once(
                connection,
                series_ticker=(
                    series_ticker
                ),
                now_ms=now_ms,
            )

            status = result[
                "status"
            ]

            if status == "SAVED":
                saved += 1

                row = result[
                    "row"
                ]

                now = (
                    time.monotonic()
                )

                if (
                    now
                    - last_log
                    >= 5.0
                ):
                    print(
                        "SYNC live | "
                        f"{result['ticker']} | "
                        f"BTC ${row['spot']:,.2f} | "
                        f"target "
                        f"${row['threshold']:,.2f} | "
                        f"distance "
                        f"{row['threshold_distance_dollars']:+.2f} | "
                        f"{row['threshold_distance_bps']:+.2f}bps | "
                        f"vol="
                        f"{row['threshold_distance_vol60']} | "
                        f"left="
                        f"{row['seconds_remaining']:.0f}s | "
                        f"YES ask="
                        f"{row['yes_ask'] * 100:.1f}c | "
                        f"saved={saved}"
                    )

                    last_log = now

                last_wait_status = None

            elif (
                status
                != last_wait_status
            ):
                print(
                    "SYNC waiting | "
                    f"{status}"
                )

                last_wait_status = (
                    status
                )

    finally:
        connection.close()


def main():
    import argparse

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--db",
        required=True,
    )

    parser.add_argument(
        "--series",
        default="KXBTC15M",
    )

    args = parser.parse_args()

    try:
        run_loop(
            db_path=args.db,
            series_ticker=(
                args.series
            ),
        )

    except KeyboardInterrupt:
        print(
            "\nMarket feature synchronizer stopped."
        )


if __name__ == "__main__":
    main()
