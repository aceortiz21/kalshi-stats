from kalshi_stats.brti_live import (
    insert_brti_snapshot,
    parse_brti_message,
)
from kalshi_stats.database import (
    connect,
    init_db,
)


def _message(final=False):
    msg = {
        "type": "cfbenchmarks_value",
        "sid": 1,
        "seq": 42,
        "msg": {
            "index_id": "BRTI",
            "received_at": 1710000000124,
            "data": (
                '{"type":"value",'
                '"id":"BRTI",'
                '"time":1710000000123,'
                '"value":"68000.12"}'
            ),
            "avg_60s_data": {
                "value": "67999.50000000",
                "window_size": 59,
                "window_start_ts_ms": (
                    1709999940123
                ),
                "window_end_ts_exclusive": (
                    1710000000123
                ),
            },
        },
    }

    if final:
        msg["msg"][
            "last_60s_windowed_average_15min"
        ] = {
            "value": "68000.23000000",
            "window_size": 14,
            "window_start_ts_ms": (
                1709999980000
            ),
            "window_end_ts_exclusive": (
                1710000000123
            ),
        }

    return msg


def test_parse_brti_normal_tick():
    row = parse_brti_message(
        _message()
    )

    assert row is not None
    assert row["index_id"] == "BRTI"
    assert row["ts"] == 1710000000123
    assert row["value"] == 68000.12
    assert (
        row["avg_60s_value"]
        == 67999.5
    )
    assert (
        row["avg_60s_window_size"]
        == 59
    )
    assert (
        row["final_60s_avg_15m"]
        is None
    )


def test_parse_and_store_final_minute_tick():
    row = parse_brti_message(
        _message(final=True)
    )

    assert row is not None

    assert (
        row["final_60s_avg_15m"]
        == 68000.23
    )

    assert (
        row[
            "final_60s_window_size_15m"
        ]
        == 14
    )

    connection = connect(
        ":memory:"
    )

    try:
        init_db(
            connection
        )

        insert_brti_snapshot(
            connection,
            row,
        )

        connection.commit()

        stored = connection.execute(
            """
            SELECT *
            FROM brti_snapshots
            """
        ).fetchone()

        assert stored is not None
        assert (
            stored["value"]
            == 68000.12
        )
        assert (
            stored[
                "final_60s_avg_15m"
            ]
            == 68000.23
        )

    finally:
        connection.close()
