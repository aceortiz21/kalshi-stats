from __future__ import annotations

import hashlib
import pickle
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


CACHE_VERSION = 2


def _analysis_code_hash(
    scenarios_path: str,
) -> str:
    """Hash code/config that materially changes analytics."""

    package_dir = Path(__file__).resolve().parent

    paths = [
        package_dir / "analytics.py",
        package_dir / "strategies.py",
        package_dir / "scenarios.py",
        package_dir / "models.py",
        Path(scenarios_path).resolve(),
    ]

    digest = hashlib.sha256()

    for path in paths:
        digest.update(str(path).encode("utf-8"))
        digest.update(path.read_bytes())

    return digest.hexdigest()


def _model_market_count(connection) -> int:
    """Count settled markets with data usable by analytics."""

    row = connection.execute(
        """
        SELECT COUNT(*)
        FROM markets m
        WHERE m.result IN ('yes', 'no')
          AND (
              EXISTS (
                  SELECT 1
                  FROM candles c
                  WHERE c.market_ticker = m.ticker
              )
              OR EXISTS (
                  SELECT 1
                  FROM trades t
                  WHERE t.market_ticker = m.ticker
              )
              OR EXISTS (
                  SELECT 1
                  FROM quote_snapshots s
                  WHERE s.market_ticker = m.ticker
              )
          )
        """
    ).fetchone()

    return int(row[0])


def _historical_signature(
    connection,
    scenarios_path: str,
) -> tuple:
    """Fingerprint historical inputs.

    Active-market snapshots do not invalidate the model until
    that market actually becomes settled historical data.
    """

    settled = connection.execute(
        """
        SELECT
            COUNT(*) AS market_count,
            COALESCE(MAX(close_time), '') AS max_close
        FROM markets
        WHERE result IN ('yes', 'no')
        """
    ).fetchone()

    max_candle_rowid = connection.execute(
        """
        SELECT COALESCE(MAX(rowid), 0)
        FROM candles
        """
    ).fetchone()[0]

    max_trade_rowid = connection.execute(
        """
        SELECT COALESCE(MAX(rowid), 0)
        FROM trades
        """
    ).fetchone()[0]

    max_settled_snapshot_rowid = connection.execute(
        """
        SELECT COALESCE(MAX(s.rowid), 0)
        FROM quote_snapshots s
        JOIN markets m
          ON m.ticker = s.market_ticker
        WHERE m.result IN ('yes', 'no')
        """
    ).fetchone()[0]

    return (
        CACHE_VERSION,
        _analysis_code_hash(scenarios_path),
        int(settled["market_count"]),
        str(settled["max_close"]),
        int(max_candle_rowid),
        int(max_trade_rowid),
        int(max_settled_snapshot_rowid),
    )


def _read_payload(path: Path):
    if not path.exists():
        return None

    try:
        with path.open("rb") as file:
            return pickle.load(file)
    except Exception:
        return None


def load_cached_historical_cache(
    *,
    connection,
    scenarios_path: str,
    cache_path: str | Path,
):
    """Return the disk model only when it matches current data."""

    path = Path(cache_path)
    payload = _read_payload(path)

    if payload is None:
        return None

    if payload.get("version") != CACHE_VERSION:
        return None

    current_signature = _historical_signature(
        connection,
        scenarios_path,
    )

    if payload.get("signature") != current_signature:
        return None

    return payload.get("cache")


def count_model_pending_markets(
    connection,
    cache,
) -> int:
    """Settled model-eligible markets added since model build."""

    metadata = cache.get("_model_meta", {})

    model_count = int(
        metadata.get("market_count", 0)
    )

    current_count = _model_market_count(
        connection
    )

    return max(
        0,
        current_count - model_count,
    )


def load_or_build_historical_cache(
    *,
    connection,
    scenarios_path: str,
    cache_path: str | Path,
    builder: Callable,
    force_rebuild: bool = False,
):
    """Load a valid disk model or build a new version."""

    path = Path(cache_path)

    signature = _historical_signature(
        connection,
        scenarios_path,
    )

    started = time.perf_counter()

    old_payload = _read_payload(path)

    previous_model_number = 0

    if old_payload is not None:
        old_cache = old_payload.get("cache", {})

        previous_model_number = int(
            old_cache
            .get("_model_meta", {})
            .get("model_number", 0)
        )

        if (
            not force_rebuild
            and old_payload.get("version")
                == CACHE_VERSION
            and old_payload.get("signature")
                == signature
        ):
            return (
                old_cache,
                "loaded from disk",
                time.perf_counter() - started,
            )

    cache = builder(
        connection,
        scenarios_path,
    )

    model_number = previous_model_number + 1

    strong_strategies = sum(
        1
        for result in cache[
            "validated_strategies"
        ]
        if result.validation_status == "STRONG"
    )

    cache["_model_meta"] = {
        "model_number": model_number,
        "built_at": (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        ),
        "market_count": _model_market_count(
            connection
        ),
        "strong_strategies": strong_strategies,
    }

    payload = {
        "version": CACHE_VERSION,
        "signature": signature,
        "cache": cache,
    }

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = path.with_name(
        path.name + ".tmp"
    )

    with temporary.open("wb") as file:
        pickle.dump(
            payload,
            file,
            protocol=pickle.HIGHEST_PROTOCOL,
        )

    temporary.replace(path)

    return (
        cache,
        "rebuilt and saved",
        time.perf_counter() - started,
    )
