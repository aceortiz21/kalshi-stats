from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict

from .database import (
    connect,
    init_db,
)
from .kalshi_account import (
    KalshiAccountClient,
)


def _number(
    value,
    default=0.0,
):
    if value is None:
        return float(
            default
        )

    return float(
        value
    )


def _ticker(
    item,
):
    return str(
        item.get(
            "market_ticker"
        )
        or item.get(
            "ticker"
        )
    )


def insert_fills(
    connection,
    fills,
):
    for fill in fills:
        fill_id = fill.get(
            "fill_id"
        )

        if not fill_id:
            continue

        connection.execute(
            """
            INSERT OR REPLACE INTO account_fills (
                fill_id,
                trade_id,
                order_id,
                market_ticker,
                side,
                action,
                count,
                yes_price,
                no_price,
                fee_cost,
                is_taker,
                created_time,
                ts,
                subaccount_number,
                raw_json
            )
            VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?
            )
            """,
            (
                str(fill_id),

                fill.get(
                    "trade_id"
                ),

                fill.get(
                    "order_id"
                ),

                _ticker(
                    fill
                ),

                fill.get(
                    "side"
                )
                or fill.get(
                    "outcome_side"
                ),

                fill.get(
                    "action"
                ),

                _number(
                    fill.get(
                        "count_fp"
                    )
                ),

                (
                    None
                    if fill.get(
                        "yes_price_dollars"
                    )
                    is None
                    else _number(
                        fill[
                            "yes_price_dollars"
                        ]
                    )
                ),

                (
                    None
                    if fill.get(
                        "no_price_dollars"
                    )
                    is None
                    else _number(
                        fill[
                            "no_price_dollars"
                        ]
                    )
                ),

                _number(
                    fill.get(
                        "fee_cost"
                    )
                ),

                (
                    None
                    if fill.get(
                        "is_taker"
                    )
                    is None
                    else int(
                        bool(
                            fill[
                                "is_taker"
                            ]
                        )
                    )
                ),

                fill.get(
                    "created_time"
                ),

                fill.get(
                    "ts"
                ),

                int(
                    fill.get(
                        "subaccount_number",
                        0,
                    )
                    or 0
                ),

                json.dumps(
                    fill,
                    sort_keys=True,
                ),
            ),
        )


def insert_settlements(
    connection,
    settlements,
):
    for item in settlements:
        ticker = _ticker(
            item
        )

        settled_time = item.get(
            "settled_time"
        )

        if (
            not ticker
            or not settled_time
        ):
            continue

        connection.execute(
            """
            INSERT OR REPLACE INTO
            account_settlements (
                market_ticker,
                settled_time,
                subaccount_number,

                event_ticker,
                market_result,

                yes_count,
                yes_total_cost,

                no_count,
                no_total_cost,

                revenue_cents,
                value_cents,

                fee_cost,
                raw_json
            )
            VALUES (
                ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?
            )
            """,
            (
                ticker,
                settled_time,

                int(
                    item.get(
                        "subaccount_number",
                        0,
                    )
                    or 0
                ),

                item.get(
                    "event_ticker"
                ),

                item.get(
                    "market_result"
                ),

                _number(
                    item.get(
                        "yes_count_fp"
                    )
                ),

                _number(
                    item.get(
                        "yes_total_cost_dollars"
                    )
                ),

                _number(
                    item.get(
                        "no_count_fp"
                    )
                ),

                _number(
                    item.get(
                        "no_total_cost_dollars"
                    )
                ),

                item.get(
                    "revenue"
                ),

                item.get(
                    "value"
                ),

                _number(
                    item.get(
                        "fee_cost"
                    )
                ),

                json.dumps(
                    item,
                    sort_keys=True,
                ),
            ),
        )


