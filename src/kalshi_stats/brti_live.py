from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import time

import websockets

from cryptography.hazmat.primitives import (
    hashes,
    serialization,
)
from cryptography.hazmat.primitives.asymmetric import (
    padding,
)

from .database import (
    connect,
    init_db,
)


WS_URL = (
    "wss://external-api-ws.kalshi.com"
    "/trade-api/ws/v2"
)

WS_PATH = "/trade-api/ws/v2"

DEFAULT_INDEX_ID = "BRTI"


def _sign(
    private_key,
    text: str,
) -> str:
    signature = private_key.sign(
        text.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(
                hashes.SHA256()
            ),
            salt_length=(
                padding.PSS.DIGEST_LENGTH
            ),
        ),
        hashes.SHA256(),
    )

    return base64.b64encode(
        signature
    ).decode("utf-8")


def _auth_headers():
    key_id = os.environ[
        "KALSHI_API_KEY_ID"
    ]

    key_path = os.environ[
        "KALSHI_PRIVATE_KEY_PATH"
    ]

    with open(
        key_path,
        "rb",
    ) as file:
        private_key = (
            serialization.load_pem_private_key(
                file.read(),
                password=None,
            )
        )

    timestamp = str(
        int(time.time() * 1000)
    )

    message = (
        timestamp
        + "GET"
        + WS_PATH
    )

    return {
        "KALSHI-ACCESS-KEY": (
            key_id
        ),
        "KALSHI-ACCESS-TIMESTAMP": (
            timestamp
        ),
        "KALSHI-ACCESS-SIGNATURE": (
            _sign(
                private_key,
                message,
            )
        ),
    }


def parse_brti_message(
    message: dict,
) -> dict | None:
    if (
        message.get("type")
        != "cfbenchmarks_value"
    ):
        return None

    msg = message.get(
        "msg",
        {},
    )

    if (
        msg.get("index_id")
        != "BRTI"
    ):
        return None

    try:
        upstream = json.loads(
            msg.get(
                "data",
                "{}",
            )
        )
    except (
        TypeError,
        json.JSONDecodeError,
    ):
        return None

    if (
        upstream.get("time")
        is None
        or upstream.get("value")
        is None
    ):
        return None

    avg60 = (
        msg.get(
            "avg_60s_data"
        )
        or {}
    )

    final60 = (
        msg.get(
            "last_60s_windowed_average_15min"
        )
        or {}
    )

    return {
        "index_id": "BRTI",
        "ts": int(
            upstream["time"]
        ),
        "received_at": (
            None
            if msg.get(
                "received_at"
            )
            is None
            else int(
                msg["received_at"]
            )
        ),
        "value": float(
            upstream["value"]
        ),

        "avg_60s_value": (
            None
            if avg60.get("value")
            is None
            else float(
                avg60["value"]
            )
        ),
        "avg_60s_window_size": (
            None
            if avg60.get(
                "window_size"
            )
            is None
            else int(
                avg60[
                    "window_size"
                ]
            )
        ),
        "avg_60s_window_start_ts_ms": (
            avg60.get(
                "window_start_ts_ms"
            )
        ),
        "avg_60s_window_end_ts_exclusive": (
            avg60.get(
                "window_end_ts_exclusive"
            )
        ),

        "final_60s_avg_15m": (
            None
            if final60.get(
                "value"
            )
            is None
            else float(
                final60[
                    "value"
                ]
            )
        ),
        "final_60s_window_size_15m": (
            None
            if final60.get(
                "window_size"
            )
            is None
            else int(
                final60[
                    "window_size"
                ]
            )
        ),
        "final_60s_window_start_ts_ms_15m": (
            final60.get(
                "window_start_ts_ms"
            )
        ),
        "final_60s_window_end_ts_exclusive_15m": (
            final60.get(
                "window_end_ts_exclusive"
            )
        ),
    }


