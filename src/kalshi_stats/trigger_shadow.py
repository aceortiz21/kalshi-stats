from __future__ import annotations

import argparse
import time

from datetime import datetime
from decimal import (
    Decimal,
    ROUND_DOWN,
)

from .database import (
    connect,
    init_db,
)


STRATEGY_ID = (
    "60-69c_5-10m_tp25_sl5"
)

PRICE_LOW = 0.60
PRICE_HIGH = 0.69

TIME_LOW = 300
TIME_HIGH = 599

TP_DELTA = 0.25
SL_DELTA = 0.05

WINDOW_MAX_GAP_MS = 2500
PATH_MAX_GAP_MS = 5000

SHADOW_NOTIONAL_CAP = (
    Decimal("0.01")
)


PROFILES = [
    {
        "profile_id": "RAW",
        "window_seconds": 0,
        "minimum_occupancy": 1.0,
        "requires_continuous": True,
    },
    {
        "profile_id": "STABLE_3S",
        "window_seconds": 3,
        "minimum_occupancy": 1.0,
        "requires_continuous": True,
    },
    {
        "profile_id": "STABLE_5S",
        "window_seconds": 5,
        "minimum_occupancy": 1.0,
        "requires_continuous": True,
    },
    {
        "profile_id": "STABLE_10S",
        "window_seconds": 10,
        "minimum_occupancy": 1.0,
        "requires_continuous": True,
    },
    {
        "profile_id": "STABLE_15S",
        "window_seconds": 15,
        "minimum_occupancy": 1.0,
        "requires_continuous": True,
    },
    {
        "profile_id": "OCC_4_OF_5S",
        "window_seconds": 5,
        "minimum_occupancy": 0.80,
        "requires_continuous": False,
    },
    {
        "profile_id": "OCC_8_OF_10S",
        "window_seconds": 10,
        "minimum_occupancy": 0.80,
        "requires_continuous": False,
    },
]


PROFILE_MAP = {
    profile["profile_id"]:
        profile
    for profile
    in PROFILES
}


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


def row_qualifies(
    row,
    side,
):
    ask = float(
        row[
            f"{side}_ask"
        ]
    )

    seconds = float(
        row[
            "seconds_remaining"
        ]
    )

    return (
        PRICE_LOW
        <= ask
        <= PRICE_HIGH
        and TIME_LOW
        <= seconds
        <= TIME_HIGH
    )


def shadow_count_for_price(
    entry_price,
    *,
    notional_cap=SHADOW_NOTIONAL_CAP,
):
    """
    Largest 0.01-contract quantity whose gross
    entry notional does not exceed the configured cap.
    """

    price = Decimal(
        str(
            entry_price
        )
    )

    if price <= 0:
        return (
            "0.00",
            0.0,
        )

    raw_count = (
        Decimal(
            notional_cap
        )
        / price
    )

    hundredths = (
        raw_count
        * Decimal("100")
    ).to_integral_value(
        rounding=ROUND_DOWN
    )

    count = (
        hundredths
        / Decimal("100")
    )

    if count < Decimal("0.01"):
        return (
            "0.00",
            0.0,
        )

    notional = (
        count
        * price
    )

    return (
        f"{count:.2f}",
        float(
            notional
        ),
    )


