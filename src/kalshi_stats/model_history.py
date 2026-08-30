from __future__ import annotations

import argparse

from .dashboard_cache import (
    read_model_history,
)


def _short_key(
    key: str,
) -> str:
    parts = key.split("|")

    if len(parts) != 3:
        return key

    return (
        f"{parts[0]} / "
        f"{parts[1]} / "
        f"{parts[2]}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--cache",
        required=True,
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=10,
    )

    args = parser.parse_args()

    history = read_model_history(
        args.cache
    )

    if not history:
        print(
            "No model history found."
        )
        return

    print()
    print("MODEL HISTORY")
    print("=" * 78)

    for record in history[
        -max(
            1,
            int(args.limit),
        )
        :
    ]:
        print(
            f"v"
            f"{record.get('model_number')} | "
            f"markets="
            f"{record.get('market_count')} | "
            f"STRONG="
            f"{record.get('strong_strategies')} | "
            f"built="
            f"{record.get('built_at')} | "
            f"+"
            f"{len(record.get('appeared_strategies', []))} "
            f"-"
            f"{len(record.get('disappeared_strategies', []))}"
        )

    latest = history[-1]

    appeared = latest.get(
        "appeared_strategies",
        [],
    )

    disappeared = latest.get(
        "disappeared_strategies",
        [],
    )

    print()
    print(
        "LATEST MODEL CHANGES"
    )
    print("=" * 78)

    if not appeared and not disappeared:
        print(
            "Baseline model; no prior "
            "version available for comparison."
            if len(history) == 1
            else
            "No STRONG strategy membership changes."
        )

    if appeared:
        print("Appeared:")

        for key in appeared:
            print(
                "  + "
                + _short_key(
                    str(key)
                )
            )

    if disappeared:
        print("Disappeared:")

        for key in disappeared:
            print(
                "  - "
                + _short_key(
                    str(key)
                )
            )


if __name__ == "__main__":
    main()
