from __future__ import annotations

import argparse
import time
from datetime import datetime

from .account_sync import (
    canonical_outcome_side,
)
from .database import (
    connect,
    init_db,
)


STRATEGY_ID = "60-69c_5-10m_tp15_sl5"

PRICE_LOW = 0.60
PRICE_HIGH = 0.69

TIME_LOW = 300
TIME_HIGH = 599

MAX_FEATURE_AGE_MS = 5000
MAX_BRTI_AGE_MS = 5000


def iso_to_ms(value):
    if not value:
        return None

    return int(
        datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00",
            )
        ).timestamp()
        * 1000
    )


def aligned(
    value,
    side,
):
    if value is None:
        return None

    number = float(value)

    return (
        number
        if side == "yes"
        else -number
    )


def side_prices(
    feature,
    side,
):
    return (
        float(
            feature[
                f"{side}_bid"
            ]
        ),
        float(
            feature[
                f"{side}_ask"
            ]
        ),
    )


def qualifies(
    *,
    ask,
    seconds_remaining,
):
    return (
        PRICE_LOW
        <= float(ask)
        <= PRICE_HIGH
        and TIME_LOW
        <= float(
            seconds_remaining
        )
        <= TIME_HIGH
    )


def qualifying_sides(
    feature,
):
    output = []

    for side in (
        "yes",
        "no",
    ):
        _, ask = side_prices(
            feature,
            side,
        )

        if qualifies(
            ask=ask,
            seconds_remaining=(
                feature[
                    "seconds_remaining"
                ]
            ),
        ):
            output.append(
                side
            )

    return output