def replace_positions(
    connection,
    positions,
    *,
    subaccount=0,
    collected_at_ms,
):
    connection.execute(
        """
        DELETE FROM account_positions
        WHERE subaccount_number = ?
        """,
        (
            int(subaccount),
        ),
    )

    for item in positions:
        ticker = _ticker(
            item
        )

        if not ticker:
            continue

        connection.execute(
            """
            INSERT INTO account_positions (
                market_ticker,
                subaccount_number,

                position,
                total_traded,
                market_exposure,

                realized_pnl,
                fees_paid,

                resting_orders_count,

                last_updated_ts,
                collected_at_ms,

                raw_json
            )
            VALUES (
                ?, ?,
                ?, ?, ?,
                ?, ?,
                ?,
                ?, ?,
                ?
            )
            """,
            (
                ticker,
                int(
                    subaccount
                ),

                _number(
                    item.get(
                        "position_fp"
                    )
                ),

                _number(
                    item.get(
                        "total_traded_dollars"
                    )
                ),

                _number(
                    item.get(
                        "market_exposure_dollars"
                    )
                ),

                _number(
                    item.get(
                        "realized_pnl_dollars"
                    )
                ),

                _number(
                    item.get(
                        "fees_paid_dollars"
                    )
                ),

                int(
                    item.get(
                        "resting_orders_count",
                        0,
                    )
                    or 0
                ),

                item.get(
                    "last_updated_ts"
                ),

                int(
                    collected_at_ms
                ),

                json.dumps(
                    item,
                    sort_keys=True,
                ),
            ),
        )


def insert_balance(
    connection,
    balance,
    *,
    collected_at_ms,
    subaccount=0,
):
    connection.execute(
        """
        INSERT OR REPLACE INTO
        account_balance_snapshots (
            collected_at_ms,
            subaccount_number,
            balance_cents,
            portfolio_value_cents,
            api_updated_ts
        )
        VALUES (
            ?, ?, ?, ?, ?
        )
        """,
        (
            int(
                collected_at_ms
            ),

            int(
                subaccount
            ),

            int(
                balance.get(
                    "balance",
                    0,
                )
            ),

            int(
                balance.get(
                    "portfolio_value",
                    0,
                )
            ),

            balance.get(
                "updated_ts"
            ),
        ),
    )


def canonical_outcome_side(
    fill,
):
    """
    Return the economic outcome acquired by a fill.

    Prefer Kalshi's canonical outcome_side field.

    Legacy equivalence:
      BUY YES  -> YES
      SELL NO  -> YES
      BUY NO   -> NO
      SELL YES -> NO
    """

    raw = {}

    try:
        raw = json.loads(
            fill["raw_json"]
            or "{}"
        )
    except (
        TypeError,
        json.JSONDecodeError,
    ):
        raw = {}

    canonical = raw.get(
        "outcome_side"
    )

    if canonical in {
        "yes",
        "no",
    }:
        return canonical

    side = str(
        fill["side"]
    ).lower()

    action = str(
        fill["action"]
    ).lower()

    if action == "buy":
        return side

    if action == "sell":
        if side == "yes":
            return "no"

        if side == "no":
            return "yes"

    raise ValueError(
        "Unknown Kalshi fill direction: "
        f"action={action!r}, "
        f"side={side!r}"
    )


def fill_acquisition_cost(
    fill,
):
    outcome = (
        canonical_outcome_side(
            fill
        )
    )

    count = float(
        fill["count"]
    )

    if outcome == "yes":
        price = fill[
            "yes_price"
        ]

    else:
        price = fill[
            "no_price"
        ]

    if price is None:
        raise ValueError(
            "Missing price for "
            f"{outcome} outcome"
        )

    return (
        count
        * float(price)
    )


