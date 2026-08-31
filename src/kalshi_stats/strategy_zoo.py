from __future__ import annotations

import argparse
import json
import time

from .database import (
    connect,
    init_db,
)


PRICE_BANDS = [
    ("05-14", .05, .149999),
    ("15-24", .15, .249999),
    ("25-34", .25, .349999),
    ("35-44", .35, .449999),
    ("45-54", .45, .549999),
    ("55-64", .55, .649999),
    ("65-74", .65, .749999),
    ("75-84", .75, .849999),
    ("85-94", .85, .949999),
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


EXIT_RULES = [
    {
        "id": "tp05_sl05",
        "tp_delta": .05,
        "sl_delta": .05,
    },
    {
        "id": "tp10_sl05",
        "tp_delta": .10,
        "sl_delta": .05,
    },
    {
        "id": "tp15_sl05",
        "tp_delta": .15,
        "sl_delta": .05,
    },
    {
        "id": "tp20_sl10",
        "tp_delta": .20,
        "sl_delta": .10,
    },
    {
        "id": "tp25_sl10",
        "tp_delta": .25,
        "sl_delta": .10,
    },
    {
        "id": "settle",
        "tp_delta": None,
        "sl_delta": None,
    },
]


def price_band_for(
    price,
):
    price = float(
        price
    )

    for (
        name,
        low,
        high,
    ) in PRICE_BANDS:

        if (
            low
            <= price
            <= high
        ):
            return (
                name,
                low,
                high,
            )

    return None


def time_band_for(
    seconds,
):
    seconds = float(
        seconds
    )

    for (
        name,
        low,
        high,
    ) in TIME_BANDS:

        if (
            low
            <= seconds
            <= high
        ):
            return (
                name,
                low,
                high,
            )

    return None


def grid_strategy_key(
    price_name,
    time_name,
    exit_id,
):
    return (
        "grid:v1:"
        f"p{price_name}:"
        f"t{time_name}:"
        f"{exit_id}"
    )


def iter_grid_definitions():
    for (
        price_name,
        price_low,
        price_high,
    ) in PRICE_BANDS:

        for (
            time_name,
            seconds_low,
            seconds_high,
        ) in TIME_BANDS:

            for rule in EXIT_RULES:
                key = (
                    grid_strategy_key(
                        price_name,
                        time_name,
                        rule[
                            "id"
                        ],
                    )
                )

                yield {
                    "strategy_key":
                        key,

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


def register_zoo(
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
        iter_grid_definitions()
    ):
        total += 1

        description = (
            f"{definition['price_name']}c "
            f"/ {definition['time_name']} "
            f"/ {definition['exit_id']}"
        )

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

                'GRID_V1',
                1,

                ?,
                ?,

                'PREDECLARED_GRID_FORWARD_ONLY',

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

                description,

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

        result = register_zoo(
            connection
        )

        print(
            "STRATEGY ZOO | "
            f"total={result['total']} | "
            f"registered="
            f"{result['registered']}"
        )

    finally:
        connection.close()


if __name__ == "__main__":
    main()