def ensure_confirmation_rows(
    connection,
):
    opportunities = (
        connection.execute(
            """
            SELECT *
            FROM prospective_opportunities

            WHERE strategy_id = ?

            ORDER BY
                market_feature_ts,
                opportunity_id
            """,
            (
                STRATEGY_ID,
            ),
        ).fetchall()
    )

    inserted = 0

    for opportunity in opportunities:
        for profile in PROFILES:
            profile_id = (
                profile[
                    "profile_id"
                ]
            )

            is_raw = (
                profile_id
                == "RAW"
            )

            entry_ask = (
                float(
                    opportunity[
                        "entry_ask"
                    ]
                )
                if is_raw
                else None
            )

            entry_bid = (
                float(
                    opportunity[
                        "entry_bid"
                    ]
                )
                if is_raw
                else None
            )

            cursor = (
                connection.execute(
                    """
                    INSERT OR IGNORE INTO
                    main_trigger_confirmations (
                        opportunity_id,
                        strategy_id,

                        market_ticker,
                        side,
                        episode_number,

                        profile_id,

                        raw_start_ms,

                        window_seconds,
                        minimum_occupancy,
                        requires_continuous,

                        status,

                        confirmed_at_ms,
                        confirm_feature_ts,

                        entry_bid,
                        entry_ask,
                        seconds_remaining,

                        qualified_samples,
                        total_samples,

                        tp_price,
                        sl_price,

                        label_status
                    )
                    VALUES (
                        ?, ?,

                        ?, ?, ?,

                        ?,

                        ?,

                        ?, ?, ?,

                        ?,

                        ?, ?,

                        ?, ?, ?,

                        ?, ?,

                        ?, ?,

                        ?
                    )
                    """,
                    (
                        opportunity[
                            "opportunity_id"
                        ],

                        STRATEGY_ID,

                        opportunity[
                            "market_ticker"
                        ],

                        opportunity[
                            "side"
                        ],

                        opportunity[
                            "episode_number"
                        ],

                        profile_id,

                        opportunity[
                            "episode_start_ms"
                        ],

                        profile[
                            "window_seconds"
                        ],

                        profile[
                            "minimum_occupancy"
                        ],

                        int(
                            profile[
                                "requires_continuous"
                            ]
                        ),

                        (
                            "CONFIRMED"
                            if is_raw
                            else "WAITING"
                        ),

                        (
                            opportunity[
                                "market_feature_ts"
                            ]
                            if is_raw
                            else None
                        ),

                        (
                            opportunity[
                                "market_feature_ts"
                            ]
                            if is_raw
                            else None
                        ),

                        entry_bid,
                        entry_ask,

                        (
                            opportunity[
                                "seconds_remaining"
                            ]
                            if is_raw
                            else None
                        ),

                        (
                            1
                            if is_raw
                            else None
                        ),

                        (
                            1
                            if is_raw
                            else None
                        ),

                        (
                            entry_ask
                            + TP_DELTA
                            if is_raw
                            else None
                        ),

                        (
                            entry_ask
                            - SL_DELTA
                            if is_raw
                            else None
                        ),

                        (
                            "PENDING"
                            if is_raw
                            else "WAITING"
                        ),
                    ),
                )
            )

            inserted += max(
                0,
                int(
                    cursor.rowcount
                    or 0
                ),
            )

    return inserted


def load_episode_path(
    connection,
    *,
    ticker,
    start_ms,
    end_ms,
):
    return connection.execute(
        """
        SELECT
            ts,
            seconds_remaining,
            yes_bid,
            yes_ask,
            no_bid,
            no_ask

        FROM market_feature_snapshots

        WHERE market_ticker = ?
          AND ts >= ?
          AND ts <= ?

        ORDER BY ts
        """,
        (
            str(
                ticker
            ),
            int(
                start_ms
            ),
            int(
                end_ms
            ),
        ),
    ).fetchall()


def window_has_coverage(
    rows,
    *,
    window_start_ms,
):
    if not rows:
        return False

    first_ts = int(
        rows[0][
            "ts"
        ]
    )

    # Roughly 1 Hz data. Allow a little timing jitter,
    # but do not pretend a window existed when its
    # beginning was not observed.
    if (
        first_ts
        > int(
            window_start_ms
        )
        + 1500
    ):
        return False

    previous_ts = first_ts

    for row in rows[1:]:
        row_ts = int(
            row[
                "ts"
            ]
        )

        if (
            row_ts
            - previous_ts
            > WINDOW_MAX_GAP_MS
        ):
            return False

        previous_ts = row_ts

    return True