def latest_market_feature(
    connection,
    *,
    now_ms,
):
    row = connection.execute(
        """
        SELECT *
        FROM market_feature_snapshots
        WHERE ts <= ?
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
        or age_ms
        > MAX_FEATURE_AGE_MS
    ):
        return None

    return row


def market_feature_at(
    connection,
    *,
    ticker,
    target_ms,
):
    row = connection.execute(
        """
        SELECT *
        FROM market_feature_snapshots
        WHERE market_ticker = ?
          AND ts <= ?
        ORDER BY ts DESC
        LIMIT 1
        """,
        (
            ticker,
            int(target_ms),
        ),
    ).fetchone()

    if row is None:
        return None

    age_ms = (
        int(target_ms)
        - int(row["ts"])
    )

    if (
        age_ms < 0
        or age_ms
        > MAX_FEATURE_AGE_MS
    ):
        return None

    return row


def brti_timestamp_scale(
    connection,
):
    row = connection.execute(
        """
        SELECT MAX(ts) AS ts
        FROM brti_snapshots
        """
    ).fetchone()

    if (
        row is None
        or row["ts"] is None
    ):
        return 1

    value = int(
        row["ts"]
    )

    # Unix seconds are currently ~1e9;
    # Unix milliseconds ~1e12.
    return (
        1
        if value
        >= 100_000_000_000
        else 1000
    )


def brti_at(
    connection,
    *,
    target_ms,
):
    scale = (
        brti_timestamp_scale(
            connection
        )
    )

    cutoff = (
        int(target_ms)
        if scale == 1
        else int(
            target_ms
            // 1000
        )
    )

    row = connection.execute(
        """
        SELECT *
        FROM brti_snapshots
        WHERE index_id = 'BRTI'
          AND ts <= ?
        ORDER BY ts DESC
        LIMIT 1
        """,
        (
            cutoff,
        ),
    ).fetchone()

    if row is None:
        return None

    ts_ms = (
        int(row["ts"])
        * scale
    )

    age_ms = (
        int(target_ms)
        - ts_ms
    )

    if (
        age_ms < 0
        or age_ms
        > MAX_BRTI_AGE_MS
    ):
        return None

    return {
        "row": row,
        "ts_ms": ts_ms,
        "age_ms": age_ms,
    }


def feature_values(
    *,
    feature,
    brti,
    side,
):
    threshold = float(
        feature["threshold"]
    )

    result = {
        "side_threshold_distance_bps":
            aligned(
                feature[
                    "threshold_distance_bps"
                ],
                side,
            ),

        "return_60s_aligned":
            aligned(
                feature[
                    "return_60s"
                ],
                side,
            ),

        "return_300s_aligned":
            aligned(
                feature[
                    "return_300s"
                ],
                side,
            ),

        "vwap_distance_300s_bps_aligned":
            aligned(
                feature[
                    "vwap_distance_300s_bps"
                ],
                side,
            ),

        "realized_vol_60s_bps":
            feature[
                "realized_vol_60s_bps"
            ],

        "trade_imbalance_60s_aligned":
            aligned(
                feature[
                    "trade_imbalance_60s"
                ],
                side,
            ),

        "trade_imbalance_300s_aligned":
            aligned(
                feature[
                    "trade_imbalance_300s"
                ],
                side,
            ),

        "book_imbalance_top10_aligned":
            aligned(
                feature[
                    "book_imbalance_top10"
                ],
                side,
            ),

        "btc_spread_bps":
            feature[
                "btc_spread_bps"
            ],

        "brti_ts": None,
        "brti_age_ms": None,

        "brti_value": None,
        "brti_avg_60s_value": None,
        "brti_final_60s_avg_15m": None,

        "brti_side_distance_dollars": None,
    }

    if brti is None:
        return result

    row = brti["row"]

    value = float(
        row["value"]
    )

    result.update(
        {
            "brti_ts":
                brti[
                    "ts_ms"
                ],

            "brti_age_ms":
                brti[
                    "age_ms"
                ],

            "brti_value":
                value,

            "brti_avg_60s_value":
                row[
                    "avg_60s_value"
                ],

            "brti_final_60s_avg_15m":
                row[
                    "final_60s_avg_15m"
                ],

            "brti_side_distance_dollars":
                aligned(
                    value
                    - threshold,
                    side,
                ),
        }
    )

    return result


def record_opportunities(
    connection,
    *,
    now_ms,
):
    feature = latest_market_feature(
        connection,
        now_ms=now_ms,
    )

    if feature is None:
        return 0

    sides = qualifying_sides(
        feature
    )

    if not sides:
        return 0

    brti = brti_at(
        connection,
        target_ms=int(
            feature["ts"]
        ),
    )

    inserted = 0

    for side in sides:
        bid, ask = side_prices(
            feature,
            side,
        )

        values = feature_values(
            feature=feature,
            brti=brti,
            side=side,
        )

        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO
            prospective_opportunities (
                strategy_id,
                market_ticker,
                side,

                detected_at_ms,
                market_feature_ts,

                entry_bid,
                entry_ask,
                seconds_remaining,

                threshold,
                spot,

                side_threshold_distance_bps,

                return_60s_aligned,
                return_300s_aligned,

                vwap_distance_300s_bps_aligned,
                realized_vol_60s_bps,

                trade_imbalance_60s_aligned,
                trade_imbalance_300s_aligned,
                book_imbalance_top10_aligned,

                btc_spread_bps,

                brti_ts,
                brti_age_ms,
                brti_value,
                brti_avg_60s_value,
                brti_final_60s_avg_15m,
                brti_side_distance_dollars
            )
            VALUES (
                ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?,
                ?,
                ?, ?,
                ?, ?,
                ?, ?, ?,
                ?,
                ?, ?, ?, ?, ?, ?
            )
            """,
            (
                STRATEGY_ID,
                feature[
                    "market_ticker"
                ],
                side,

                int(now_ms),
                int(
                    feature["ts"]
                ),

                bid,
                ask,
                float(
                    feature[
                        "seconds_remaining"
                    ]
                ),

                float(
                    feature[
                        "threshold"
                    ]
                ),

                float(
                    feature[
                        "spot"
                    ]
                ),

                values[
                    "side_threshold_distance_bps"
                ],

                values[
                    "return_60s_aligned"
                ],

                values[
                    "return_300s_aligned"
                ],

                values[
                    "vwap_distance_300s_bps_aligned"
                ],

                values[
                    "realized_vol_60s_bps"
                ],

                values[
                    "trade_imbalance_60s_aligned"
                ],

                values[
                    "trade_imbalance_300s_aligned"
                ],

                values[
                    "book_imbalance_top10_aligned"
                ],

                values[
                    "btc_spread_bps"
                ],

                values[
                    "brti_ts"
                ],

                values[
                    "brti_age_ms"
                ],

                values[
                    "brti_value"
                ],

                values[
                    "brti_avg_60s_value"
                ],

                values[
                    "brti_final_60s_avg_15m"
                ],

                values[
                    "brti_side_distance_dollars"
                ],
            ),
        )

        if cursor.rowcount:
            inserted += 1

            print(
                "OPPORTUNITY | "
                f"{feature['market_ticker']} | "
                f"{side.upper()} "
                f"ask={ask * 100:.1f}c | "
                f"left="
                f"{feature['seconds_remaining']:.0f}s"
            )

    return inserted


