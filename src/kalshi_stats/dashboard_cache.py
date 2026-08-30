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
        digest.update(
            str(path).encode("utf-8")
        )
        digest.update(
            path.read_bytes()
        )

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
        _analysis_code_hash(
            scenarios_path
        ),
        int(settled["market_count"]),
        str(settled["max_close"]),
        int(max_candle_rowid),
        int(max_trade_rowid),
        int(
            max_settled_snapshot_rowid
        ),
    )


def _read_payload(path: Path):
    if not path.exists():
        return None

    try:
        with path.open("rb") as file:
            return pickle.load(file)
    except Exception:
        return None


def _write_payload(
    path: Path,
    payload,
) -> None:
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


def _strong_strategy_records(
    cache,
) -> list[dict[str, str]]:
    """
    Serialize stable identities for current STRONG strategies.

    The identity is deliberately based on the state plus the
    mechanical exit-strategy ID, not ranking position.
    """

    records: list[
        dict[str, str]
    ] = []

    for result in cache.get(
        "validated_strategies",
        [],
    ):
        if (
            getattr(
                result,
                "validation_status",
                "",
            )
            != "STRONG"
        ):
            continue

        strategy = getattr(
            result,
            "strategy",
            None,
        )

        strategy_id = str(
            getattr(
                strategy,
                "id",
                getattr(
                    result,
                    "strategy_id",
                    "unknown",
                ),
            )
        )

        strategy_name = str(
            getattr(
                strategy,
                "name",
                strategy_id,
            )
        )

        price_bucket = str(
            getattr(
                result,
                "price_bucket",
                "unknown",
            )
        )

        time_bucket = str(
            getattr(
                result,
                "time_bucket",
                "unknown",
            )
        )

        key = (
            f"{price_bucket}|"
            f"{time_bucket}|"
            f"{strategy_id}"
        )

        records.append(
            {
                "key": key,
                "price_bucket": (
                    price_bucket
                ),
                "time_bucket": (
                    time_bucket
                ),
                "strategy_id": (
                    strategy_id
                ),
                "strategy_name": (
                    strategy_name
                ),
            }
        )

    records.sort(
        key=lambda item: item["key"]
    )

    return records


def _model_record_from_cache(
    cache,
) -> dict[str, object] | None:
    meta = cache.get(
        "_model_meta",
        {},
    )

    model_number = int(
        meta.get(
            "model_number",
            0,
        )
        or 0
    )

    if model_number <= 0:
        return None

    strong_records = (
        _strong_strategy_records(
            cache
        )
    )

    return {
        "model_number": (
            model_number
        ),
        "built_at": str(
            meta.get(
                "built_at",
                "",
            )
            or ""
        ),
        "market_count": int(
            meta.get(
                "market_count",
                0,
            )
            or 0
        ),
        "strong_strategies": len(
            strong_records
        ),
        "strong_strategy_keys": [
            item["key"]
            for item in strong_records
        ],
        "strong_strategy_details": (
            strong_records
        ),
        "appeared_strategies": [],
        "disappeared_strategies": [],
    }


def _with_model_changes(
    record: dict[str, object],
    previous: dict[str, object] | None,
) -> dict[str, object]:
    if previous is None:
        return record

    current_keys = set(
        record.get(
            "strong_strategy_keys",
            [],
        )
    )

    previous_keys = set(
        previous.get(
            "strong_strategy_keys",
            [],
        )
    )

    record[
        "appeared_strategies"
    ] = sorted(
        current_keys
        - previous_keys
    )

    record[
        "disappeared_strategies"
    ] = sorted(
        previous_keys
        - current_keys
    )

    return record


def _ensure_model_history(
    payload,
    cache,
) -> tuple[
    list[dict[str, object]],
    bool,
]:
    history = [
        dict(record)
        for record in payload.get(
            "model_history",
            [],
        )
        if isinstance(
            record,
            dict,
        )
    ]

    current = (
        _model_record_from_cache(
            cache
        )
    )

    if current is None:
        return history, False

    current_number = int(
        current["model_number"]
    )

    if any(
        int(
            record.get(
                "model_number",
                0,
            )
            or 0
        )
        == current_number
        for record in history
    ):
        return history, False

    previous = (
        history[-1]
        if history
        else None
    )

    current = _with_model_changes(
        current,
        previous,
    )

    history.append(current)

    return history, True


