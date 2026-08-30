from __future__ import annotations

from datetime import datetime


MAX_FEATURE_AGE_MS = 5000
MAX_PATH_GAP_MS = 5000

MICRO_MIN_PRICE = 0.001   # 0.1c
MICRO_MAX_PRICE = 0.10    # 10c

# Absolute target ladder, in cents.
MICRO_TARGET_CENTS = [
    0.2,
    0.3,
    0.4,
    0.5,
    0.6,
    0.8,
    1.0,
    1.5,
    2.0,
    2.5,
    3.0,
    4.0,
    5.0,
    6.0,
    8.0,
    10.0,
    12.0,
    15.0,
    20.0,
    25.0,
    30.0,
    40.0,
    50.0,
]


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


def time_bucket(
    seconds_remaining,
):
    seconds = float(
        seconds_remaining
    )

    if 600 <= seconds <= 900:
        return "10-15m"

    if 540 <= seconds < 600:
        return "9-10m"

    if 480 <= seconds < 540:
        return "8-9m"

    if 420 <= seconds < 480:
        return "7-8m"

    if 360 <= seconds < 420:
        return "6-7m"

    if 300 <= seconds < 360:
        return "5-6m"

    if 240 <= seconds < 300:
        return "4-5m"

    if 180 <= seconds < 240:
        return "3-4m"

    if 120 <= seconds < 180:
        return "2-3m"

    if 60 <= seconds < 120:
        return "1-2m"

    if 0 <= seconds < 60:
        return "<1m"

    return "unknown"


def price_key(
    price,
):
    """
    0.1-cent resolution.

    Example:
        0.1c -> 1
        0.8c -> 8
        5.0c -> 50
    """

    return int(
        round(
            float(price)
            * 1000
        )
    )


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


