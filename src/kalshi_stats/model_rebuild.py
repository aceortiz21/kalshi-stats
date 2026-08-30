from __future__ import annotations

import argparse

from .cli import _build_historical_dashboard_cache
from .dashboard_cache import (
    load_or_build_historical_cache,
)
from .database import connect, init_db


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--db",
        required=True,
    )

    parser.add_argument(
        "--scenarios",
        required=True,
    )

    parser.add_argument(
        "--cache",
        required=True,
    )

    args = parser.parse_args()

    connection = connect(args.db)

    try:
        init_db(connection)

        cache, status, elapsed = (
            load_or_build_historical_cache(
                connection=connection,
                scenarios_path=args.scenarios,
                cache_path=args.cache,
                builder=(
                    _build_historical_dashboard_cache
                ),
                force_rebuild=True,
            )
        )

        meta = cache.get(
            "_model_meta",
            {},
        )

        print(
            "MODEL REBUILD COMPLETE | "
            f"v{meta.get('model_number', '?')} | "
            f"markets="
            f"{meta.get('market_count', '?')} | "
            f"STRONG="
            f"{meta.get('strong_strategies', '?')} | "
            f"{elapsed:.2f}s | "
            f"{status}"
        )

    finally:
        connection.close()


if __name__ == "__main__":
    main()