def find_confirmation(
    rows,
    *,
    side,
    profile,
    raw_start_ms,
):
    duration = int(
        profile[
            "window_seconds"
        ]
    )

    if duration <= 0:
        return None

    for endpoint_index, endpoint in enumerate(
        rows
    ):
        endpoint_ts = int(
            endpoint[
                "ts"
            ]
        )

        if not row_qualifies(
            endpoint,
            side,
        ):
            continue

        window_start = (
            endpoint_ts
            - duration
            * 1000
        )

        if (
            window_start
            < int(
                raw_start_ms
            )
        ):
            continue

        window = [
            row
            for row in rows[
                : endpoint_index + 1
            ]
            if int(
                row[
                    "ts"
                ]
            )
            >= window_start
        ]

        if not window_has_coverage(
            window,
            window_start_ms=(
                window_start
            ),
        ):
            continue

        qualified = [
            row_qualifies(
                row,
                side,
            )
            for row
            in window
        ]

        total_samples = len(
            qualified
        )

        qualified_samples = sum(
            int(
                value
            )
            for value
            in qualified
        )

        if not total_samples:
            continue

        occupancy = (
            qualified_samples
            / total_samples
        )

        if (
            profile[
                "requires_continuous"
            ]
        ):
            passed = all(
                qualified
            )

        else:
            passed = (
                occupancy
                >= float(
                    profile[
                        "minimum_occupancy"
                    ]
                )
            )

        # Occupancy confirmation must still be
        # executable at a qualifying price NOW.
        if not passed:
            continue

        return {
            "row":
                endpoint,

            "qualified_samples":
                qualified_samples,

            "total_samples":
                total_samples,
        }

    return None


def service_waiting_confirmations(
    connection,
    *,
    now_ms,
):
    waiting = connection.execute(
        """
        SELECT
            confirmations.*,

            opportunities.episode_end_ms,

            markets.result
                AS market_result,

            markets.close_time

        FROM main_trigger_confirmations
            AS confirmations

        JOIN prospective_opportunities
            AS opportunities

          ON opportunities.opportunity_id
             =
             confirmations.opportunity_id

        LEFT JOIN markets
            AS markets

          ON markets.ticker
             =
             confirmations.market_ticker

        WHERE confirmations.status
              = 'WAITING'

        ORDER BY
            confirmations.raw_start_ms,
            confirmations.confirmation_id
        """
    ).fetchall()

    changed = 0

    for confirmation in waiting:
        profile = PROFILE_MAP[
            confirmation[
                "profile_id"
            ]
        ]

        end_ms = int(
            now_ms
        )

        episode_end = (
            confirmation[
                "episode_end_ms"
            ]
        )

        if episode_end is not None:
            end_ms = min(
                end_ms,
                int(
                    episode_end
                ),
            )

        market_result = str(
            confirmation[
                "market_result"
            ]
            or ""
        ).lower()

        close_ms = iso_to_ms(
            confirmation[
                "close_time"
            ]
        )

        if (
            market_result
            in {
                "yes",
                "no",
            }
            and close_ms is not None
        ):
            end_ms = min(
                end_ms,
                close_ms,
            )

        rows = load_episode_path(
            connection,
            ticker=(
                confirmation[
                    "market_ticker"
                ]
            ),
            start_ms=(
                confirmation[
                    "raw_start_ms"
                ]
            ),
            end_ms=end_ms,
        )

        found = find_confirmation(
            rows,
            side=str(
                confirmation[
                    "side"
                ]
            ).lower(),
            profile=profile,
            raw_start_ms=(
                confirmation[
                    "raw_start_ms"
                ]
            ),
        )

        if found is not None:
            row = found[
                "row"
            ]

            side = str(
                confirmation[
                    "side"
                ]
            ).lower()

            entry_ask = float(
                row[
                    f"{side}_ask"
                ]
            )

            entry_bid = float(
                row[
                    f"{side}_bid"
                ]
            )

            connection.execute(
                """
                UPDATE main_trigger_confirmations

                SET
                    status = 'CONFIRMED',

                    confirmed_at_ms = ?,
                    confirm_feature_ts = ?,

                    entry_bid = ?,
                    entry_ask = ?,
                    seconds_remaining = ?,

                    qualified_samples = ?,
                    total_samples = ?,

                    tp_price = ?,
                    sl_price = ?,

                    label_status = 'PENDING'

                WHERE confirmation_id = ?
                """,
                (
                    int(
                        row[
                            "ts"
                        ]
                    ),

                    int(
                        row[
                            "ts"
                        ]
                    ),

                    entry_bid,
                    entry_ask,

                    float(
                        row[
                            "seconds_remaining"
                        ]
                    ),

                    found[
                        "qualified_samples"
                    ],

                    found[
                        "total_samples"
                    ],

                    entry_ask
                    + TP_DELTA,

                    entry_ask
                    - SL_DELTA,

                    confirmation[
                        "confirmation_id"
                    ],
                ),
            )

            print(
                "TRIGGER CONFIRMED | "
                f"{confirmation['market_ticker']} | "
                f"{str(confirmation['side']).upper()} | "
                f"{confirmation['profile_id']} | "
                f"entry={entry_ask * 100:.1f}c"
            )

            changed += 1
            continue

        ended = (
            episode_end
            is not None
            or market_result
            in {
                "yes",
                "no",
            }
        )

        if ended:
            connection.execute(
                """
                UPDATE main_trigger_confirmations

                SET
                    status = 'EXPIRED',
                    label_status =
                        'NOT_APPLICABLE'

                WHERE confirmation_id = ?
                """,
                (
                    confirmation[
                        "confirmation_id"
                    ],
                ),
            )

            changed += 1

    return changed