def reconstruct_market_pnl(
    fills,
    settlement,
):
    """
    Reconstruct one settled binary market.

    Every fill acquires YES or NO exposure.

    A matched YES + NO pair is worth exactly $1.
    Any remaining position is handled by Kalshi's
    authoritative settlement revenue.
    """

    yes_inventory = 0.0
    no_inventory = 0.0

    total_cost = 0.0
    paired_payout = 0.0

    ordered = sorted(
        fills,
        key=lambda fill: (
            int(
                fill["ts"]
                or 0
            ),
            str(
                fill[
                    "created_time"
                ]
                or ""
            ),
            str(
                fill[
                    "fill_id"
                ]
                or ""
            ),
        ),
    )

    for fill in ordered:
        outcome = (
            canonical_outcome_side(
                fill
            )
        )

        count = float(
            fill["count"]
        )

        total_cost += (
            fill_acquisition_cost(
                fill
            )
        )

        if outcome == "yes":
            yes_inventory += count
        else:
            no_inventory += count

        # Opposing binary outcomes form a guaranteed
        # $1 pair and economically close each other.
        paired = min(
            yes_inventory,
            no_inventory,
        )

        if paired > 0:
            paired_payout += (
                paired
            )

            yes_inventory -= (
                paired
            )

            no_inventory -= (
                paired
            )

    revenue_cents = (
        settlement[
            "revenue_cents"
        ]
    )

    settlement_payout = (
        0.0
        if revenue_cents is None
        else float(
            revenue_cents
        ) / 100.0
    )

    # For a settled market, settlement fee_cost is
    # Kalshi's aggregate fee amount for that market.
    fees = float(
        settlement[
            "fee_cost"
        ]
        or 0.0
    )

    total_payout = (
        paired_payout
        + settlement_payout
    )

    pnl = (
        total_payout
        - total_cost
        - fees
    )

    return {
        "pnl": pnl,
        "cost": total_cost,
        "payout": total_payout,
        "paired_payout": (
            paired_payout
        ),
        "settlement_payout": (
            settlement_payout
        ),
        "fees": fees,
        "yes_remaining": (
            yes_inventory
        ),
        "no_remaining": (
            no_inventory
        ),
    }


def account_trade_summary(
    connection,
):
    fills = connection.execute(
        """
        SELECT *
        FROM account_fills
        ORDER BY
            COALESCE(ts, 0),
            created_time,
            fill_id
        """
    ).fetchall()

    settlements = (
        connection.execute(
            """
            SELECT *
            FROM account_settlements
            ORDER BY settled_time
            """
        ).fetchall()
    )

    fills_by_market = (
        defaultdict(list)
    )

    for fill in fills:
        fills_by_market[
            str(
                fill[
                    "market_ticker"
                ]
            )
        ].append(fill)

    settlement_by_market = {
        str(
            settlement[
                "market_ticker"
            ]
        ): settlement
        for settlement
        in settlements
    }

    completed = []

    total_net = 0.0
    total_fees = 0.0

    wins = 0
    losses = 0
    breakeven = 0

    for (
        ticker,
        market_fills,
    ) in fills_by_market.items():
        settlement = (
            settlement_by_market.get(
                ticker
            )
        )

        if settlement is None:
            continue

        result = reconstruct_market_pnl(
            market_fills,
            settlement,
        )

        pnl = result[
            "pnl"
        ]

        total_net += pnl
        total_fees += result[
            "fees"
        ]

        if pnl > 1e-9:
            wins += 1

        elif pnl < -1e-9:
            losses += 1

        else:
            breakeven += 1

        completed.append(
            {
                "ticker": ticker,
                "pnl": pnl,
                "fills": len(
                    market_fills
                ),
                **result,
            }
        )

    completed.sort(
        key=lambda item: (
            item["pnl"]
        ),
        reverse=True,
    )

    latest_balance = (
        connection.execute(
            """
            SELECT *
            FROM account_balance_snapshots
            ORDER BY collected_at_ms DESC
            LIMIT 1
            """
        ).fetchone()
    )

    return {
        "fills": len(
            fills
        ),

        "markets_traded": len(
            fills_by_market
        ),

        "settled_markets": len(
            completed
        ),

        "winning_markets": wins,
        "losing_markets": losses,
        "breakeven_markets": (
            breakeven
        ),

        "realized_settled_pnl": (
            total_net
        ),

        "fees": (
            total_fees
        ),

        "completed": (
            completed
        ),

        "latest_balance": (
            latest_balance
        ),
    }



