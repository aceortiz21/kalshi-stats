from __future__ import annotations

import argparse
import json
import statistics
import time

from collections import defaultdict

from .database import (
    connect,
    init_db,
)

from .shadow_lab import (
    register_strategy,
)

from .trigger_shadow import (
    PROFILES,
)


MAIN_PRICE_BANDS = [
    (
        "60-62",
        .60,
        .629999,
    ),
    (
        "63-65",
        .63,
        .659999,
    ),
    (
        "66-69",
        .66,
        .69,
    ),
]


MAIN_TIME_BANDS = [
    (
        "5-6m",
        300,
        359,
    ),
    (
        "6-8m",
        360,
        479,
    ),
    (
        "8-10m",
        480,
        599,
    ),
]


MAIN_DISCOVERY_MIN_N = 5
MICRO_DISCOVERY_MIN_N = 10

MAX_NEW_MAIN_PER_RUN = 8
MAX_NEW_MICRO_PER_RUN = 8


def normalized_roi(
    *,
    pnl,
    notional,
):
    notional = float(
        notional
    )

    if notional <= 0:
        return None

    return (
        float(
            pnl
        )
        / notional
    )


def main_discovery_candidates(
    rows,
):
    grouped = defaultdict(
        list
    )

    for row in rows:
        profile_id = str(
            row[
                "profile_id"
            ]
        )

        entry = float(
            row[
                "entry_ask"
            ]
        )

        seconds = float(
            row[
                "seconds_remaining"
            ]
        )

        roi = normalized_roi(
            pnl=row[
                "bid_proxy_gross_pnl"
            ],
            notional=row[
                "entry_notional"
            ],
        )

        if roi is None:
            continue

        for (
            price_name,
            price_low,
            price_high,
        ) in MAIN_PRICE_BANDS:

            if not (
                price_low
                <= entry
                <= price_high
            ):
                continue

            for (
                time_name,
                seconds_low,
                seconds_high,
            ) in MAIN_TIME_BANDS:

                if not (
                    seconds_low
                    <= seconds
                    <= seconds_high
                ):
                    continue

                key = (
                    profile_id,
                    price_name,
                    time_name,
                )

                grouped[
                    key
                ].append(
                    roi
                )

    candidates = []

    for (
        profile_id,
        price_name,
        time_name,
    ), rois in grouped.items():

        n = len(
            rois
        )

        if n < MAIN_DISCOVERY_MIN_N:
            continue

        avg_roi = statistics.fmean(
            rois
        )

        if avg_roi <= 0:
            continue

        price = next(
            item
            for item
            in MAIN_PRICE_BANDS
            if item[0]
            == price_name
        )

        timing = next(
            item
            for item
            in MAIN_TIME_BANDS
            if item[0]
            == time_name
        )

        candidates.append(
            {
                "profile_id":
                    profile_id,

                "price_name":
                    price_name,

                "entry_low":
                    price[1],

                "entry_high":
                    price[2],

                "time_name":
                    time_name,

                "seconds_low":
                    timing[1],

                "seconds_high":
                    timing[2],

                "discovery_n":
                    n,

                "discovery_avg_roi":
                    avg_roi,
            }
        )

    candidates.sort(
        key=lambda item: (
            item[
                "discovery_avg_roi"
            ],
            item[
                "discovery_n"
            ],
        ),
        reverse=True,
    )

    return candidates


def micro_discovery_candidates(
    rows,
):
    grouped = defaultdict(
        list
    )

    for row in rows:
        entry_price = float(
            row[
                "entry_ask"
            ]
        )

        target_price = float(
            row[
                "target_price"
            ]
        )

        if entry_price <= 0:
            continue

        status = str(
            row[
                "status"
            ]
        )

        if status == "HIT":
            roi = (
                target_price
                / entry_price
                - 1.0
            )

        elif status == "MISS":
            roi = -1.0

        else:
            continue

        key = (
            int(
                row[
                    "entry_price_key"
                ]
            ),

            str(
                row[
                    "time_bucket"
                ]
            ),

            int(
                round(
                    target_price
                    * 1000
                )
            ),
        )

        grouped[
            key
        ].append(
            roi
        )

    candidates = []

    for (
        entry_key,
        time_bucket,
        target_key,
    ), rois in grouped.items():

        n = len(
            rois
        )

        if n < MICRO_DISCOVERY_MIN_N:
            continue

        avg_roi = statistics.fmean(
            rois
        )

        if avg_roi <= 0:
            continue

        candidates.append(
            {
                "entry_price_key":
                    entry_key,

                "time_bucket":
                    time_bucket,

                "target_price_key":
                    target_key,

                "target_price":
                    (
                        target_key
                        / 1000.0
                    ),

                "discovery_n":
                    n,

                "discovery_avg_roi":
                    avg_roi,
            }
        )

    candidates.sort(
        key=lambda item: (
            item[
                "discovery_avg_roi"
            ],
            item[
                "discovery_n"
            ],
        ),
        reverse=True,
    )

    return candidates


def historical_micro_key(
    *,
    entry_key,
    time_bucket,
    target_key,
):
    return (
        f"micro:"
        f"{entry_key}:"
        f"{time_bucket}:"
        f"{target_key}:v1"
    )