def future_path(
    connection,
    *,
    ticker,
    after_ms,
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
            str(
                ticker
            ),
            int(
                after_ms
            ),
        ),
    ).fetchall()


def trustworthy_prefix(
    rows,
    *,
    start_ms,
):
    output = []

    previous_ts = int(
        start_ms
    )

    for row in rows:
        row_ts = int(
            row[
                "ts"
            ]
        )

        if (
            row_ts
            - previous_ts
            > PATH_MAX_GAP_MS
        ):
            break

        output.append(
            row
        )

        previous_ts = row_ts

    return output


def path_complete_to_close(
    rows,
    *,
    start_ms,
    close_ms,
):
    if (
        close_ms is None
        or not rows
    ):
        return False

    previous_ts = int(
        start_ms
    )

    saw = False

    for row in rows:
        row_ts = int(
            row[
                "ts"
            ]
        )

        if row_ts > close_ms:
            break

        if (
            row_ts
            - previous_ts
            > PATH_MAX_GAP_MS
        ):
            return False

        previous_ts = row_ts
        saw = True

    if not saw:
        return False

    return (
        close_ms
        - previous_ts
        <= PATH_MAX_GAP_MS
    )


def label_confirmed_triggers(
    connection,
):
    confirmations = (
        connection.execute(
            """
            SELECT
                confirmations.*,

                markets.result
                    AS market_result,

                markets.close_time

            FROM main_trigger_confirmations
                AS confirmations

            LEFT JOIN markets
                AS markets

              ON markets.ticker
                 =
                 confirmations.market_ticker

            WHERE
                confirmations.status
                    = 'CONFIRMED'

                AND confirmations.label_status
                    = 'PENDING'

            ORDER BY
                confirmations.confirmed_at_ms
            """
        ).fetchall()
    )

    changed = 0

    for confirmation in confirmations:
        entry = float(
            confirmation[
                "entry_ask"
            ]
        )

        tp = float(
            confirmation[
                "tp_price"
            ]
        )

        sl = float(
            confirmation[
                "sl_price"
            ]
        )

        side = str(
            confirmation[
                "side"
            ]
        ).lower()

        rows = future_path(
            connection,
            ticker=(
                confirmation[
                    "market_ticker"
                ]
            ),
            after_ms=(
                confirmation[
                    "confirm_feature_ts"
                ]
            ),
        )

        trustworthy = (
            trustworthy_prefix(
                rows,
                start_ms=(
                    confirmation[
                        "confirm_feature_ts"
                    ]
                ),
            )
        )

        hit = None

        for row in trustworthy:
            bid = float(
                row[
                    f"{side}_bid"
                ]
            )

            if bid >= tp:
                hit = (
                    "TP",
                    row,
                    TP_DELTA,
                )
                break

            if bid <= sl:
                hit = (
                    "SL",
                    row,
                    -SL_DELTA,
                )
                break

        if hit is not None:
            (
                reason,
                row,
                planned_gross,
            ) = hit

            exit_bid = float(
                row[
                    f"{side}_bid"
                ]
            )

            connection.execute(
                """
                UPDATE main_trigger_confirmations

                SET
                    label_status = 'LABELED',

                    first_hit = ?,
                    exit_ts_ms = ?,
                    exit_bid = ?,

                    planned_gross_profit_per_contract
                        = ?,

                    gross_profit_per_contract
                        = ?,

                    path_complete = 1

                WHERE confirmation_id = ?
                """,
                (
                    reason,

                    int(
                        row[
                            "ts"
                        ]
                    ),

                    exit_bid,

                    planned_gross,

                    exit_bid
                    - entry,

                    confirmation[
                        "confirmation_id"
                    ],
                ),
            )

            changed += 1
            continue

        market_result = str(
            confirmation[
                "market_result"
            ]
            or ""
        ).lower()

        if market_result not in {
            "yes",
            "no",
        }:
            continue

        close_ms = iso_to_ms(
            confirmation[
                "close_time"
            ]
        )

        complete = (
            path_complete_to_close(
                rows,
                start_ms=(
                    confirmation[
                        "confirm_feature_ts"
                    ]
                ),
                close_ms=close_ms,
            )
        )

        if not complete:
            connection.execute(
                """
                UPDATE main_trigger_confirmations

                SET
                    label_status = 'INCOMPLETE',
                    settlement_result = ?,
                    path_complete = 0

                WHERE confirmation_id = ?
                """,
                (
                    market_result,

                    confirmation[
                        "confirmation_id"
                    ],
                ),
            )

            changed += 1
            continue

        settlement_exit = (
            1.0
            if market_result
            == side
            else 0.0
        )

        gross = (
            settlement_exit
            - entry
        )

        connection.execute(
            """
            UPDATE main_trigger_confirmations

            SET
                label_status = 'LABELED',

                first_hit = 'SETTLEMENT',

                exit_ts_ms = ?,
                exit_bid = ?,

                planned_gross_profit_per_contract
                    = ?,

                gross_profit_per_contract
                    = ?,

                settlement_result = ?,
                path_complete = 1

            WHERE confirmation_id = ?
            """,
            (
                close_ms,
                settlement_exit,
                gross,
                gross,
                market_result,

                confirmation[
                    "confirmation_id"
                ],
            ),
        )

        changed += 1

    return changed


