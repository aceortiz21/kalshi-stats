
from types import SimpleNamespace

from kalshi_stats.dashboard_cache import (
    _strong_strategy_records,
)


def test_strong_strategy_records_use_stable_identity():
    strategy = SimpleNamespace(
        id="tp15_sl5",
        name="TP +15c / SL -5c",
    )

    strong = SimpleNamespace(
        validation_status="STRONG",
        price_bucket="80-89c",
        time_bucket="3-5m left",
        strategy=strategy,
    )

    failed = SimpleNamespace(
        validation_status="FAILED",
        price_bucket="70-79c",
        time_bucket="5-10m left",
        strategy=strategy,
    )

    records = _strong_strategy_records(
        {
            "validated_strategies": [
                strong,
                failed,
            ]
        }
    )

    assert records == [
        {
            "key": (
                "80-89c|"
                "3-5m left|"
                "tp15_sl5"
            ),
            "price_bucket": (
                "80-89c"
            ),
            "time_bucket": (
                "3-5m left"
            ),
            "strategy_id": (
                "tp15_sl5"
            ),
            "strategy_name": (
                "TP +15c / SL -5c"
            ),
        }
    ]
