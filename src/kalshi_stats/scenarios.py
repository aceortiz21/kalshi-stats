from __future__ import annotations

import json
from pathlib import Path

from .models import ScenarioDefinition


def _validate_scenario(item: dict[str, object]) -> None:
    required = ("id", "name", "description", "trigger_price_min", "trigger_price_max")
    missing = [field for field in required if field not in item]
    if missing:
        raise ValueError(f"Scenario missing required fields: {', '.join(missing)}")

    trigger_min = float(item["trigger_price_min"])
    trigger_max = float(item["trigger_price_max"])
    if not (0.0 <= trigger_min <= 1.0 and 0.0 <= trigger_max <= 1.0 and trigger_min <= trigger_max):
        raise ValueError(f"Scenario {item['id']} has invalid trigger price bounds")

    trade_side = str(item.get("trade_side", "same"))
    if trade_side not in {"same", "opposite"}:
        raise ValueError(f"Scenario {item['id']} has invalid trade_side {trade_side!r}")

    occurrence_mode = str(item.get("occurrence_mode", "first_per_market"))
    if occurrence_mode not in {"first_per_market", "reentry_after_cooldown"}:
        raise ValueError(f"Scenario {item['id']} has invalid occurrence_mode {occurrence_mode!r}")

    cooldown_seconds = int(item.get("cooldown_seconds", 60))
    if cooldown_seconds < 0:
        raise ValueError(f"Scenario {item['id']} has negative cooldown_seconds")

    elapsed_min = int(item.get("elapsed_seconds_min", 0))
    elapsed_max = int(item.get("elapsed_seconds_max", 900))
    remaining_min = int(item.get("seconds_remaining_min", 0))
    remaining_max = int(item.get("seconds_remaining_max", 900))
    if elapsed_min > elapsed_max:
        raise ValueError(f"Scenario {item['id']} has invalid elapsed_seconds bounds")
    if remaining_min > remaining_max:
        raise ValueError(f"Scenario {item['id']} has invalid seconds_remaining bounds")

    targets = [float(target) for target in item.get("targets", [])]
    if any(target < 0.0 or target > 1.0 for target in targets):
        raise ValueError(f"Scenario {item['id']} has out-of-range target price")


def load_scenarios(path: str | Path) -> list[ScenarioDefinition]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for item in data:
        _validate_scenario(item)
    return [
        ScenarioDefinition(
            id=item["id"],
            name=item["name"],
            description=item["description"],
            trigger_price_min=float(item["trigger_price_min"]),
            trigger_price_max=float(item["trigger_price_max"]),
            targets=[float(target) for target in item.get("targets", [])],
            elapsed_seconds_min=int(item.get("elapsed_seconds_min", 0)),
            elapsed_seconds_max=int(item.get("elapsed_seconds_max", 900)),
            seconds_remaining_min=int(item.get("seconds_remaining_min", 0)),
            seconds_remaining_max=int(item.get("seconds_remaining_max", 900)),
            trade_side=item.get("trade_side", "same"),
            occurrence_mode=item.get("occurrence_mode", "first_per_market"),
            cooldown_seconds=int(item.get("cooldown_seconds", 60)),
        )
        for item in data
    ]