def ensure_shadow_intents(
    connection,
    *,
    now_ms,
):
    rows = connection.execute(
        """
        SELECT confirmations.*

        FROM main_trigger_confirmations
            AS confirmations

        LEFT JOIN shadow_execution_intents
            AS intents

          ON intents.confirmation_id
             =
             confirmations.confirmation_id

        WHERE confirmations.status
              = 'CONFIRMED'

          AND intents.shadow_intent_id
              IS NULL

        ORDER BY
            confirmations.confirmed_at_ms
        """
    ).fetchall()

    inserted = 0

    for confirmation in rows:
        entry = float(
            confirmation[
                "entry_ask"
            ]
        )

        (
            count_fp,
            notional,
        ) = shadow_count_for_price(
            entry
        )

        if count_fp == "0.00":
            continue

        connection.execute(
            """
            INSERT INTO shadow_execution_intents (
                confirmation_id,

                mode,

                strategy_id,
                profile_id,

                market_ticker,
                side,
                episode_number,

                created_at_ms,

                notional_cap,

                count_fp,

                entry_price,
                entry_notional,

                tp_price,
                sl_price,

                status
            )
            VALUES (
                ?,

                'SHADOW',

                ?, ?,

                ?, ?, ?,

                ?,

                ?,

                ?,

                ?, ?,

                ?, ?,

                'OPEN'
            )
            """,
            (
                confirmation[
                    "confirmation_id"
                ],

                confirmation[
                    "strategy_id"
                ],

                confirmation[
                    "profile_id"
                ],

                confirmation[
                    "market_ticker"
                ],

                confirmation[
                    "side"
                ],

                confirmation[
                    "episode_number"
                ],

                int(
                    now_ms
                ),

                float(
                    SHADOW_NOTIONAL_CAP
                ),

                count_fp,

                entry,
                notional,

                confirmation[
                    "tp_price"
                ],

                confirmation[
                    "sl_price"
                ],
            ),
        )

        inserted += 1

    return inserted


