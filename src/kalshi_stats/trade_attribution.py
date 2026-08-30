from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from .account_sync import (
    canonical_outcome_side,
)


EPSILON = 1e-9

QUALIFIED_TRADED = "QUALIFIED_TRADED"
PASS_TRADED = "PASS_TRADED"
NO_SYSTEM_DATA = "NO_SYSTEM_DATA"


def _iso_to_ms(
    value,
):
    if not value:
        return None

    return int(
        datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00",
            )
        ).timestamp()
        * 1000
    )


def _price_for_side(
    fill,
    side,
):
    if side == "yes":
        return float(
            fill["yes_price"]
        )

    if side == "no":
        return float(
            fill["no_price"]
        )

    raise ValueError(
        f"Unknown side: {side}"
    )


def _entry_classification(
    fill,
):
    feature_ts = fill[
        "feature_market_ts"
    ]

    if feature_ts is None:
        return NO_SYSTEM_DATA

    if int(
        fill[
            "feature_base_qualified"
        ]
        or 0
    ):
        return QUALIFIED_TRADED

    return PASS_TRADED


def _new_session(
    *,
    ticker,
    side,
    fill,
    count,
    cost,
    fee,
):
    fill_ms = (
        fill[
            "feature_fill_ts_ms"
        ]
    )

    if fill_ms is None:
        fill_ms = _iso_to_ms(
            fill[
                "created_time"
            ]
        )

    return {
        "market_ticker": ticker,
        "side": side,

        "entry_fill_id":
            str(
                fill["fill_id"]
            ),

        "entry_time":
            fill[
                "created_time"
            ],

        "entry_ts_ms":
            fill_ms,

        "entry_price":
            _price_for_side(
                fill,
                side,
            ),

        "system_status":
            _entry_classification(
                fill
            ),

        "entry_market_feature_ts":
            fill[
                "feature_market_ts"
            ],

        "entry_seconds_remaining":
            fill[
                "feature_seconds_remaining"
            ],

        "entry_side_bid":
            fill[
                "feature_side_bid"
            ],

        "entry_side_ask":
            fill[
                "feature_side_ask"
            ],

        "entry_count": float(
            count
        ),

        "gross_cost": float(
            cost
        ),

        "fees": float(
            fee
        ),

        "paired_payout": 0.0,
        "settlement_payout": 0.0,

        "close_time": None,
        "close_ts_ms": None,
        "close_reason": None,

        "pnl": None,
    }


