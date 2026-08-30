import sqlite3

from kalshi_stats.live_monitor import (
    brti_state_signature,
    load_live_brti_state,
)
from kalshi_stats.reporting import (
    _render_brti_strip,
)


def _connection():
    connection = sqlite3.connect(
        ":memory:"
    )

    connection.row_factory = (
        sqlite3.Row
    )

    connection.executescript(
        """
        CREATE TABLE markets (
            ticker TEXT PRIMARY KEY,
            reference_price REAL
        );

        CREATE TABLE brti_snapshots (
            index_id TEXT NOT NULL,
            ts INTEGER NOT NULL,
            value REAL NOT NULL,
            avg_60s_value REAL,
            avg_60s_window_size INTEGER,
            final_60s_avg_15m REAL,
            final_60s_window_size_15m INTEGER
        );
        """
    )

    return connection


def test_live_brti_state_calculates_distances():
    connection = _connection()

    try:
        connection.execute(
            """
            INSERT INTO markets
            VALUES (?, ?)
            """,
            (
                "TEST",
                79000.0,
            ),
        )

        connection.execute(
            """
            INSERT INTO brti_snapshots
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "BRTI",
                1000,
                79010.0,
                79008.0,
                60,
                79005.0,
                37,
            ),
        )

        state = load_live_brti_state(
            connection,
            "TEST",
            now_ms=1500,
        )

        assert state is not None
        assert state["age_ms"] == 500
        assert state["target"] == 79000.0
        assert state["value"] == 79010.0
        assert (
            state["distance_dollars"]
            == 10.0
        )
        assert (
            state[
                "final_distance_dollars"
            ]
            == 5.0
        )

        signature = (
            brti_state_signature(
                state
            )
        )

        assert signature is not None
        assert signature[0] == 1000

    finally:
        connection.close()


def test_brti_strip_renders_final_window():
    rendered = _render_brti_strip(
        {
            "target": 79000.0,
            "ts": 1000,
            "age_ms": 500,
            "value": 79010.0,
            "distance_dollars": 10.0,
            "distance_bps": (
                10.0
                / 79000.0
                * 10000.0
            ),
            "avg_60s_value": 79008.0,
            "avg_60s_window_size": 60,
            "final_60s_avg_15m": 79005.0,
            "final_60s_window_size_15m": 37,
            "final_distance_dollars": 5.0,
            "final_distance_bps": (
                5.0
                / 79000.0
                * 10000.0
            ),
        }
    )

    assert "OFFICIAL KALSHI BRTI" in rendered
    assert "$79,010.00" in rendered
    assert "$79,000.00" in rendered
    assert "Official settlement avg" in rendered
    assert "$79,005.00" in rendered
    assert "37 / 60" in rendered