def settle_shadow_intents(
    connection,
):
    rows = connection.execute(
        """
        SELECT
            intents.shadow_intent_id,
            intents.count_fp,

            confirmations.label_status,
            confirmations.first_hit,
            confirmations.exit_bid,

            confirmations.planned_gross_profit_per_contract,
            confirmations.gross_profit_per_contract

        FROM shadow_execution_intents
            AS intents

        JOIN main_trigger_confirmations
            AS confirmations

          ON confirmations.confirmation_id
             =
             intents.confirmation_id

        WHERE intents.status = 'OPEN'

          AND confirmations.label_status
              IN (
                  'LABELED',
                  'INCOMPLETE'
              )
        """
    ).fetchall()

    changed = 0

    for row in rows:
        if (
            row[
                "label_status"
            ]
            == "INCOMPLETE"
        ):
            connection.execute(
                """
                UPDATE shadow_execution_intents

                SET status = 'INCOMPLETE'

                WHERE shadow_intent_id = ?
                """,
                (
                    row[
                        "shadow_intent_id"
                    ],
                ),
            )

            changed += 1
            continue

        count = float(
            row[
                "count_fp"
            ]
        )

        planned = float(
            row[
                "planned_gross_profit_per_contract"
            ]
            or 0.0
        )

        bid_proxy = float(
            row[
                "gross_profit_per_contract"
            ]
            or 0.0
        )

        connection.execute(
            """
            UPDATE shadow_execution_intents

            SET
                status = 'CLOSED',

                exit_reason = ?,
                exit_price = ?,

                planned_gross_pnl = ?,
                bid_proxy_gross_pnl = ?

            WHERE shadow_intent_id = ?
            """,
            (
                row[
                    "first_hit"
                ],

                row[
                    "exit_bid"
                ],

                count
                * planned,

                count
                * bid_proxy,

                row[
                    "shadow_intent_id"
                ],
            ),
        )

        changed += 1

    return changed