def register_main_challengers(
    connection,
    *,
    now_ms,
):
    rows = connection.execute(
        """
        SELECT
            confirmations.profile_id,
            confirmations.entry_ask,
            confirmations.seconds_remaining,

            intents.entry_notional,
            intents.bid_proxy_gross_pnl

        FROM shadow_execution_intents
            AS intents

        JOIN main_trigger_confirmations
            AS confirmations

          ON confirmations.confirmation_id
             =
             intents.confirmation_id

        WHERE intents.status = 'CLOSED'

          AND confirmations.confirmed_at_ms
              < ?

          AND intents.entry_notional > 0

          AND intents.bid_proxy_gross_pnl
              IS NOT NULL
        """,
        (
            int(
                now_ms
            ),
        ),
    ).fetchall()

    candidates = (
        main_discovery_candidates(
            rows
        )
    )

    inserted = 0

    for candidate in candidates[
        :MAX_NEW_MAIN_PER_RUN
    ]:
        profile_id = candidate[
            "profile_id"
        ]

        price_name = candidate[
            "price_name"
        ]

        time_name = candidate[
            "time_name"
        ]

        strategy_key = (
            "mainctx:"
            + profile_id
            + ":"
            + price_name
            + ":"
            + time_name
            + ":v1"
        )

        inserted += register_strategy(
            connection,

            strategy_key=(
                strategy_key
            ),

            family=(
                "MAIN_CONTEXT"
            ),

            version=1,

            description=(
                f"{profile_id} · "
                f"{price_name}c · "
                f"{time_name}"
            ),

            definition={
                "profile_id":
                    profile_id,

                "entry_low":
                    candidate[
                        "entry_low"
                    ],

                "entry_high":
                    candidate[
                        "entry_high"
                    ],

                "seconds_low":
                    candidate[
                        "seconds_low"
                    ],

                "seconds_high":
                    candidate[
                        "seconds_high"
                    ],

                "tp_delta":
                    .25,

                "sl_delta":
                    .05,

                "discovery_n":
                    candidate[
                        "discovery_n"
                    ],

                "discovery_avg_roi":
                    candidate[
                        "discovery_avg_roi"
                    ],
            },

            evidence_basis=(
                "DISCOVERY_FILTERED_SHADOW_"
                "THEN_FORWARD"
            ),

            now_ms=now_ms,
        )

    return inserted


def register_micro_challengers(
    connection,
    *,
    now_ms,
):
    rows = connection.execute(
        """
        SELECT
            opportunities.entry_price_key,
            opportunities.entry_ask,
            opportunities.time_bucket,

            targets.target_price,
            targets.status

        FROM micro_multiplier_opportunities
            AS opportunities

        JOIN micro_multiplier_targets
            AS targets

          ON targets.micro_opportunity_id
             =
             opportunities.micro_opportunity_id

        WHERE opportunities.detected_at_ms
              < ?

          AND targets.status
              IN (
                  'HIT',
                  'MISS'
              )
        """,
        (
            int(
                now_ms
            ),
        ),
    ).fetchall()

    candidates = (
        micro_discovery_candidates(
            rows
        )
    )

    inserted = 0

    for candidate in candidates:
        entry_key = candidate[
            "entry_price_key"
        ]

        time_bucket = candidate[
            "time_bucket"
        ]

        target_key = candidate[
            "target_price_key"
        ]

        # If this exact micro cell is already one of
        # the original historical-atlas challengers,
        # don't create a duplicate live-discovery copy.
        existing_historical = (
            connection.execute(
                """
                SELECT 1
                FROM shadow_strategy_registry
                WHERE strategy_key = ?
                LIMIT 1
                """,
                (
                    historical_micro_key(
                        entry_key=(
                            entry_key
                        ),

                        time_bucket=(
                            time_bucket
                        ),

                        target_key=(
                            target_key
                        ),
                    ),
                ),
            ).fetchone()
            is not None
        )

        if existing_historical:
            continue

        strategy_key = (
            f"micro_live:"
            f"{entry_key}:"
            f"{time_bucket}:"
            f"{target_key}:v1"
        )

        inserted += register_strategy(
            connection,

            strategy_key=(
                strategy_key
            ),

            family=(
                "MICRO_LIVE_DISCOVERY"
            ),

            version=1,

            description=(
                f"{entry_key / 10:.1f}c "
                f"-> "
                f"{target_key / 10:.1f}c "
                f"/ {time_bucket}"
            ),

            definition={
                "entry_price_key":
                    entry_key,

                "time_bucket":
                    time_bucket,

                "target_price_key":
                    target_key,

                "target_price":
                    candidate[
                        "target_price"
                    ],

                "discovery_n":
                    candidate[
                        "discovery_n"
                    ],

                "discovery_avg_roi":
                    candidate[
                        "discovery_avg_roi"
                    ],
            },

            evidence_basis=(
                "LIVE_DISCOVERY_"
                "THEN_FORWARD"
            ),

            now_ms=now_ms,
        )

        if (
            inserted
            >= MAX_NEW_MICRO_PER_RUN
        ):
            break

    return inserted


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

    main_inserted = (
        register_main_challengers(
            connection,
            now_ms=now_ms,
        )
    )

    micro_inserted = (
        register_micro_challengers(
            connection,
            now_ms=now_ms,
        )
    )

    connection.commit()

    return {
        "main_challengers":
            main_inserted,

        "micro_challengers":
            micro_inserted,

        "total_new":
            (
                main_inserted
                + micro_inserted
            ),
    }


def run_loop(
    connection,
    *,
    interval=900.0,
):
    while True:
        result = run_once(
            connection
        )

        print(
            "CHALLENGER GENERATOR | "
            f"main_new="
            f"{result['main_challengers']} | "
            f"micro_new="
            f"{result['micro_challengers']} | "
            f"total_new="
            f"{result['total_new']}"
        )

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
        default=900.0,
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