def sync_once(
    connection,
    client,
    *,
    subaccount=0,
    include_historical=True,
):
    current_fills = (
        client.get_fills()
    )

    historical_fills = (
        client.get_historical_fills()
        if include_historical
        else []
    )

    settlements = (
        client.get_settlements()
    )

    positions = (
        client.get_positions(
            subaccount=(
                subaccount
            )
        )
    )

    balance = (
        client.get_balance(
            subaccount=(
                subaccount
            )
        )
    )

    collected_at_ms = int(
        time.time()
        * 1000
    )

    insert_fills(
        connection,
        historical_fills,
    )

    insert_fills(
        connection,
        current_fills,
    )

    insert_settlements(
        connection,
        settlements,
    )

    replace_positions(
        connection,
        positions,
        subaccount=(
            subaccount
        ),
        collected_at_ms=(
            collected_at_ms
        ),
    )

    insert_balance(
        connection,
        balance,
        collected_at_ms=(
            collected_at_ms
        ),
        subaccount=(
            subaccount
        ),
    )

    connection.commit()

    return {
        "current_fills": len(
            current_fills
        ),
        "historical_fills": len(
            historical_fills
        ),
        "settlements": len(
            settlements
        ),
        "positions": len(
            positions
        ),
        "balance": balance,
    }


def print_summary(
    connection,
):
    summary = (
        account_trade_summary(
            connection
        )
    )

    print()
    print(
        "=" * 72
    )

    print(
        "PERSONAL KALSHI LEDGER"
    )

    print(
        "=" * 72
    )

    print(
        "fills:",
        summary[
            "fills"
        ],
    )

    print(
        "markets traded:",
        summary[
            "markets_traded"
        ],
    )

    print(
        "settled markets:",
        summary[
            "settled_markets"
        ],
    )

    print(
        "settled trading P&L:",
        (
            f"${summary['realized_settled_pnl']:+.2f}"
        ),
    )

    print(
        "recorded fees:",
        (
            f"${summary['fees']:.2f}"
        ),
    )

    balance = summary[
        "latest_balance"
    ]

    if balance is not None:
        print(
            "cash balance:",
            (
                f"${balance['balance_cents'] / 100:.2f}"
            ),
        )

        print(
            "portfolio value:",
            (
                f"${balance['portfolio_value_cents'] / 100:.2f}"
            ),
        )

    print()
    print(
        "RECENT / BEST COMPLETED MARKETS"
    )

    for item in summary[
        "completed"
    ][:15]:
        print(
            f"{item['ticker']:<28} "
            f"P&L ${item['pnl']:+.2f} "
            f"fills={item['fills']:<3} "
            f"fees=${item['fees']:.2f}"
        )


def main():
    parser = (
        argparse.ArgumentParser()
    )

    parser.add_argument(
        "--db",
        required=True,
    )

    parser.add_argument(
        "--interval",
        type=float,
        default=10.0,
    )

    parser.add_argument(
        "--subaccount",
        type=int,
        default=0,
    )

    parser.add_argument(
        "--once",
        action="store_true",
    )

    args = parser.parse_args()

    connection = connect(
        args.db
    )

    client = (
        KalshiAccountClient()
    )

    try:
        init_db(
            connection
        )

        if args.once:
            result = sync_once(
                connection,
                client,
                subaccount=(
                    args.subaccount
                ),
            )

            print(
                "Account sync:",
                result,
            )

            print_summary(
                connection
            )

            return

        include_historical = True

        while True:
            try:
                result = sync_once(
                    connection,
                    client,
                    subaccount=(
                        args.subaccount
                    ),
                    include_historical=(
                        include_historical
                    ),
                )

                include_historical = False

                balance = result[
                    "balance"
                ]

                print(
                    "ACCOUNT live | "
                    f"fills="
                    f"{result['current_fills']} | "
                    f"positions="
                    f"{result['positions']} | "
                    f"cash="
                    f"${int(balance.get('balance', 0)) / 100:.2f} | "
                    f"portfolio="
                    f"${int(balance.get('portfolio_value', 0)) / 100:.2f}"
                )

            except Exception as error:
                print(
                    "ACCOUNT sync error | "
                    f"{type(error).__name__}: "
                    f"{error}"
                )

            time.sleep(
                max(
                    2.0,
                    args.interval,
                )
            )

    finally:
        connection.close()


if __name__ == "__main__":
    main()