def reconstruct_trade_sessions(
    connection,
):
    """
    Reconstruct actual position sessions from Kalshi fills.

    A session begins when net exposure changes from flat
    to YES or NO.

    Adding the same outcome stays in the same session.

    Acquiring the opposite outcome closes existing
    exposure because YES + NO form a $1 binary pair.

    If one fill crosses through flat, the excess starts
    a new session on the opposite side.
    """

    rows = connection.execute(
        """
        SELECT
            fills.*,

            snapshots.fill_ts_ms
                AS feature_fill_ts_ms,

            snapshots.market_feature_ts
                AS feature_market_ts,

            snapshots.seconds_remaining
                AS feature_seconds_remaining,

            snapshots.side_bid
                AS feature_side_bid,

            snapshots.side_ask
                AS feature_side_ask,

            snapshots.base_setup_qualified
                AS feature_base_qualified

        FROM account_fills AS fills

        LEFT JOIN fill_feature_snapshots AS snapshots
          ON snapshots.fill_id = fills.fill_id

        ORDER BY
            fills.market_ticker,
            fills.created_time,
            fills.fill_id
        """
    ).fetchall()

    settlements = {
        str(
            row["market_ticker"]
        ): row

        for row in connection.execute(
            """
            SELECT *
            FROM account_settlements
            """
        ).fetchall()
    }

    by_market = defaultdict(
        list
    )

    for row in rows:
        by_market[
            str(
                row[
                    "market_ticker"
                ]
            )
        ].append(row)

    sessions = []

    for (
        ticker,
        market_fills,
    ) in by_market.items():

        residual_side = None
        residual_count = 0.0
        current = None

        for fill in market_fills:
            side = (
                canonical_outcome_side(
                    fill
                )
            )

            count = float(
                fill["count"]
            )

            if count <= EPSILON:
                continue

            price = (
                _price_for_side(
                    fill,
                    side,
                )
            )

            fee = float(
                fill["fee_cost"]
                or 0.0
            )

            fill_ms = (
                fill[
                    "feature_fill_ts_ms"
                ]
            )

            if fill_ms is None:
                fill_ms = (
                    _iso_to_ms(
                        fill[
                            "created_time"
                        ]
                    )
                )

            # Flat -> new position session.
            if (
                current is None
                or residual_side
                is None
                or residual_count
                <= EPSILON
            ):
                current = _new_session(
                    ticker=ticker,
                    side=side,
                    fill=fill,
                    count=count,
                    cost=(
                        count
                        * price
                    ),
                    fee=fee,
                )

                residual_side = side
                residual_count = (
                    count
                )

                continue

            # Add to existing exposure.
            if side == residual_side:
                current[
                    "entry_count"
                ] += count

                current[
                    "gross_cost"
                ] += (
                    count
                    * price
                )

                current[
                    "fees"
                ] += fee

                residual_count += count

                continue

            # Opposite outcome closes existing exposure.
            close_count = min(
                count,
                residual_count,
            )

            close_fraction = (
                close_count
                / count
            )

            current[
                "gross_cost"
            ] += (
                close_count
                * price
            )

            current[
                "fees"
            ] += (
                fee
                * close_fraction
            )

            current[
                "paired_payout"
            ] += close_count

            residual_count -= (
                close_count
            )

            remaining = (
                count
                - close_count
            )

            if (
                residual_count
                <= EPSILON
            ):
                current[
                    "close_time"
                ] = fill[
                    "created_time"
                ]

                current[
                    "close_ts_ms"
                ] = fill_ms

                current[
                    "close_reason"
                ] = "PAIR"

                current[
                    "pnl"
                ] = (
                    current[
                        "paired_payout"
                    ]
                    - current[
                        "gross_cost"
                    ]
                    - current[
                        "fees"
                    ]
                )

                sessions.append(
                    current
                )

                current = None
                residual_side = None
                residual_count = 0.0

            # A single opposite-side fill can close the
            # old position and immediately flip net exposure.
            if remaining > EPSILON:
                remaining_fee = (
                    fee
                    * (
                        remaining
                        / count
                    )
                )

                current = (
                    _new_session(
                        ticker=ticker,
                        side=side,
                        fill=fill,
                        count=remaining,
                        cost=(
                            remaining
                            * price
                        ),
                        fee=(
                            remaining_fee
                        ),
                    )
                )

                residual_side = (
                    side
                )

                residual_count = (
                    remaining
                )

        # Any residual exposure is resolved by settlement.
        if (
            current is not None
            and residual_count
            > EPSILON
        ):
            settlement = (
                settlements.get(
                    ticker
                )
            )

            if settlement is None:
                current[
                    "close_reason"
                ] = "OPEN"

                sessions.append(
                    current
                )

                continue

            revenue_cents = (
                settlement[
                    "revenue_cents"
                ]
            )

            settlement_payout = (
                0.0
                if revenue_cents
                is None
                else float(
                    revenue_cents
                ) / 100.0
            )

            current[
                "settlement_payout"
            ] = (
                settlement_payout
            )

            current[
                "close_time"
            ] = settlement[
                "settled_time"
            ]

            current[
                "close_ts_ms"
            ] = _iso_to_ms(
                settlement[
                    "settled_time"
                ]
            )

            current[
                "close_reason"
            ] = "SETTLEMENT"

            current[
                "pnl"
            ] = (
                current[
                    "paired_payout"
                ]
                + settlement_payout
                - current[
                    "gross_cost"
                ]
                - current[
                    "fees"
                ]
            )

            sessions.append(
                current
            )

    sessions.sort(
        key=lambda row: (
            row[
                "entry_ts_ms"
            ]
            or 0,
            row[
                "market_ticker"
            ],
        )
    )

    return sessions


def _session_summary(
    sessions,
    status,
):
    selected = [
        row
        for row in sessions
        if row[
            "system_status"
        ] == status
    ]

    closed = [
        row
        for row in selected
        if row["pnl"]
        is not None
    ]

    pnl = sum(
        row["pnl"]
        for row in closed
    )

    wins = sum(
        row["pnl"] > 0
        for row in closed
    )

    losses = sum(
        row["pnl"] < 0
        for row in closed
    )

    return {
        "sessions":
            len(selected),

        "closed":
            len(closed),

        "pnl":
            pnl,

        "avg_pnl":
            (
                pnl
                / len(closed)
                if closed
                else None
            ),

        "wins":
            wins,

        "losses":
            losses,

        "win_rate":
            (
                wins
                / len(closed)
                if closed
                else None
            ),
    }


