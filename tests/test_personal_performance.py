from kalshi_stats.personal_performance import (
    _market_stats,
    personal_performance_signature,
)


def test_market_stats_profit_and_drawdown():
    rows = [
        {
            "ticker": "A",
            "pnl": 10.0,
            "settled_time":
                "2026-01-01T00:00:00Z",
        },
        {
            "ticker": "B",
            "pnl": -4.0,
            "settled_time":
                "2026-01-01T00:15:00Z",
        },
        {
            "ticker": "C",
            "pnl": -8.0,
            "settled_time":
                "2026-01-01T00:30:00Z",
        },
    ]

    stats = _market_stats(
        rows
    )

    assert stats["markets"] == 3
    assert stats["pnl"] == -2.0
    assert stats["wins"] == 1
    assert stats["losses"] == 2

    # Peak equity = +10.
    # Final equity = -2.
    # Drawdown = 12.
    assert stats[
        "max_drawdown"
    ] == 12.0

    assert round(
        stats[
            "profit_factor"
        ],
        4,
    ) == round(
        10 / 12,
        4,
    )


def test_personal_signature_changes_with_new_evidence():
    base = {
        "cash": 33.73,
        "portfolio_value": 0.0,
        "fees": 36.85,
        "fills": 284,

        "all": {
            "pnl": -107.27,
            "markets": 76,
            "wins": 37,
            "losses": 39,
        },

        "completed": [
            {
                "settled_time":
                    "2026-08-22T22:30:00Z",
            }
        ],

        "prospective": {
            "total": 0,
            "pending": 0,
            "labeled": 0,
            "incomplete": 0,
        },

        "fill_capture": {
            "with_features": 0,
            "qualified": 0,
        },
    }

    changed = {
        **base,

        "prospective": {
            **base[
                "prospective"
            ],
            "total": 1,
            "pending": 1,
        },
    }

    assert (
        personal_performance_signature(
            base
        )
        !=
        personal_performance_signature(
            changed
        )
    )
