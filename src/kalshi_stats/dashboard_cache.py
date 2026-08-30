from __future__ import annotations

import hashlib
import pickle
import time
from pathlib import Path
from typing import Callable


CACHE_VERSION = 1


def _analysis_code_hash(
    scenarios_path: str,
) -> str:
    """Hash everything that materially changes cached analytics."""

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


def _historical_signature(
    connection,
    scenarios_path: str,
) -> tuple:
    """Cheap fingerprint of historical data used by the model.

    Active-market quote updates intentionally do not invalidate
    the expensive historical cache.
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

    # Only settled-market snapshots affect historical analytics.
    # Live snapshots from the current contract therefore do not
    # force a rebuild every time the program starts.
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


def load_or_build_historical_cache(
    *,
    connection,
    scenarios_path: str,
    cache_path: str | Path,
    builder: Callable,
    force_rebuild: bool = False,
):
    """Load a valid disk cache or rebuild it once."""

    path = Path(cache_path)
    signature = _historical_signature(
        connection,
        scenarios_path,
    )

    started = time.perf_counter()

    if not force_rebuild and path.exists():
        try:
            with path.open("rb") as file:
                payload = pickle.load(file)

            if (
                payload.get("version") == CACHE_VERSION
                and payload.get("signature") == signature
            ):
                return (
                    payload["cache"],
                    "loaded from disk",
                    time.perf_counter() - started,
                )

        except Exception as exc:
            print(
                "Historical cache could not be loaded; "
                f"rebuilding: {type(exc).__name__}: {exc}"
            )

    cache = builder(
        connection,
        scenarios_path,
    )

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