def insert_brti_snapshot(
    connection,
    row: dict,
) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO
        brti_snapshots (
            index_id,
            ts,
            received_at,
            value,

            avg_60s_value,
            avg_60s_window_size,
            avg_60s_window_start_ts_ms,
            avg_60s_window_end_ts_exclusive,

            final_60s_avg_15m,
            final_60s_window_size_15m,
            final_60s_window_start_ts_ms_15m,
            final_60s_window_end_ts_exclusive_15m
        )
        VALUES (
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?
        )
        """,
        (
            row["index_id"],
            row["ts"],
            row["received_at"],
            row["value"],

            row[
                "avg_60s_value"
            ],
            row[
                "avg_60s_window_size"
            ],
            row[
                "avg_60s_window_start_ts_ms"
            ],
            row[
                "avg_60s_window_end_ts_exclusive"
            ],

            row[
                "final_60s_avg_15m"
            ],
            row[
                "final_60s_window_size_15m"
            ],
            row[
                "final_60s_window_start_ts_ms_15m"
            ],
            row[
                "final_60s_window_end_ts_exclusive_15m"
            ],
        ),
    )


async def run_brti_live(
    *,
    db_path: str,
    index_id: str = DEFAULT_INDEX_ID,
) -> None:
    connection = connect(
        db_path
    )

    init_db(
        connection
    )

    reconnect_delay = 0.5
    saved = 0

    try:
        while True:
            websocket = None

            try:
                websocket = (
                    await websockets.connect(
                        WS_URL,
                        additional_headers=(
                            _auth_headers()
                        ),
                        ping_interval=20,
                        ping_timeout=20,
                        close_timeout=5,
                        max_size=2 * 1024 * 1024,
                    )
                )

                await websocket.send(
                    json.dumps(
                        {
                            "id": 1,
                            "cmd": "subscribe",
                            "params": {
                                "channels": [
                                    "cfbenchmarks_value"
                                ],
                                "index_ids": [
                                    index_id
                                ],
                            },
                        }
                    )
                )

                print(
                    "BRTI WebSocket subscribed:",
                    index_id,
                )

                reconnect_delay = 0.5
                last_log = 0.0

                while True:
                    raw = (
                        await websocket.recv()
                    )

                    message = json.loads(
                        raw
                    )

                    if (
                        message.get(
                            "type"
                        )
                        == "error"
                    ):
                        raise RuntimeError(
                            f"BRTI WebSocket error: "
                            f"{message}"
                        )

                    row = parse_brti_message(
                        message
                    )

                    if row is None:
                        continue

                    insert_brti_snapshot(
                        connection,
                        row,
                    )

                    connection.commit()

                    saved += 1

                    now = time.monotonic()

                    final_avg = row[
                        "final_60s_avg_15m"
                    ]

                    final_n = row[
                        "final_60s_window_size_15m"
                    ]

                    # Log every tick during the final
                    # settlement minute; otherwise ~5s.
                    should_log = (
                        final_avg is not None
                        or now - last_log >= 5.0
                    )

                    if should_log:
                        final_text = (
                            "-"
                            if final_avg is None
                            else (
                                f"${final_avg:,.2f}"
                                f" ({final_n}/60)"
                            )
                        )

                        print(
                            "BRTI live | "
                            f"${row['value']:,.2f} | "
                            f"avg60="
                            f"${row['avg_60s_value']:,.2f} | "
                            f"final15m={final_text} | "
                            f"saved={saved}"
                        )

                        last_log = now

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                print(
                    "BRTI WebSocket disconnected: "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

                print(
                    "Reconnecting in "
                    f"{reconnect_delay:.1f}s..."
                )

                await asyncio.sleep(
                    reconnect_delay
                )

                reconnect_delay = min(
                    reconnect_delay * 2,
                    10.0,
                )

            finally:
                if websocket is not None:
                    try:
                        await websocket.close()
                    except Exception:
                        pass

    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Collect Kalshi CF Benchmarks "
            "BRTI WebSocket values."
        )
    )

    parser.add_argument(
        "--db",
        required=True,
    )

    parser.add_argument(
        "--index",
        default=DEFAULT_INDEX_ID,
    )

    args = parser.parse_args()

    try:
        asyncio.run(
            run_brti_live(
                db_path=args.db,
                index_id=args.index,
            )
        )

    except KeyboardInterrupt:
        print(
            "\nBRTI collector stopped."
        )


if __name__ == "__main__":
    main()