def read_model_history(
    cache_path: str | Path,
) -> list[dict[str, object]]:
    """
    Read persisted model history.

    Old v2 cache files are migrated in place by recording the
    currently cached model as the first baseline version.
    """

    path = Path(cache_path)
    payload = _read_payload(path)

    if payload is None:
        return []

    cache = payload.get(
        "cache",
        {},
    )

    history, changed = (
        _ensure_model_history(
            payload,
            cache,
        )
    )

    cache["_model_history"] = (
        history
    )

    if changed or (
        payload.get(
            "model_history"
        )
        != history
    ):
        payload[
            "model_history"
        ] = history
        payload["cache"] = cache

        _write_payload(
            path,
            payload,
        )

    return history


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

    if (
        payload.get("version")
        != CACHE_VERSION
    ):
        return None

    current_signature = (
        _historical_signature(
            connection,
            scenarios_path,
        )
    )

    if (
        payload.get("signature")
        != current_signature
    ):
        return None

    cache = payload.get(
        "cache",
        {},
    )

    history, changed = (
        _ensure_model_history(
            payload,
            cache,
        )
    )

    cache["_model_history"] = (
        history
    )

    if changed:
        payload[
            "model_history"
        ] = history
        payload["cache"] = cache

        _write_payload(
            path,
            payload,
        )

    return cache


def count_model_pending_markets(
    connection,
    cache,
) -> int:
    """Settled model-eligible markets added since model build."""

    metadata = cache.get(
        "_model_meta",
        {},
    )

    model_count = int(
        metadata.get(
            "market_count",
            0,
        )
    )

    current_count = (
        _model_market_count(
            connection
        )
    )

    return max(
        0,
        current_count
        - model_count,
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

    signature = (
        _historical_signature(
            connection,
            scenarios_path,
        )
    )

    started = time.perf_counter()

    old_payload = _read_payload(
        path
    )

    previous_model_number = 0
    history: list[
        dict[str, object]
    ] = []

    if old_payload is not None:
        old_cache = old_payload.get(
            "cache",
            {},
        )

        previous_model_number = int(
            old_cache
            .get(
                "_model_meta",
                {},
            )
            .get(
                "model_number",
                0,
            )
        )

        history, history_changed = (
            _ensure_model_history(
                old_payload,
                old_cache,
            )
        )

        old_cache[
            "_model_history"
        ] = history

        if (
            not force_rebuild
            and old_payload.get(
                "version"
            )
            == CACHE_VERSION
            and old_payload.get(
                "signature"
            )
            == signature
        ):
            if history_changed:
                old_payload[
                    "model_history"
                ] = history
                old_payload[
                    "cache"
                ] = old_cache

                _write_payload(
                    path,
                    old_payload,
                )

            return (
                old_cache,
                "loaded from disk",
                time.perf_counter()
                - started,
            )

    cache = builder(
        connection,
        scenarios_path,
    )

    model_number = (
        previous_model_number + 1
    )

    strong_records = (
        _strong_strategy_records(
            cache
        )
    )

    built_at = (
        datetime.now(
            timezone.utc
        )
        .replace(
            microsecond=0
        )
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )

    cache["_model_meta"] = {
        "model_number": (
            model_number
        ),
        "built_at": built_at,
        "market_count": (
            _model_market_count(
                connection
            )
        ),
        "strong_strategies": (
            len(strong_records)
        ),
    }

    current_record = (
        _model_record_from_cache(
            cache
        )
    )

    if current_record is not None:
        previous = (
            history[-1]
            if history
            else None
        )

        current_record = (
            _with_model_changes(
                current_record,
                previous,
            )
        )

        history.append(
            current_record
        )

    cache["_model_history"] = (
        history
    )

    payload = {
        "version": CACHE_VERSION,
        "signature": signature,
        "cache": cache,
        "model_history": history,
    }

    _write_payload(
        path,
        payload,
    )

    return (
        cache,
        "rebuilt and saved",
        time.perf_counter()
        - started,
    )
