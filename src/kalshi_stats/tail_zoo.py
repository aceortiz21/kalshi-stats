from __future__ import annotations

import argparse
import json
import time

from .database import (
    connect,
    init_db,
)


LOW_BANDS = [
    ("0.1-0.4", .001, .004999),
    ("0.5-0.9", .005, .009999),
    ("1.0-1.9", .010, .019999),
    ("2.0-2.9", .020, .029999),
    ("3.0-4.9", .030, .049999),
]


HIGH_BANDS = [
    ("95-96", .950, .969999),
    ("97-98", .970, .984999),
    ("98.5-99", .985, .994999),
    ("99-99.9", .995, .999),
]


TIME_BANDS = [
    ("0.5-2m", 30, 119),
    ("2-4m", 120, 239),
    ("4-6m", 240, 359),
    ("6-8m", 360, 479),
    ("8-10m", 480, 599),
    ("10-12m", 600, 719),
    ("12-14m", 720, 839),
]


LOW_TARGET_MULTIPLIERS = [
    1.25,
    1.5,
    2.0,
    3.0,
    5.0,
]


HIGH_RULES = [
    {
        "id": "tp0.5_sl1",
        "tp_delta": .005,
        "sl_delta": .010,
    },
    {
        "id": "tp1_sl1",
        "tp_delta": .010,
        "sl_delta": .010,
    },
    {
        "id": "tp1_sl2",
        "tp_delta": .010,
        "sl_delta": .020,
    },
    {
        "id": "settle",
        "tp_delta": None,
        "sl_delta": None,
    },
]


def tail_strategy_key(
    side_type,
    price_name,
    time_name,
    exit_id,
):
    return (
        "tail:v1:"
        f"{side_type}:"
        f"p{price_name}:"
        f"t{time_name}:"
        f"{exit_id}"
    )


def iter_tail_definitions():
    for (
        price_name,
        price_low,
        price_high,
    ) in LOW_BANDS:

        reference = (
            price_low
            + price_high
        ) / 2.0

        for (
            time_name,
            seconds_low,
            seconds_high,
        ) in TIME_BANDS:

            for multiplier in (
                LOW_TARGET_MULTIPLIERS
            ):
                exit_id = (
                    "x"
                    + str(
                        multiplier
                    ).replace(
                        ".",
                        "_"
                    )
                )

                yield {
                    "strategy_key":
                        tail_strategy_key(
                            "LOW",
                            price_name,
                            time_name,
                            exit_id,
                        ),

                    "tail_type":
                        "LOW",

                    "price_name":
                        price_name,

                    "price_low":
                        price_low,

                    "price_high":
                        price_high,

                    "time_name":
                        time_name,

                    "seconds_low":
                        seconds_low,

                    "seconds_high":
                        seconds_high,

                    "exit_id":
                        exit_id,

                    "target_multiplier":
                        multiplier,

                    "reference_price":
                        reference,
                }

            yield {
                "strategy_key":
                    tail_strategy_key(
                        "LOW",
                        price_name,
                        time_name,
                        "settle",
                    ),

                "tail_type":
                    "LOW",

                "price_name":
                    price_name,

                "price_low":
                    price_low,

                "price_high":
                    price_high,

                "time_name":
                    time_name,

                "seconds_low":
                    seconds_low,

                "seconds_high":
                    seconds_high,

                "exit_id":
                    "settle",

                "target_multiplier":
                    None,
            }

    for (
        price_name,
        price_low,
        price_high,
    ) in HIGH_BANDS:

        for (
            time_name,
            seconds_low,
            seconds_high,
        ) in TIME_BANDS:

            for rule in HIGH_RULES:
                yield {
                    "strategy_key":
                        tail_strategy_key(
                            "HIGH",
                            price_name,
                            time_name,
                            rule[
                                "id"
                            ],
                        ),

                    "tail_type":
                        "HIGH",

                    "price_name":
                        price_name,

                    "price_low":
                        price_low,

                    "price_high":
                        price_high,

                    "time_name":
                        time_name,

                    "seconds_low":
                        seconds_low,

                    "seconds_high":
                        seconds_high,

                    "exit_id":
                        rule[
                            "id"
                        ],

                    "tp_delta":
                        rule[
                            "tp_delta"
                        ],

                    "sl_delta":
                        rule[
                            "sl_delta"
                        ],
                }


def register_tail_zoo(
    connection,
    *,
    now_ms=None,
):
    if now_ms is None:
        now_ms = int(
            time.time()
            * 1000
        )

    inserted = 0
    total = 0

    for definition in (
        iter_tail_definitions()
    ):
        total += 1

        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO
            shadow_strategy_registry (
                strategy_key,
                family,
                version,

                description,
                definition_json,

                evidence_basis,

                created_at_ms,
                discovery_cutoff_ms,
                shadow_start_ms,

                enabled
            )
            VALUES (
                ?,
                'TAIL_V1',
                1,

                ?,
                ?,

                'PREDECLARED_TAIL_FORWARD_ONLY',

                ?,
                ?,
                ?,

                1
            )
            """,
            (
                definition[
                    "strategy_key"
                ],

                (
                    f"{definition['price_name']}c "
                    f"/ {definition['time_name']} "
                    f"/ {definition['exit_id']}"
                ),

                json.dumps(
                    definition,
                    sort_keys=True,
                ),

                int(
                    now_ms
                ),

                int(
                    now_ms
                )
                - 1,

                int(
                    now_ms
                ),
            ),
        )

        inserted += max(
            0,
            int(
                cursor.rowcount
                or 0
            ),
        )

    connection.commit()

    return {
        "total":
            total,

        "registered":
            inserted,
    }


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

    args = parser.parse_args()

    connection = connect(
        args.db
    )

    try:
        init_db(
            connection
        )

        print(
            register_tail_zoo(
                connection
            )
        )

    finally:
        connection.close()


if __name__ == "__main__":
    main()
