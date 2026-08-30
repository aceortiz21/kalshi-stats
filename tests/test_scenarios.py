from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kalshi_stats.scenarios import load_scenarios


class ScenarioConfigTests(unittest.TestCase):
    def test_load_scenarios_rejects_invalid_occurrence_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "scenarios.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "id": "broken",
                            "name": "Broken",
                            "description": "Invalid mode.",
                            "trigger_price_min": 0.1,
                            "trigger_price_max": 0.2,
                            "occurrence_mode": "bad_mode",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_scenarios(path)

    def test_load_scenarios_accepts_current_config(self) -> None:
        scenarios = load_scenarios("config/scenarios.json")
        self.assertGreaterEqual(len(scenarios), 1)


if __name__ == "__main__":
    unittest.main()