def record_micro_opportunities(
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

    ticker = str(
        feature[
            "market_ticker"
        ]
    )

    feature_ts = int(
        feature["ts"]
    )

    seconds_remaining = float(
        feature[
            "seconds_remaining"
        ]
    )

    bucket = time_bucket(
        seconds_remaining
    )

    if bucket == "unknown":
        return 0

    inserted = 0

    for side in (
        "yes",
        "no",
    ):
        bid, ask = side_prices(
            feature,
            side,
        )

        if not (
            MICRO_MIN_PRICE
            <= ask
            <= MICRO_MAX_PRICE
        ):
            continue

        key = price_key(
            ask
        )

        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO
            micro_multiplier_opportunities (
                market_ticker,
                side,

                detected_at_ms,
                market_feature_ts,

                entry_price_key,

                entry_bid,
                entry_ask,

                seconds_remaining,
                time_bucket
            )
            VALUES (
                ?, ?,
                ?, ?,
                ?,
                ?, ?,
                ?, ?
            )
            """,
            (
                ticker,
                side,

                int(now_ms),
                feature_ts,

                key,

                bid,
                ask,

                seconds_remaining,
                bucket,
            ),
        )

        if not cursor.rowcount:
            continue

        opportunity_id = int(
            cursor.lastrowid
        )

        target_count = 0

        for target_cents in (
            MICRO_TARGET_CENTS
        ):
            target_price = (
                float(
                    target_cents
                )
                / 100.0
            )

            if (
                target_price
                <= ask
                + 1e-12
            ):
                continue

            connection.execute(
                """
                INSERT INTO
                micro_multiplier_targets (
                    micro_opportunity_id,
                    target_price,
                    multiplier
                )
                VALUES (?, ?, ?)
                """,
                (
                    opportunity_id,
                    target_price,
                    target_price
                    / ask,
                ),
            )

            target_count += 1

        inserted += 1

        print(
            "MICRO OPPORTUNITY | "
            f"{ticker} | "
            f"{side.upper()} | "
            f"ask={ask * 100:.1f}c | "
            f"left={seconds_remaining:.0f}s | "
            f"bucket={bucket} | "
            f"targets={target_count}"
        )

    return inserted


def future_market_path(
    connection,
    *,
    ticker,
    after_ts,
):
    return connection.execute(
        """
        SELECT
            ts,
            yes_bid,
            no_bid

        FROM market_feature_snapshots

        WHERE market_ticker = ?
          AND ts > ?

        ORDER BY ts
        """,
        (
            ticker,
            int(after_ts),
        ),
    ).fetchall()


def market_result_state(
    connection,
    *,
    ticker,
):
    return connection.execute(
        """
        SELECT
            result,
            close_time

        FROM markets

        WHERE ticker = ?
        """,
        (
            ticker,
        ),
    ).fetchone()


def path_is_complete(
    path,
    *,
    entry_ts,
    close_ms,
):
    if (
        close_ms is None
        or not path
    ):
        return False

    rows = [
        row
        for row in path
        if int(
            row["ts"]
        ) <= close_ms
    ]

    if not rows:
        return False

    previous_ts = int(
        entry_ts
    )

    for row in rows:
        row_ts = int(
            row["ts"]
        )

        if (
            row_ts
            - previous_ts
            > MAX_PATH_GAP_MS
        ):
            return False

        previous_ts = row_ts

    return (
        close_ms
        - previous_ts
        <= MAX_PATH_GAP_MS
    )


def label_micro_opportunity(
    connection,
    opportunity,
):
    opportunity_id = int(
        opportunity[
            "micro_opportunity_id"
        ]
    )

    ticker = str(
        opportunity[
            "market_ticker"
        ]
    )

    side = str(
        opportunity[
            "side"
        ]
    ).lower()

    entry_ts = int(
        opportunity[
            "market_feature_ts"
        ]
    )

    pending_targets = (
        connection.execute(
            """
            SELECT *
            FROM micro_multiplier_targets

            WHERE micro_opportunity_id = ?
              AND status = 'PENDING'

            ORDER BY target_price
            """,
            (
                opportunity_id,
            ),
        ).fetchall()
    )

    if not pending_targets:
        connection.execute(
            """
            UPDATE micro_multiplier_opportunities

            SET label_status = 'COMPLETE'

            WHERE micro_opportunity_id = ?
            """,
            (
                opportunity_id,
            ),
        )

        return 0

    path = future_market_path(
        connection,
        ticker=ticker,
        after_ts=entry_ts,
    )

    # Only target hits observed before the first
    # >5-second quote-path gap are trustworthy.
    trustworthy_path = []

    previous_ts = entry_ts

    for row in path:
        row_ts = int(
            row["ts"]
        )

        if (
            row_ts
            - previous_ts
            > MAX_PATH_GAP_MS
        ):
            break

        trustworthy_path.append(
            row
        )

        previous_ts = row_ts

    changed = 0

    for target in pending_targets:
        target_price = float(
            target[
                "target_price"
            ]
        )

        hit_row = None

        for row in trustworthy_path:
            bid = float(
                row[
                    f"{side}_bid"
                ]
            )

            if (
                bid
                + 1e-12
                >= target_price
            ):
                hit_row = row
                break

        if hit_row is None:
            continue

        hit_bid = float(
            hit_row[
                f"{side}_bid"
            ]
        )

        connection.execute(
            """
            UPDATE micro_multiplier_targets

            SET
                status = 'HIT',
                hit_ts_ms = ?,
                hit_bid = ?

            WHERE micro_opportunity_id = ?
              AND target_price = ?
            """,
            (
                int(
                    hit_row["ts"]
                ),
                hit_bid,
                opportunity_id,
                target_price,
            ),
        )

        changed += 1

        print(
            "MICRO HIT | "
            f"{ticker} | "
            f"{side.upper()} | "
            f"{opportunity['entry_ask'] * 100:.1f}c"
            " -> "
            f"{target_price * 100:.1f}c | "
            f"bid={hit_bid * 100:.1f}c"
        )

    remaining = (
        connection.execute(
            """
            SELECT COUNT(*)
            FROM micro_multiplier_targets

            WHERE micro_opportunity_id = ?
              AND status = 'PENDING'
            """,
            (
                opportunity_id,
            ),
        ).fetchone()[0]
    )

    if remaining == 0:
        connection.execute(
            """
            UPDATE micro_multiplier_opportunities

            SET label_status = 'COMPLETE'

            WHERE micro_opportunity_id = ?
            """,
            (
                opportunity_id,
            ),
        )

        return changed

    state = market_result_state(
        connection,
        ticker=ticker,
    )

    if state is None:
        return changed

    result = str(
        state["result"]
        or ""
    ).lower()

    if result not in {
        "yes",
        "no",
    }:
        return changed

    close_ms = iso_to_ms(
        state[
            "close_time"
        ]
    )

    complete = path_is_complete(
        path,
        entry_ts=entry_ts,
        close_ms=close_ms,
    )

    remaining_status = (
        "MISS"
        if complete
        else "INCOMPLETE"
    )

    cursor = connection.execute(
        """
        UPDATE micro_multiplier_targets

        SET status = ?

        WHERE micro_opportunity_id = ?
          AND status = 'PENDING'
        """,
        (
            remaining_status,
            opportunity_id,
        ),
    )

    changed += max(
        0,
        int(
            cursor.rowcount
            or 0
        ),
    )

    connection.execute(
        """
        UPDATE micro_multiplier_opportunities

        SET
            label_status = ?,
            settlement_result = ?,
            path_complete = ?

        WHERE micro_opportunity_id = ?
        """,
        (
            (
                "COMPLETE"
                if complete
                else "INCOMPLETE"
            ),
            result,
            int(
                complete
            ),
            opportunity_id,
        ),
    )

    return changed


def label_micro_opportunities(
    connection,
):
    pending = connection.execute(
        """
        SELECT *
        FROM micro_multiplier_opportunities

        WHERE label_status = 'PENDING'

        ORDER BY detected_at_ms
        """
    ).fetchall()

    changed = 0

    for opportunity in pending:
        changed += (
            label_micro_opportunity(
                connection,
                opportunity,
            )
        )

    return changed