def build_trade_attribution(
    connection,
):
    sessions = (
        reconstruct_trade_sessions(
            connection
        )
    )

    qualified = (
        _session_summary(
            sessions,
            QUALIFIED_TRADED,
        )
    )

    passed = (
        _session_summary(
            sessions,
            PASS_TRADED,
        )
    )

    no_data = (
        _session_summary(
            sessions,
            NO_SYSTEM_DATA,
        )
    )

    opportunities = (
        connection.execute(
            """
            SELECT *
            FROM prospective_opportunities
            ORDER BY detected_at_ms
            """
        ).fetchall()
    )

    qualified_sessions_by_market_side = (
        defaultdict(list)
    )

    for session in sessions:
        if (
            session[
                "system_status"
            ]
            != QUALIFIED_TRADED
        ):
            continue

        qualified_sessions_by_market_side[
            (
                session[
                    "market_ticker"
                ],
                session[
                    "side"
                ],
            )
        ].append(session)

    opportunity_rows = []

    for opportunity in opportunities:
        key = (
            str(
                opportunity[
                    "market_ticker"
                ]
            ),
            str(
                opportunity[
                    "side"
                ]
            ).lower(),
        )

        matching = (
            qualified_sessions_by_market_side.get(
                key,
                [],
            )
        )

        traded = bool(
            matching
        )

        matched_session = (
            matching[0]
            if matching
            else None
        )

        opportunity_rows.append(
            {
                "opportunity_id":
                    opportunity[
                        "opportunity_id"
                    ],

                "market_ticker":
                    key[0],

                "side":
                    key[1],

                "detected_at_ms":
                    opportunity[
                        "detected_at_ms"
                    ],

                "entry_ask":
                    opportunity[
                        "entry_ask"
                    ],

                "label_status":
                    opportunity[
                        "label_status"
                    ],

                "first_hit":
                    opportunity[
                        "first_hit"
                    ],

                "gross_profit_per_contract":
                    opportunity[
                        "gross_profit_per_contract"
                    ],

                "user_action":
                    (
                        "TRADED"
                        if traded
                        else "SKIPPED"
                    ),

                "matched_session":
                    matched_session,
            }
        )

    traded_opportunities = [
        row
        for row
        in opportunity_rows
        if row[
            "user_action"
        ] == "TRADED"
    ]

    skipped = [
        row
        for row
        in opportunity_rows
        if row[
            "user_action"
        ] == "SKIPPED"
    ]

    labeled_skipped = [
        row
        for row in skipped
        if (
            row[
                "label_status"
            ]
            == "LABELED"
            and row[
                "gross_profit_per_contract"
            ]
            is not None
        )
    ]

    skipped_tp = sum(
        row["first_hit"]
        == "TP"
        for row in labeled_skipped
    )

    skipped_sl = sum(
        row["first_hit"]
        == "SL"
        for row in labeled_skipped
    )

    skipped_avg = (
        sum(
            float(
                row[
                    "gross_profit_per_contract"
                ]
            )
            for row
            in labeled_skipped
        )
        / len(
            labeled_skipped
        )
        if labeled_skipped
        else None
    )

    return {
        "sessions": sessions,

        "qualified_traded":
            qualified,

        "pass_traded":
            passed,

        "no_system_data":
            no_data,

        "opportunities":
            opportunity_rows,

        "qualified_opportunities":
            len(
                opportunity_rows
            ),

        "qualified_and_traded":
            len(
                traded_opportunities
            ),

        "qualified_and_skipped":
            len(
                skipped
            ),

        "skipped_labeled":
            len(
                labeled_skipped
            ),

        "skipped_tp":
            skipped_tp,

        "skipped_sl":
            skipped_sl,

        "skipped_tp_rate":
            (
                skipped_tp
                / len(
                    labeled_skipped
                )
                if labeled_skipped
                else None
            ),

        "skipped_avg_gross_profit":
            skipped_avg,
    }
