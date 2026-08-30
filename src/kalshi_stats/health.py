from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime) -> str:
    return (
        value
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _parse_utc(
    value: str | None,
) -> datetime | None:
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00",
            )
        )
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed.astimezone(
        timezone.utc
    )


def _age_seconds(
    value: str | None,
) -> float | None:
    parsed = _parse_utc(value)

    if parsed is None:
        return None

    return max(
        0.0,
        (
            _utc_now() - parsed
        ).total_seconds(),
    )


def build_data_health(
    connection: sqlite3.Connection,
    *,
    series_ticker: str,
    model_meta: dict,
    model_pending: int,
    auto_rebuild_after: int,
    pending_finalizations: int,
    current_market_ticker: str | None,
    ws_connected: bool,
    last_event_latency_ms: int | None,
    model_rebuild_running: bool,
) -> dict[str, object]:
    """
    Lightweight operational health snapshot.

    This deliberately measures data collection and model
    freshness only. It does not make trading-quality claims.
    """

    now = _utc_now()
    cutoff = now - timedelta(
        hours=24
    )

    cutoff_text = _utc_text(
        cutoff
    )
    now_text = _utc_text(
        now
    )

    recent = connection.execute(
        """
        WITH recent AS (
            SELECT
                ticker,
                result
            FROM markets
            WHERE series_ticker = ?
              AND close_time >= ?
              AND close_time <= ?
        ),
        candle_counts AS (
            SELECT
                market_ticker,
                COUNT(*) AS candle_count
            FROM candles
            WHERE period_interval = 1
            GROUP BY market_ticker
        )
        SELECT
            COUNT(*) AS market_count,
            SUM(
                CASE
                    WHEN result IN ('yes', 'no')
                    THEN 1
                    ELSE 0
                END
            ) AS settled_count,
            SUM(
                CASE
                    WHEN result IN ('yes', 'no')
                     AND COALESCE(
                         candle_count,
                         0
                     ) >= 14
                    THEN 1
                    ELSE 0
                END
            ) AS complete_candles
        FROM recent
        LEFT JOIN candle_counts
          ON candle_counts.market_ticker
           = recent.ticker
        """,
        (
            series_ticker,
            cutoff_text,
            now_text,
        ),
    ).fetchone()

    recent_markets = int(
        recent["market_count"]
        or 0
    )

    recent_settled = int(
        recent["settled_count"]
        or 0
    )

    complete_candles = int(
        recent["complete_candles"]
        or 0
    )

    incomplete_candles = max(
        0,
        recent_settled
        - complete_candles,
    )

    # KXBTC15M is expected to close four contracts
    # per hour, or 96 in a complete 24h window.
    expected_recent_markets = 96

    missing_recent_markets = max(
        0,
        expected_recent_markets
        - recent_markets,
    )

    quote_row = connection.execute(
        """
        SELECT
            COUNT(
                DISTINCT market_ticker
            ) AS recent_markets,
            MAX(collected_at)
                AS last_snapshot,
            MIN(collected_at)
                AS first_snapshot
        FROM quote_snapshots
        WHERE collected_at >= ?
        """,
        (cutoff_text,),
    ).fetchone()

    recent_quote_markets = int(
        quote_row[
            "recent_markets"
        ]
        or 0
    )

    last_snapshot = (
        quote_row["last_snapshot"]
        or ""
    )

    first_recent_snapshot = (
        quote_row["first_snapshot"]
        or ""
    )

    first_snapshot_row = (
        connection.execute(
            """
            SELECT
                MIN(collected_at)
            FROM quote_snapshots
            """
        ).fetchone()
    )

    first_snapshot = (
        first_snapshot_row[0]
        if first_snapshot_row
        else None
    )

    first_snapshot_dt = (
        _parse_utc(
            first_snapshot
        )
    )

    quote_coverage_mature = bool(
        first_snapshot_dt
        and first_snapshot_dt
        <= cutoff
    )

    quote_gap_markets = (
        max(
            0,
            expected_recent_markets
            - recent_quote_markets,
        )
        if quote_coverage_mature
        else 0
    )

    current_market_quotes = 0

    if current_market_ticker:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM quote_snapshots
            WHERE market_ticker = ?
            """,
            (
                current_market_ticker,
            ),
        ).fetchone()

        current_market_quotes = int(
            row[0] or 0
        )

    model_number = int(
        model_meta.get(
            "model_number",
            0,
        )
        or 0
    )

    model_market_count = int(
        model_meta.get(
            "market_count",
            0,
        )
        or 0
    )

    strong_strategies = int(
        model_meta.get(
            "strong_strategies",
            0,
        )
        or 0
    )

    model_built_at = str(
        model_meta.get(
            "built_at",
            "",
        )
        or ""
    )

    model_age_seconds = (
        _age_seconds(
            model_built_at
        )
    )

    last_snapshot_age = (
        _age_seconds(
            last_snapshot
        )
    )

    warnings: list[str] = []
    critical: list[str] = []

    if (
        current_market_ticker
        and not ws_connected
    ):
        warnings.append(
            "WebSocket is reconnecting"
        )

    if (
        last_event_latency_ms
        is not None
    ):
        if (
            last_event_latency_ms
            >= 10_000
        ):
            critical.append(
                "WebSocket processing "
                "latency >=10s"
            )
        elif (
            last_event_latency_ms
            >= 2_000
        ):
            warnings.append(
                "WebSocket processing "
                "latency >=2s"
            )

    if pending_finalizations >= 8:
        critical.append(
            "large finalization backlog"
        )
    elif pending_finalizations >= 3:
        warnings.append(
            "finalization backlog growing"
        )

    if incomplete_candles >= 4:
        critical.append(
            "multiple settled markets "
            "missing candles"
        )
    elif incomplete_candles > 0:
        warnings.append(
            "settled market awaiting "
            "complete candles"
        )

    if missing_recent_markets >= 4:
        critical.append(
            "recent market metadata gaps"
        )
    elif missing_recent_markets > 1:
        warnings.append(
            "possible recent market gap"
        )

    if quote_coverage_mature:
        if quote_gap_markets >= 4:
            critical.append(
                "high-resolution quote "
                "coverage gap"
            )
        elif quote_gap_markets > 1:
            warnings.append(
                "possible quote coverage gap"
            )

    if (
        model_age_seconds
        is not None
        and model_age_seconds
        >= 72 * 3600
    ):
        warnings.append(
            "model older than 72h"
        )

    if critical:
        status = "BAD"
        issues = (
            critical + warnings
        )
    elif warnings:
        status = "WARNING"
        issues = warnings
    else:
        status = "GOOD"
        issues = []

    return {
        "status": status,
        "issues": issues,
        "recent_markets": (
            recent_markets
        ),
        "expected_recent_markets": (
            expected_recent_markets
        ),
        "recent_settled": (
            recent_settled
        ),
        "complete_candles": (
            complete_candles
        ),
        "incomplete_candles": (
            incomplete_candles
        ),
        "recent_quote_markets": (
            recent_quote_markets
        ),
        "quote_coverage_mature": (
            quote_coverage_mature
        ),
        "quote_gap_markets": (
            quote_gap_markets
        ),
        "first_recent_snapshot": (
            first_recent_snapshot
        ),
        "last_snapshot": (
            last_snapshot
        ),
        "last_snapshot_age_seconds": (
            last_snapshot_age
        ),
        "current_market_quotes": (
            current_market_quotes
        ),
        "pending_finalizations": (
            pending_finalizations
        ),
        "ws_connected": (
            ws_connected
        ),
        "last_event_latency_ms": (
            last_event_latency_ms
        ),
        "model_number": (
            model_number
        ),
        "model_market_count": (
            model_market_count
        ),
        "strong_strategies": (
            strong_strategies
        ),
        "model_built_at": (
            model_built_at
        ),
        "model_age_seconds": (
            model_age_seconds
        ),
        "model_pending": int(
            model_pending
        ),
        "auto_rebuild_after": int(
            auto_rebuild_after
        ),
        "model_rebuild_running": (
            model_rebuild_running
        ),
    }


def health_signature(
    health: dict[str, object],
) -> tuple:
    """Stable fields that should trigger a UI refresh."""

    return (
        health.get("status"),
        health.get(
            "recent_markets"
        ),
        health.get(
            "complete_candles"
        ),
        health.get(
            "incomplete_candles"
        ),
        health.get(
            "recent_quote_markets"
        ),
        health.get(
            "pending_finalizations"
        ),
        health.get(
            "ws_connected"
        ),
        health.get(
            "model_number"
        ),
        health.get(
            "model_pending"
        ),
        health.get(
            "model_rebuild_running"
        ),
    )