def unprocessed_fills(
    connection,
):
    return connection.execute(
        """
        SELECT fills.*
        FROM account_fills AS fills

        LEFT JOIN fill_feature_snapshots
          ON fill_feature_snapshots.fill_id
           = fills.fill_id

        WHERE fill_feature_snapshots.fill_id
              IS NULL

        ORDER BY
            fills.created_time,
            fills.fill_id
        """
    ).fetchall()


def record_fill_snapshot(
    connection,
    *,
    fill,
    captured_at_ms,
):
    fill_ms = iso_to_ms(
        fill["created_time"]
    )

    if fill_ms is None:
        return False

    side = canonical_outcome_side(
        fill
    )

    price = (
        float(
            fill["yes_price"]
        )
        if side == "yes"
        else float(
            fill["no_price"]
        )
    )

    feature = market_feature_at(
        connection,
        ticker=str(
            fill[
                "market_ticker"
            ]
        ),
        target_ms=fill_ms,
    )

    if feature is None:
        connection.execute(
            """
            INSERT OR IGNORE INTO
            fill_feature_snapshots (
                fill_id,
                market_ticker,
                fill_created_time,
                fill_ts_ms,
                outcome_side,
                count,
                fill_price,
                captured_at_ms,
                base_setup_qualified
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                fill["fill_id"],
                fill[
                    "market_ticker"
                ],
                fill[
                    "created_time"
                ],
                fill_ms,
                side,
                float(
                    fill["count"]
                ),
                price,
                int(
                    captured_at_ms
                ),
            ),
        )

        return True

    bid, ask = side_prices(
        feature,
        side,
    )

    qualified = qualifies(
        ask=ask,
        seconds_remaining=(
            feature[
                "seconds_remaining"
            ]
        ),
    )

    brti = brti_at(
        connection,
        target_ms=fill_ms,
    )

    values = feature_values(
        feature=feature,
        brti=brti,
        side=side,
    )

    connection.execute(
        """
        INSERT OR IGNORE INTO
        fill_feature_snapshots (
            fill_id,
            market_ticker,

            fill_created_time,
            fill_ts_ms,

            outcome_side,

            count,
            fill_price,

            captured_at_ms,

            market_feature_ts,
            feature_age_ms,

            seconds_remaining,

            side_bid,
            side_ask,

            base_setup_qualified,

            threshold,
            spot,

            side_threshold_distance_bps,

            return_60s_aligned,
            return_300s_aligned,

            vwap_distance_300s_bps_aligned,
            realized_vol_60s_bps,

            trade_imbalance_60s_aligned,
            trade_imbalance_300s_aligned,
            book_imbalance_top10_aligned,

            btc_spread_bps,

            brti_ts,
            brti_age_ms,

            brti_value,
            brti_avg_60s_value,
            brti_final_60s_avg_15m,

            brti_side_distance_dollars
        )
        VALUES (
            ?, ?,
            ?, ?,
            ?,
            ?, ?,
            ?,
            ?, ?,
            ?,
            ?, ?,
            ?,
            ?, ?,
            ?,
            ?, ?,
            ?, ?,
            ?, ?, ?,
            ?,
            ?, ?,
            ?, ?, ?,
            ?
        )
        """,
        (
            fill[
                "fill_id"
            ],

            fill[
                "market_ticker"
            ],

            fill[
                "created_time"
            ],

            fill_ms,

            side,

            float(
                fill["count"]
            ),

            price,

            int(
                captured_at_ms
            ),

            int(
                feature["ts"]
            ),

            fill_ms
            - int(
                feature["ts"]
            ),

            float(
                feature[
                    "seconds_remaining"
                ]
            ),

            bid,
            ask,

            int(
                qualified
            ),

            float(
                feature[
                    "threshold"
                ]
            ),

            float(
                feature[
                    "spot"
                ]
            ),

            values[
                "side_threshold_distance_bps"
            ],

            values[
                "return_60s_aligned"
            ],

            values[
                "return_300s_aligned"
            ],

            values[
                "vwap_distance_300s_bps_aligned"
            ],

            values[
                "realized_vol_60s_bps"
            ],

            values[
                "trade_imbalance_60s_aligned"
            ],

            values[
                "trade_imbalance_300s_aligned"
            ],

            values[
                "book_imbalance_top10_aligned"
            ],

            values[
                "btc_spread_bps"
            ],

            values[
                "brti_ts"
            ],

            values[
                "brti_age_ms"
            ],

            values[
                "brti_value"
            ],

            values[
                "brti_avg_60s_value"
            ],

            values[
                "brti_final_60s_avg_15m"
            ],

            values[
                "brti_side_distance_dollars"
            ],
        ),
    )

    return True


def record_fill_snapshots(
    connection,
    *,
    now_ms,
):
    fills = unprocessed_fills(
        connection
    )

    saved = 0

    for fill in fills:
        if record_fill_snapshot(
            connection,
            fill=fill,
            captured_at_ms=now_ms,
        ):
            saved += 1

    return saved


def run_once(
    connection,
    *,
    now_ms=None,
):
    now_ms = (
        int(
            time.time()
            * 1000
        )
        if now_ms is None
        else int(now_ms)
    )

    opportunities = (
        record_opportunities(
            connection,
            now_ms=now_ms,
        )
    )

    fills = record_fill_snapshots(
        connection,
        now_ms=now_ms,
    )

    connection.commit()

    return {
        "opportunities": (
            opportunities
        ),
        "fills": fills,
    }


def run_loop(
    *,
    db_path,
):
    connection = connect(
        db_path
    )

    init_db(
        connection
    )

    total_opportunities = 0
    total_fills = 0
    last_log = 0.0

    try:
        while True:
            result = run_once(
                connection
            )

            total_opportunities += (
                result[
                    "opportunities"
                ]
            )

            total_fills += (
                result["fills"]
            )

            now = time.monotonic()

            if (
                result["fills"]
                or result[
                    "opportunities"
                ]
                or now
                - last_log
                >= 10
            ):
                pending = (
                    connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM prospective_opportunities
                        WHERE label_status = 'PENDING'
                        """
                    ).fetchone()[0]
                )

                print(
                    "PROSPECTIVE live | "
                    f"new_opps="
                    f"{result['opportunities']} | "
                    f"new_fills="
                    f"{result['fills']} | "
                    f"pending="
                    f"{pending} | "
                    f"session_opps="
                    f"{total_opportunities} | "
                    f"session_fills="
                    f"{total_fills}"
                )

                last_log = now

            time.sleep(
                1.0
            )

    finally:
        connection.close()


def main():
    parser = (
        argparse.ArgumentParser()
    )

    parser.add_argument(
        "--db",
        required=True,
    )

    parser.add_argument(
        "--once",
        action="store_true",
    )

    args = parser.parse_args()

    connection = None

    try:
        if args.once:
            connection = connect(
                args.db
            )

            init_db(
                connection
            )

            print(
                run_once(
                    connection
                )
            )

        else:
            run_loop(
                db_path=args.db
            )

    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    main()
