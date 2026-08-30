from __future__ import annotations

from .account_sync import (
    account_trade_summary,
)
from .trade_attribution import (
    build_trade_attribution,
)


def _market_stats(rows):
    if not rows:
        return {
            "markets": 0,
            "pnl": 0.0,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "avg_win": None,
            "avg_loss": None,
            "profit_factor": None,
            "max_drawdown": 0.0,
        }

    wins = [
        row
        for row in rows
        if row["pnl"] > 0
    ]

    losses = [
        row
        for row in rows
        if row["pnl"] < 0
    ]

    gross_wins = sum(
        row["pnl"]
        for row in wins
    )

    gross_losses = abs(
        sum(
            row["pnl"]
            for row in losses
        )
    )

    avg_win = (
        gross_wins / len(wins)
        if wins
        else None
    )

    avg_loss = (
        -gross_losses / len(losses)
        if losses
        else None
    )

    profit_factor = (
        gross_wins / gross_losses
        if gross_losses > 0
        else None
    )

    chronological = sorted(
        rows,
        key=lambda row: (
            row.get(
                "settled_time"
            )
            or "",
            row["ticker"],
        ),
    )

    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0

    for row in chronological:
        equity += row["pnl"]

        peak = max(
            peak,
            equity,
        )

        drawdown = (
            peak - equity
        )

        max_drawdown = max(
            max_drawdown,
            drawdown,
        )

    return {
        "markets": len(rows),

        "pnl": sum(
            row["pnl"]
            for row in rows
        ),

        "wins": len(wins),

        "losses": len(losses),

        "win_rate": (
            len(wins)
            / len(rows)
        ),

        "avg_win": avg_win,
        "avg_loss": avg_loss,

        "profit_factor": (
            profit_factor
        ),

        "max_drawdown": (
            max_drawdown
        ),
    }


def build_personal_performance(
    connection,
):
    summary = (
        account_trade_summary(
            connection
        )
    )

    settlement_rows = (
        connection.execute(
            """
            SELECT
                market_ticker,
                settled_time,
                market_result

            FROM account_settlements
            """
        ).fetchall()
    )

    settlement_map = {
        str(
            row["market_ticker"]
        ): {
            "settled_time":
                row["settled_time"],

            "market_result":
                row["market_result"],
        }
        for row in settlement_rows
    }

    completed = []

    for item in summary[
        "completed"
    ]:
        ticker = str(
            item["ticker"]
        )

        settlement = (
            settlement_map.get(
                ticker,
                {},
            )
        )

        completed.append(
            {
                **item,

                "settled_time":
                    settlement.get(
                        "settled_time"
                    ),

                "market_result":
                    settlement.get(
                        "market_result"
                    ),
            }
        )

    completed.sort(
        key=lambda row: (
            row.get(
                "settled_time"
            )
            or "",
            row["ticker"],
        ),
        reverse=True,
    )

    all_stats = _market_stats(
        completed
    )

    btc_rows = [
        row
        for row in completed
        if row["ticker"].startswith(
            "KXBTC15M-"
        )
    ]

    btc_stats = _market_stats(
        btc_rows
    )

    prospective = (
        connection.execute(
            """
            SELECT
                COUNT(*) AS total,

                SUM(
                    CASE
                        WHEN label_status = 'PENDING'
                        THEN 1 ELSE 0
                    END
                ) AS pending,

                SUM(
                    CASE
                        WHEN label_status = 'LABELED'
                        THEN 1 ELSE 0
                    END
                ) AS labeled,

                SUM(
                    CASE
                        WHEN label_status = 'INCOMPLETE'
                        THEN 1 ELSE 0
                    END
                ) AS incomplete

            FROM prospective_opportunities
            """
        ).fetchone()
    )

    fill_capture = (
        connection.execute(
            """
            SELECT
                COUNT(*) AS total,

                SUM(
                    CASE
                        WHEN market_feature_ts IS NOT NULL
                        THEN 1 ELSE 0
                    END
                ) AS with_features,

                SUM(
                    CASE
                        WHEN base_setup_qualified = 1
                        THEN 1 ELSE 0
                    END
                ) AS qualified

            FROM fill_feature_snapshots
            """
        ).fetchone()
    )

    balance = summary[
        "latest_balance"
    ]

    cash = (
        None
        if balance is None
        else float(
            balance[
                "balance_cents"
            ]
        ) / 100.0
    )

    portfolio = (
        None
        if balance is None
        else float(
            balance[
                "portfolio_value_cents"
            ]
        ) / 100.0
    )

    attribution = (
        build_trade_attribution(
            connection
        )
    )

    return {
        "cash": cash,
        "portfolio_value": (
            portfolio
        ),

        "fees": float(
            summary["fees"]
        ),

        "fills": int(
            summary["fills"]
        ),

        "all": all_stats,
        "btc15m": btc_stats,

        "completed": completed,

        "prospective": {
            "total": int(
                prospective["total"]
                or 0
            ),

            "pending": int(
                prospective["pending"]
                or 0
            ),

            "labeled": int(
                prospective["labeled"]
                or 0
            ),

            "incomplete": int(
                prospective["incomplete"]
                or 0
            ),
        },

        "attribution":
            attribution,

        "fill_capture": {
            "total": int(
                fill_capture["total"]
                or 0
            ),

            "with_features": int(
                fill_capture[
                    "with_features"
                ]
                or 0
            ),

            "qualified": int(
                fill_capture[
                    "qualified"
                ]
                or 0
            ),
        },
    }


def personal_performance_signature(
    state,
):
    if not state:
        return None

    all_stats = state[
        "all"
    ]

    prospective = state[
        "prospective"
    ]

    fill_capture = state[
        "fill_capture"
    ]

    attribution = state.get(
        "attribution",
        {},
    )

    latest_time = None

    if state[
        "completed"
    ]:
        latest_time = (
            state[
                "completed"
            ][0].get(
                "settled_time"
            )
        )

    return (
        round(
            all_stats["pnl"],
            4,
        ),

        all_stats["markets"],
        all_stats["wins"],
        all_stats["losses"],

        round(
            state["fees"],
            4,
        ),

        (
            None
            if state["cash"]
            is None
            else round(
                state["cash"],
                2,
            )
        ),

        (
            None
            if state[
                "portfolio_value"
            ]
            is None
            else round(
                state[
                    "portfolio_value"
                ],
                2,
            )
        ),

        prospective["total"],
        prospective["pending"],
        prospective["labeled"],
        prospective[
            "incomplete"
        ],

        fill_capture[
            "with_features"
        ],

        fill_capture[
            "qualified"
        ],

        latest_time,

        attribution.get(
            "qualified_and_traded",
            0,
        ),

        attribution.get(
            "qualified_and_skipped",
            0,
        ),

        attribution
        .get(
            "qualified_traded",
            {},
        )
        .get(
            "closed",
            0,
        ),

        attribution
        .get(
            "pass_traded",
            {},
        )
        .get(
            "closed",
            0,
        ),
    )