def build_live_trigger_state(
    connection,
    *,
    market_ticker,
    side,
):
    opportunity = connection.execute(
        """
        SELECT *
        FROM prospective_opportunities

        WHERE strategy_id = ?
          AND market_ticker = ?
          AND side = ?

        ORDER BY episode_number DESC
        LIMIT 1
        """,
        (
            STRATEGY_ID,
            str(
                market_ticker
            ),
            str(
                side
            ).lower(),
        ),
    ).fetchone()

    if opportunity is None:
        return None

    confirmations = connection.execute(
        """
        SELECT
            confirmations.*,

            intents.count_fp,
            intents.entry_notional,
            intents.status
                AS shadow_status,

            intents.planned_gross_pnl,
            intents.bid_proxy_gross_pnl

        FROM main_trigger_confirmations
            AS confirmations

        LEFT JOIN shadow_execution_intents
            AS intents

          ON intents.confirmation_id
             =
             confirmations.confirmation_id

        WHERE confirmations.opportunity_id = ?

        ORDER BY
            CASE confirmations.profile_id
                WHEN 'RAW' THEN 1
                WHEN 'STABLE_3S' THEN 2
                WHEN 'STABLE_5S' THEN 3
                WHEN 'STABLE_10S' THEN 4
                WHEN 'STABLE_15S' THEN 5
                WHEN 'OCC_4_OF_5S' THEN 6
                WHEN 'OCC_8_OF_10S' THEN 7
                ELSE 99
            END
        """,
        (
            opportunity[
                "opportunity_id"
            ],
        ),
    ).fetchall()

    return {
        "opportunity_id":
            opportunity[
                "opportunity_id"
            ],

        "episode_number":
            opportunity[
                "episode_number"
            ],

        "episode_start_ms":
            opportunity[
                "episode_start_ms"
            ],

        "episode_end_ms":
            opportunity[
                "episode_end_ms"
            ],

        "confirmations": [
            dict(
                row
            )
            for row
            in confirmations
        ],
    }


def trigger_state_signature(
    state,
):
    if not state:
        return None

    return (
        state[
            "opportunity_id"
        ],

        state[
            "episode_number"
        ],

        state[
            "episode_end_ms"
        ],

        tuple(
            (
                row[
                    "profile_id"
                ],

                row[
                    "status"
                ],

                row[
                    "confirmed_at_ms"
                ],

                row[
                    "entry_ask"
                ],

                row[
                    "label_status"
                ],

                row[
                    "first_hit"
                ],

                row.get(
                    "shadow_status"
                ),
            )
            for row
            in state[
                "confirmations"
            ]
        ),
    )


def run_once(
    connection,
    *,
    now_ms=None,
):
    if now_ms is None:
        now_ms = int(
            time.time()
            * 1000
        )

    inserted = (
        ensure_confirmation_rows(
            connection
        )
    )

    confirmations = (
        service_waiting_confirmations(
            connection,
            now_ms=now_ms,
        )
    )

    labels = (
        label_confirmed_triggers(
            connection
        )
    )

    shadow_intents = (
        ensure_shadow_intents(
            connection,
            now_ms=now_ms,
        )
    )

    shadow_closed = (
        settle_shadow_intents(
            connection
        )
    )

    connection.commit()

    return {
        "rows_inserted":
            inserted,

        "confirmations":
            confirmations,

        "labels":
            labels,

        "shadow_intents":
            shadow_intents,

        "shadow_closed":
            shadow_closed,
    }


def run_loop(
    connection,
    *,
    interval=1.0,
):
    last_log = 0.0

    while True:
        result = run_once(
            connection
        )

        now = time.monotonic()

        changed = any(
            result.values()
        )

        if (
            changed
            or now
            - last_log
            >= 10.0
        ):
            waiting = (
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM main_trigger_confirmations
                    WHERE status = 'WAITING'
                    """
                ).fetchone()[0]
            )

            confirmed = (
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM main_trigger_confirmations
                    WHERE status = 'CONFIRMED'
                    """
                ).fetchone()[0]
            )

            print(
                "TRIGGER SHADOW | "
                f"new_rows={result['rows_inserted']} | "
                f"new_confirms={result['confirmations']} | "
                f"new_labels={result['labels']} | "
                f"shadow_opened={result['shadow_intents']} | "
                f"shadow_closed={result['shadow_closed']} | "
                f"waiting={waiting} | "
                f"confirmed={confirmed}"
            )

            last_log = now

        time.sleep(
            interval
        )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--db",
        required=True,
    )

    parser.add_argument(
        "--once",
        action="store_true",
    )

    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
    )

    args = parser.parse_args()

    connection = connect(
        args.db
    )

    try:
        init_db(
            connection
        )

        if args.once:
            print(
                run_once(
                    connection
                )
            )
            return

        run_loop(
            connection,
            interval=args.interval,
        )

    finally:
        connection.close()


if __name__ == "__main__":
    main()
