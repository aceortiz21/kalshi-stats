from __future__ import annotations

import argparse
import json
import math
import statistics
import time

from decimal import (
    Decimal,
    ROUND_DOWN,
    ROUND_CEILING,
)

from .database import (
    connect,
    init_db,
)

from .strategy_zoo import (
    EXIT_RULES,
    grid_strategy_key,
    price_band_for,
    time_band_for,
)


DEFAULT_STARTING_CASH = 10.0
DEFAULT_TRADE_NOTIONAL = 1.00

ENTRY_WAIT_MS = 2000

EPSILON = 1e-9


def floor_contract_count(
    notional,
    price,
):
    """
    Kalshi fixed-point contract quantity:
    minimum increment = 0.01 contract.
    """

    price = Decimal(
        str(
            price
        )
    )

    notional = Decimal(
        str(
            notional
        )
    )

    if (
        price <= 0
        or notional <= 0
    ):
        return 0.0

    raw = (
        notional
        / price
    )

    hundredths = (
        raw
        * Decimal("100")
    ).to_integral_value(
        rounding=ROUND_DOWN
    )

    return float(
        hundredths
        / Decimal("100")
    )


def taker_fee_estimate(
    count,
    price,
):
    """
    Standard event-contract trade-fee proxy.

    Kalshi additionally applies balance/rounding
    mechanics to fractional/subpenny fills. We retain
    this fee separately as an estimate until actual
    live fills can provide exchange-reported fee_cost.
    """

    count = Decimal(
        str(
            count
        )
    )

    price = Decimal(
        str(
            price
        )
    )

    if (
        count <= 0
        or price <= 0
        or price >= 1
    ):
        return 0.0

    raw = (
        Decimal("0.07")
        * count
        * price
        * (
            Decimal("1")
            - price
        )
    )

    centicent = Decimal(
        "0.0001"
    )

    rounded = (
        (
            raw
            / centicent
        ).to_integral_value(
            rounding=ROUND_CEILING
        )
        * centicent
    )

    return float(
        rounded
    )


def side_book(
    row,
    side,
):
    side = str(
        side
    ).lower()

    if side not in {
        "yes",
        "no",
    }:
        raise ValueError(
            f"Unknown side: {side}"
        )

    return {
        "bid":
            float(
                row[
                    f"{side}_bid"
                ]
            ),

        "bid_size":
            (
                None
                if row[
                    f"{side}_bid_size"
                ]
                is None
                else float(
                    row[
                        f"{side}_bid_size"
                    ]
                )
            ),

        "ask":
            float(
                row[
                    f"{side}_ask"
                ]
            ),

        "ask_size":
            (
                None
                if row[
                    f"{side}_ask_size"
                ]
                is None
                else float(
                    row[
                        f"{side}_ask_size"
                    ]
                )
            ),
    }


def fill_adjusted_exit_prices(
    trade,
    fill_price,
):
    """
    For delta-based strategies, TP/SL must be
    calculated from the ACTUAL simulated fill,
    not the earlier signal/reference quote.

    Micro multiplier targets remain absolute.
    Settlement strategies have no TP/SL.
    """

    family = str(
        trade[
            "family"
        ]
    )

    original_entry = float(
        trade[
            "entry_limit"
        ]
    )

    tp = (
        None
        if trade[
            "tp_price"
        ]
        is None
        else float(
            trade[
                "tp_price"
            ]
        )
    )

    sl = (
        None
        if trade[
            "sl_price"
        ]
        is None
        else float(
            trade[
                "sl_price"
            ]
        )
    )

    delta_based = family in {
        "MAIN_TRIGGER",
        "MAIN_CONTEXT",
        "GRID_V1",
    }

    if not delta_based:
        return tp, sl

    if tp is not None:
        tp_delta = (
            tp
            - original_entry
        )

        tp = min(
            1.0,
            float(
                fill_price
            )
            + tp_delta,
        )

    if sl is not None:
        sl_delta = (
            original_entry
            - sl
        )

        sl = max(
            0.0,
            float(
                fill_price
            )
            - sl_delta,
        )

    return tp, sl



def ensure_accounts(
    connection,
    *,
    now_ms,
    starting_cash,
    trade_notional,
):
    strategies = connection.execute(
        """
        SELECT strategy_key

        FROM shadow_strategy_registry

        WHERE enabled = 1
        """
    ).fetchall()

    inserted = 0

    for strategy in strategies:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO
            paper_accounts (
                strategy_key,

                starting_cash,
                cash,
                realized_pnl,

                trade_notional,

                created_at_ms,
                updated_at_ms,

                enabled
            )
            VALUES (
                ?,
                ?,
                ?,
                0,
                ?,
                ?,
                ?,
                1
            )
            """,
            (
                strategy[
                    "strategy_key"
                ],

                float(
                    starting_cash
                ),

                float(
                    starting_cash
                ),

                float(
                    trade_notional
                ),

                int(
                    now_ms
                ),

                int(
                    now_ms
                ),
            ),
        )

        inserted += max(
            0,
            int(
                cursor.rowcount
                or 0
            ),
        )

    return inserted


def insert_signal(
    connection,
    *,
    strategy_key,
    family,
    signal_key,
    market_ticker,
    side,
    signal_ts_ms,
    entry_limit,
    tp_price,
    sl_price,
    now_ms,
):
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO
        paper_trades (
            strategy_key,
            signal_key,

            family,

            market_ticker,
            side,

            signal_ts_ms,

            entry_limit,

            tp_price,
            sl_price,

            created_at_ms,
            updated_at_ms
        )
        VALUES (
            ?, ?,
            ?,
            ?, ?,
            ?,
            ?,
            ?, ?,
            ?, ?
        )
        """,
        (
            strategy_key,
            signal_key,

            family,

            market_ticker,
            side,

            int(
                signal_ts_ms
            ),

            float(
                entry_limit
            ),

            (
                None
                if tp_price is None
                else float(
                    tp_price
                )
            ),

            (
                None
                if sl_price is None
                else float(
                    sl_price
                )
            ),

            int(
                now_ms
            ),

            int(
                now_ms
            ),
        ),
    )

    return max(
        0,
        int(
            cursor.rowcount
            or 0
        ),
    )


def discover_main_signals(
    connection,
    registry,
    account,
    *,
    now_ms,
):
    definition = json.loads(
        registry[
            "definition_json"
        ]
    )

    profile_id = str(
        definition[
            "profile_id"
        ]
    )

    start_ms = max(
        int(
            registry[
                "shadow_start_ms"
            ]
        ),
        int(
            account[
                "created_at_ms"
            ]
        ),
    )

    rows = connection.execute(
        """
        SELECT *
        FROM main_trigger_confirmations

        WHERE profile_id = ?
          AND status = 'CONFIRMED'
          AND confirmed_at_ms >= ?
          AND confirmed_at_ms <= ?

        ORDER BY confirmed_at_ms
        """,
        (
            profile_id,
            start_ms,
            int(
                now_ms
            ),
        ),
    ).fetchall()

    inserted = 0

    entry_low = definition.get(
        "entry_low"
    )

    entry_high = definition.get(
        "entry_high"
    )

    seconds_low = definition.get(
        "seconds_low"
    )

    seconds_high = definition.get(
        "seconds_high"
    )

    for row in rows:
        entry = float(
            row[
                "entry_ask"
            ]
        )

        seconds = float(
            row[
                "seconds_remaining"
            ]
        )

        if (
            entry_low is not None
            and entry
            < float(
                entry_low
            )
        ):
            continue

        if (
            entry_high is not None
            and entry
            > float(
                entry_high
            )
        ):
            continue

        if (
            seconds_low is not None
            and seconds
            < float(
                seconds_low
            )
        ):
            continue

        if (
            seconds_high is not None
            and seconds
            > float(
                seconds_high
            )
        ):
            continue

        inserted += insert_signal(
            connection,

            strategy_key=(
                registry[
                    "strategy_key"
                ]
            ),

            family=(
                registry[
                    "family"
                ]
            ),

            signal_key=(
                "confirmation:"
                + str(
                    row[
                        "confirmation_id"
                    ]
                )
            ),

            market_ticker=(
                row[
                    "market_ticker"
                ]
            ),

            side=(
                row[
                    "side"
                ]
            ),

            signal_ts_ms=(
                row[
                    "confirmed_at_ms"
                ]
            ),

            entry_limit=entry,

            tp_price=(
                row[
                    "tp_price"
                ]
            ),

            sl_price=(
                row[
                    "sl_price"
                ]
            ),

            now_ms=now_ms,
        )

    return inserted


def discover_micro_signals(
    connection,
    registry,
    account,
    *,
    now_ms,
):
    definition = json.loads(
        registry[
            "definition_json"
        ]
    )

    entry_key = int(
        definition[
            "entry_price_key"
        ]
    )

    bucket = str(
        definition[
            "time_bucket"
        ]
    )

    target_key = int(
        definition[
            "target_price_key"
        ]
    )

    start_ms = max(
        int(
            registry[
                "shadow_start_ms"
            ]
        ),
        int(
            account[
                "created_at_ms"
            ]
        ),
    )

    rows = connection.execute(
        """
        SELECT
            opportunities.*,
            targets.target_price

        FROM micro_multiplier_opportunities
            AS opportunities

        JOIN micro_multiplier_targets
            AS targets

          ON targets.micro_opportunity_id
             =
             opportunities.micro_opportunity_id

        WHERE
            opportunities.entry_price_key = ?

          AND opportunities.time_bucket = ?

          AND CAST(
                ROUND(
                    targets.target_price
                    * 1000
                )
                AS INTEGER
              ) = ?

          AND opportunities.detected_at_ms
              >= ?

          AND opportunities.detected_at_ms
              <= ?

        ORDER BY
            opportunities.detected_at_ms,
            opportunities.micro_opportunity_id
        """,
        (
            entry_key,
            bucket,
            target_key,
            start_ms,
            int(
                now_ms
            ),
        ),
    ).fetchall()

    inserted = 0

    for row in rows:
        inserted += insert_signal(
            connection,

            strategy_key=(
                registry[
                    "strategy_key"
                ]
            ),

            family=(
                registry[
                    "family"
                ]
            ),

            signal_key=(
                "micro:"
                + str(
                    row[
                        "micro_opportunity_id"
                    ]
                )
                + ":"
                + str(
                    target_key
                )
            ),

            market_ticker=(
                row[
                    "market_ticker"
                ]
            ),

            side=(
                row[
                    "side"
                ]
            ),

            signal_ts_ms=(
                row[
                    "detected_at_ms"
                ]
            ),

            entry_limit=(
                row[
                    "entry_ask"
                ]
            ),

            tp_price=(
                row[
                    "target_price"
                ]
            ),

            # Micro strategy has no stop.
            sl_price=None,

            now_ms=now_ms,
        )

    return inserted


def discover_grid_signals(
    connection,
    *,
    now_ms,
):
    """
    Generate one forward paper entry per
    strategy / market / side.

    The source is the synchronized 1-second
    market_feature_snapshots table.
    """

    registry_rows = connection.execute(
        """
        SELECT
            registry.strategy_key,
            registry.shadow_start_ms,

            accounts.created_at_ms
                AS account_created_at_ms

        FROM shadow_strategy_registry
            AS registry

        JOIN paper_accounts
            AS accounts

          ON accounts.strategy_key
             =
             registry.strategy_key

        WHERE registry.family = 'GRID_V1'
          AND registry.enabled = 1
          AND accounts.enabled = 1
        """
    ).fetchall()

    if not registry_rows:
        return 0

    allowed = {}

    for row in registry_rows:
        allowed[
            str(
                row[
                    "strategy_key"
                ]
            )
        ] = max(
            int(
                row[
                    "shadow_start_ms"
                ]
            ),

            int(
                row[
                    "account_created_at_ms"
                ]
            ),
        )

    minimum_start = min(
        allowed.values()
    )

    last_signal = connection.execute(
        """
        SELECT MAX(
            signal_ts_ms
        )

        FROM paper_trades

        WHERE family = 'GRID_V1'
        """
    ).fetchone()[0]

    scan_start = (
        minimum_start
        if last_signal is None
        else max(
            minimum_start,
            int(
                last_signal
            ),
        )
    )

    snapshots = connection.execute(
        """
        SELECT *

        FROM market_feature_snapshots

        WHERE ts >= ?
          AND ts <= ?

        ORDER BY ts
        """,
        (
            int(
                scan_start
            ),

            int(
                now_ms
            ),
        ),
    ).fetchall()

    inserted = 0

    for snapshot in snapshots:
        ts_ms = int(
            snapshot[
                "ts"
            ]
        )

        timing = time_band_for(
            snapshot[
                "seconds_remaining"
            ]
        )

        if timing is None:
            continue

        time_name = timing[0]

        for side in (
            "yes",
            "no",
        ):
            entry = float(
                snapshot[
                    f"{side}_ask"
                ]
            )

            pricing = price_band_for(
                entry
            )

            if pricing is None:
                continue

            price_name = pricing[0]

            for rule in EXIT_RULES:
                strategy_key = (
                    grid_strategy_key(
                        price_name,
                        time_name,
                        rule[
                            "id"
                        ],
                    )
                )

                start_ms = allowed.get(
                    strategy_key
                )

                if start_ms is None:
                    continue

                if ts_ms < start_ms:
                    continue

                tp_delta = rule[
                    "tp_delta"
                ]

                sl_delta = rule[
                    "sl_delta"
                ]

                if tp_delta is None:
                    tp_price = None
                    sl_price = None

                else:
                    tp_price = (
                        entry
                        + float(
                            tp_delta
                        )
                    )

                    sl_price = (
                        entry
                        - float(
                            sl_delta
                        )
                    )

                    # Preserve the intended reward/risk
                    # geometry rather than clipping it.
                    if (
                        tp_price > .99
                        or sl_price < .01
                    ):
                        continue

                inserted += insert_signal(
                    connection,

                    strategy_key=(
                        strategy_key
                    ),

                    family="GRID_V1",

                    signal_key=(
                        f"grid:"
                        f"{snapshot['market_ticker']}:"
                        f"{side}"
                    ),

                    market_ticker=(
                        snapshot[
                            "market_ticker"
                        ]
                    ),

                    side=side,

                    signal_ts_ms=ts_ms,

                    entry_limit=entry,

                    tp_price=tp_price,
                    sl_price=sl_price,

                    now_ms=now_ms,
                )

    return inserted



def discover_signals(
    connection,
    *,
    now_ms,
):
    rows = connection.execute(
        """
        SELECT
            registry.*,

            accounts.created_at_ms
                AS account_created_at_ms,

            accounts.trade_notional,

            accounts.cash

        FROM shadow_strategy_registry
            AS registry

        JOIN paper_accounts
            AS accounts

          ON accounts.strategy_key
             =
             registry.strategy_key

        WHERE registry.enabled = 1
          AND accounts.enabled = 1
        """
    ).fetchall()

    inserted = 0
    grid_present = False

    for registry in rows:
        account = {
            "created_at_ms":
                registry[
                    "account_created_at_ms"
                ],
        }

        family = str(
            registry[
                "family"
            ]
        )

        if family in {
            "MAIN_TRIGGER",
            "MAIN_CONTEXT",
        }:
            inserted += (
                discover_main_signals(
                    connection,
                    registry,
                    account,
                    now_ms=now_ms,
                )
            )

        elif family in {
            "MICRO_MULTIPLIER",
            "MICRO_LIVE_DISCOVERY",
        }:
            inserted += (
                discover_micro_signals(
                    connection,
                    registry,
                    account,
                    now_ms=now_ms,
                )
            )

        elif family == "GRID_V1":
            grid_present = True

    if grid_present:
        inserted += (
            discover_grid_signals(
                connection,
                now_ms=now_ms,
            )
        )

    return inserted


def first_entry_book(
    connection,
    trade,
):
    return connection.execute(
        """
        SELECT *
        FROM topbook_snapshots

        WHERE market_ticker = ?
          AND ts_ms >= ?
          AND ts_ms <= ?

        ORDER BY ts_ms
        LIMIT 1
        """,
        (
            trade[
                "market_ticker"
            ],

            int(
                trade[
                    "signal_ts_ms"
                ]
            ),

            int(
                trade[
                    "signal_ts_ms"
                ]
            )
            + ENTRY_WAIT_MS,
        ),
    ).fetchone()


def record_fill(
    connection,
    *,
    paper_trade_id,
    leg,
    ts_ms,
    price,
    count,
    fee,
    liquidity,
    reason,
):
    connection.execute(
        """
        INSERT INTO paper_fills (
            paper_trade_id,

            leg,

            ts_ms,

            price,
            count,
            notional,

            fee,

            liquidity,
            reason
        )
        VALUES (
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?
        )
        """,
        (
            paper_trade_id,

            leg,

            int(
                ts_ms
            ),

            float(
                price
            ),

            float(
                count
            ),

            float(
                price
            )
            * float(
                count
            ),

            float(
                fee
            ),

            liquidity,
            reason,
        ),
    )


def process_entries(
    connection,
    *,
    now_ms,
):
    trades = connection.execute(
        """
        SELECT
            trades.*,

            accounts.cash,
            accounts.trade_notional

        FROM paper_trades
            AS trades

        JOIN paper_accounts
            AS accounts

          ON accounts.strategy_key
             =
             trades.strategy_key

        WHERE trades.state
              = 'WAITING_ENTRY'

        ORDER BY trades.signal_ts_ms
        """
    ).fetchall()

    changed = 0

    for trade in trades:
        book_row = first_entry_book(
            connection,
            trade,
        )

        deadline = (
            int(
                trade[
                    "signal_ts_ms"
                ]
            )
            + ENTRY_WAIT_MS
        )

        if book_row is None:
            if now_ms <= deadline:
                continue

            connection.execute(
                """
                UPDATE paper_trades

                SET
                    state = 'NO_FILL',
                    entry_status = 'NO_BOOK',
                    updated_at_ms = ?

                WHERE paper_trade_id = ?
                """,
                (
                    int(
                        now_ms
                    ),

                    trade[
                        "paper_trade_id"
                    ],
                ),
            )

            changed += 1
            continue

        book = side_book(
            book_row,
            trade[
                "side"
            ],
        )

        ask = book[
            "ask"
        ]

        ask_size = book[
            "ask_size"
        ]

        # Model a real IOC limit order.
        if (
            ask_size is None
            or ask_size <= 0
            or ask
            > float(
                trade[
                    "entry_limit"
                ]
            )
        ):
            connection.execute(
                """
                UPDATE paper_trades

                SET
                    state = 'NO_FILL',
                    entry_status = 'IOC_NO_FILL',

                    requested_count = 0,

                    entry_done_ts_ms = ?,
                    last_book_ts_ms = ?,
                    updated_at_ms = ?

                WHERE paper_trade_id = ?
                """,
                (
                    int(
                        book_row[
                            "ts_ms"
                        ]
                    ),

                    int(
                        book_row[
                            "ts_ms"
                        ]
                    ),

                    int(
                        now_ms
                    ),

                    trade[
                        "paper_trade_id"
                    ],
                ),
            )

            changed += 1
            continue

        usable_cash = float(
            trade[
                "cash"
            ]
        )

        requested_notional = min(
            usable_cash,
            float(
                trade[
                    "trade_notional"
                ]
            ),
        )

        requested_count = (
            floor_contract_count(
                requested_notional,
                ask,
            )
        )

        if requested_count < 0.01:
            connection.execute(
                """
                UPDATE paper_trades

                SET
                    state = 'NO_CAPITAL',
                    entry_status = 'NO_CAPITAL',

                    requested_count = ?,

                    entry_done_ts_ms = ?,
                    last_book_ts_ms = ?,
                    updated_at_ms = ?

                WHERE paper_trade_id = ?
                """,
                (
                    requested_count,

                    int(
                        book_row[
                            "ts_ms"
                        ]
                    ),

                    int(
                        book_row[
                            "ts_ms"
                        ]
                    ),

                    int(
                        now_ms
                    ),

                    trade[
                        "paper_trade_id"
                    ],
                ),
            )

            changed += 1
            continue

        fill_count = min(
            requested_count,
            float(
                ask_size
            ),
        )

        # Exchange quantity granularity.
        fill_count = (
            math.floor(
                (
                    fill_count
                    + EPSILON
                )
                * 100
            )
            / 100.0
        )

        if fill_count < 0.01:
            connection.execute(
                """
                UPDATE paper_trades

                SET
                    state = 'NO_FILL',
                    entry_status = 'IOC_NO_FILL',

                    requested_count = ?,

                    entry_done_ts_ms = ?,
                    last_book_ts_ms = ?,
                    updated_at_ms = ?

                WHERE paper_trade_id = ?
                """,
                (
                    requested_count,

                    int(
                        book_row[
                            "ts_ms"
                        ]
                    ),

                    int(
                        book_row[
                            "ts_ms"
                        ]
                    ),

                    int(
                        now_ms
                    ),

                    trade[
                        "paper_trade_id"
                    ],
                ),
            )

            changed += 1
            continue

        cost = (
            fill_count
            * ask
        )

        fee = taker_fee_estimate(
            fill_count,
            ask,
        )

        # This should rarely matter with a $10 paper
        # account, but never allow paper cash negative.
        while (
            fill_count >= 0.01
            and cost
            + fee
            > usable_cash
            + EPSILON
        ):
            fill_count = round(
                fill_count
                - 0.01,
                2,
            )

            cost = (
                fill_count
                * ask
            )

            fee = taker_fee_estimate(
                fill_count,
                ask,
            )

        if fill_count < 0.01:
            connection.execute(
                """
                UPDATE paper_trades

                SET
                    state = 'NO_CAPITAL',
                    entry_status = 'NO_CAPITAL',
                    requested_count = ?,
                    updated_at_ms = ?

                WHERE paper_trade_id = ?
                """,
                (
                    requested_count,

                    int(
                        now_ms
                    ),

                    trade[
                        "paper_trade_id"
                    ],
                ),
            )

            changed += 1
            continue

        effective_tp, effective_sl = (
            fill_adjusted_exit_prices(
                trade,
                ask,
            )
        )

        entry_status = (
            "FILLED"
            if (
                fill_count
                + EPSILON
                >= requested_count
            )
            else "PARTIAL_IOC"
        )

        record_fill(
            connection,

            paper_trade_id=(
                trade[
                    "paper_trade_id"
                ]
            ),

            leg="ENTRY",

            ts_ms=(
                book_row[
                    "ts_ms"
                ]
            ),

            price=ask,
            count=fill_count,
            fee=fee,

            liquidity="TAKER",
            reason="ENTRY_IOC",
        )

        connection.execute(
            """
            UPDATE paper_accounts

            SET
                cash = cash - ?,
                updated_at_ms = ?

            WHERE strategy_key = ?
            """,
            (
                cost
                + fee,

                int(
                    now_ms
                ),

                trade[
                    "strategy_key"
                ],
            ),
        )

        connection.execute(
            """
            UPDATE paper_trades

            SET
                requested_count = ?,

                filled_count = ?,
                remaining_count = ?,

                entry_avg_price = ?,
                entry_notional = ?,
                entry_fee = ?,

                entry_status = ?,

                tp_price = ?,
                sl_price = ?,

                entry_first_fill_ts_ms = ?,
                entry_done_ts_ms = ?,

                last_book_ts_ms = ?,

                state = 'OPEN',
                updated_at_ms = ?

            WHERE paper_trade_id = ?
            """,
            (
                requested_count,

                fill_count,
                fill_count,

                ask,
                cost,
                fee,

                entry_status,

                effective_tp,
                effective_sl,

                int(
                    book_row[
                        "ts_ms"
                    ]
                ),

                int(
                    book_row[
                        "ts_ms"
                    ]
                ),

                int(
                    book_row[
                        "ts_ms"
                    ]
                ),

                int(
                    now_ms
                ),

                trade[
                    "paper_trade_id"
                ],
            ),
        )

        changed += 1

    return changed


def finalize_closed_trade(
    connection,
    trade_id,
    *,
    now_ms,
):
    trade = connection.execute(
        """
        SELECT *
        FROM paper_trades

        WHERE paper_trade_id = ?
        """,
        (
            trade_id,
        ),
    ).fetchone()

    fills = connection.execute(
        """
        SELECT *
        FROM paper_fills

        WHERE paper_trade_id = ?

        ORDER BY ts_ms, paper_fill_id
        """,
        (
            trade_id,
        ),
    ).fetchall()

    entry_notional = sum(
        float(
            row[
                "notional"
            ]
        )
        for row
        in fills
        if row[
            "leg"
        ]
        == "ENTRY"
    )

    exit_notional = sum(
        float(
            row[
                "notional"
            ]
        )
        for row
        in fills
        if row[
            "leg"
        ]
        == "EXIT"
    )

    entry_fee = sum(
        float(
            row[
                "fee"
            ]
        )
        for row
        in fills
        if row[
            "leg"
        ]
        == "ENTRY"
    )

    exit_fee = sum(
        float(
            row[
                "fee"
            ]
        )
        for row
        in fills
        if row[
            "leg"
        ]
        == "EXIT"
    )

    gross_pnl = (
        exit_notional
        - entry_notional
    )

    net_pnl = (
        gross_pnl
        - entry_fee
        - exit_fee
    )

    exit_fills = [
        row
        for row
        in fills
        if row[
            "leg"
        ]
        == "EXIT"
    ]

    total_exit_count = sum(
        float(
            row[
                "count"
            ]
        )
        for row
        in exit_fills
    )

    exit_avg = (
        None
        if total_exit_count <= 0
        else sum(
            float(
                row[
                    "price"
                ]
            )
            * float(
                row[
                    "count"
                ]
            )
            for row
            in exit_fills
        )
        / total_exit_count
    )

    first_exit = (
        None
        if not exit_fills
        else int(
            exit_fills[0][
                "ts_ms"
            ]
        )
    )

    last_exit = (
        int(
            now_ms
        )
        if not exit_fills
        else int(
            exit_fills[-1][
                "ts_ms"
            ]
        )
    )

    reason = (
        None
        if not exit_fills
        else str(
            exit_fills[-1][
                "reason"
            ]
            or ""
        )
    )

    connection.execute(
        """
        UPDATE paper_trades

        SET
            remaining_count = 0,

            exit_avg_price = ?,
            exit_notional = ?,
            exit_fee = ?,

            exit_status = 'FILLED',
            exit_reason = ?,

            exit_first_fill_ts_ms = ?,
            closed_at_ms = ?,

            gross_pnl = ?,
            net_pnl = ?,

            state = 'CLOSED',
            updated_at_ms = ?

        WHERE paper_trade_id = ?
        """,
        (
            exit_avg,
            exit_notional,
            exit_fee,

            reason,

            first_exit,
            last_exit,

            gross_pnl,
            net_pnl,

            int(
                now_ms
            ),

            trade_id,
        ),
    )

    connection.execute(
        """
        UPDATE paper_accounts

        SET
            realized_pnl =
                realized_pnl + ?,

            updated_at_ms = ?

        WHERE strategy_key = ?
        """,
        (
            net_pnl,

            int(
                now_ms
            ),

            trade[
                "strategy_key"
            ],
        ),
    )


def process_open_trades(
    connection,
    *,
    now_ms,
):
    trades = connection.execute(
        """
        SELECT *
        FROM paper_trades

        WHERE state IN (
            'OPEN',
            'STOP_EXIT'
        )

        ORDER BY signal_ts_ms
        """
    ).fetchall()

    changed = 0

    for trade in trades:
        last_ts = int(
            trade[
                "last_book_ts_ms"
            ]
            or trade[
                "entry_done_ts_ms"
            ]
            or trade[
                "signal_ts_ms"
            ]
        )

        book_rows = connection.execute(
            """
            SELECT *
            FROM topbook_snapshots

            WHERE market_ticker = ?
              AND ts_ms > ?
              AND ts_ms <= ?

            ORDER BY ts_ms
            """,
            (
                trade[
                    "market_ticker"
                ],

                last_ts,

                int(
                    now_ms
                ),
            ),
        ).fetchall()

        remaining = float(
            trade[
                "remaining_count"
            ]
        )

        stop_triggered = bool(
            trade[
                "stop_triggered"
            ]
        )

        latest_processed = last_ts

        for book_row in book_rows:
            latest_processed = int(
                book_row[
                    "ts_ms"
                ]
            )

            if remaining <= EPSILON:
                break

            book = side_book(
                book_row,
                trade[
                    "side"
                ],
            )

            bid = book[
                "bid"
            ]

            bid_size = book[
                "bid_size"
            ]

            if (
                bid_size is None
                or bid_size <= 0
            ):
                continue

            reason = None
            price = None
            liquidity = None

            sl_price = (
                None
                if trade[
                    "sl_price"
                ]
                is None
                else float(
                    trade[
                        "sl_price"
                    ]
                )
            )

            tp_price = (
                None
                if trade[
                    "tp_price"
                ]
                is None
                else float(
                    trade[
                        "tp_price"
                    ]
                )
            )

            if (
                stop_triggered
                or (
                    sl_price is not None
                    and bid
                    <= sl_price
                )
            ):
                stop_triggered = True

                reason = "STOP"
                price = bid

                # The live executor would flatten
                # immediately once the stop fires.
                liquidity = "TAKER"

            elif (
                tp_price is not None
                and bid
                >= tp_price
            ):
                reason = "TP"

                # Conservative resting-limit proxy:
                # do not credit TP until an actual buyer
                # exists at/above our target.
                price = tp_price

                liquidity = (
                    "RESTING_LIMIT_PROXY"
                )

            if reason is None:
                continue

            fill_count = min(
                remaining,
                float(
                    bid_size
                ),
            )

            fill_count = (
                math.floor(
                    (
                        fill_count
                        + EPSILON
                    )
                    * 100
                )
                / 100.0
            )

            if fill_count < 0.01:
                continue

            # Conservatively charge taker-rate fee even
            # on the TP proxy until we model exact maker
            # fee eligibility for KXBTC15M.
            fee = taker_fee_estimate(
                fill_count,
                price,
            )

            proceeds = (
                fill_count
                * price
            )

            record_fill(
                connection,

                paper_trade_id=(
                    trade[
                        "paper_trade_id"
                    ]
                ),

                leg="EXIT",

                ts_ms=(
                    book_row[
                        "ts_ms"
                    ]
                ),

                price=price,
                count=fill_count,
                fee=fee,

                liquidity=liquidity,
                reason=reason,
            )

            connection.execute(
                """
                UPDATE paper_accounts

                SET
                    cash =
                        cash + ? - ?,

                    updated_at_ms = ?

                WHERE strategy_key = ?
                """,
                (
                    proceeds,
                    fee,

                    int(
                        now_ms
                    ),

                    trade[
                        "strategy_key"
                    ],
                ),
            )

            remaining -= fill_count

            changed += 1

        state = (
            "STOP_EXIT"
            if (
                stop_triggered
                and remaining
                > EPSILON
            )
            else "OPEN"
        )

        connection.execute(
            """
            UPDATE paper_trades

            SET
                remaining_count = ?,
                stop_triggered = ?,

                last_book_ts_ms = ?,

                state = ?,
                updated_at_ms = ?

            WHERE paper_trade_id = ?
            """,
            (
                max(
                    0.0,
                    remaining
                ),

                int(
                    stop_triggered
                ),

                latest_processed,

                state,

                int(
                    now_ms
                ),

                trade[
                    "paper_trade_id"
                ],
            ),
        )

        if remaining <= EPSILON:
            finalize_closed_trade(
                connection,
                trade[
                    "paper_trade_id"
                ],
                now_ms=now_ms,
            )

    return changed


def settle_positions(
    connection,
    *,
    now_ms,
):
    trades = connection.execute(
        """
        SELECT
            trades.*,
            markets.result

        FROM paper_trades
            AS trades

        JOIN markets
            AS markets

          ON markets.ticker
             =
             trades.market_ticker

        WHERE trades.state IN (
            'OPEN',
            'STOP_EXIT'
        )

          AND LOWER(
                COALESCE(
                    markets.result,
                    ''
                )
              )
              IN (
                  'yes',
                  'no'
              )
        """
    ).fetchall()

    changed = 0

    for trade in trades:
        remaining = float(
            trade[
                "remaining_count"
            ]
        )

        if remaining <= EPSILON:
            continue

        result = str(
            trade[
                "result"
            ]
        ).lower()

        side = str(
            trade[
                "side"
            ]
        ).lower()

        settlement_price = (
            1.0
            if result == side
            else 0.0
        )

        proceeds = (
            remaining
            * settlement_price
        )

        record_fill(
            connection,

            paper_trade_id=(
                trade[
                    "paper_trade_id"
                ]
            ),

            leg="EXIT",

            ts_ms=now_ms,

            price=settlement_price,
            count=remaining,

            fee=0.0,

            liquidity="SETTLEMENT",
            reason="SETTLEMENT",
        )

        connection.execute(
            """
            UPDATE paper_accounts

            SET
                cash = cash + ?,
                updated_at_ms = ?

            WHERE strategy_key = ?
            """,
            (
                proceeds,

                int(
                    now_ms
                ),

                trade[
                    "strategy_key"
                ],
            ),
        )

        connection.execute(
            """
            UPDATE paper_trades

            SET
                remaining_count = 0,
                updated_at_ms = ?

            WHERE paper_trade_id = ?
            """,
            (
                int(
                    now_ms
                ),

                trade[
                    "paper_trade_id"
                ],
            ),
        )

        finalize_closed_trade(
            connection,
            trade[
                "paper_trade_id"
            ],
            now_ms=now_ms,
        )

        changed += 1

    return changed


def paper_events(
    connection,
    strategy_key,
    *,
    after_ms,
):
    rows = connection.execute(
        """
        SELECT
            paper_trade_id,
            market_ticker,
            signal_ts_ms,

            entry_notional,
            entry_fee,

            net_pnl

        FROM paper_trades

        WHERE strategy_key = ?
          AND state = 'CLOSED'

          AND signal_ts_ms >= ?

          AND entry_notional > 0

          AND net_pnl IS NOT NULL

        ORDER BY
            signal_ts_ms,
            paper_trade_id
        """,
        (
            strategy_key,
            int(
                after_ms
            ),
        ),
    ).fetchall()

    events = []

    for row in rows:
        capital_used = (
            float(
                row[
                    "entry_notional"
                ]
            )
            + float(
                row[
                    "entry_fee"
                ]
            )
        )

        if capital_used <= 0:
            continue

        events.append(
            {
                "ts":
                    int(
                        row[
                            "signal_ts_ms"
                        ]
                    ),

                "market_ticker":
                    str(
                        row[
                            "market_ticker"
                        ]
                    ),

                "roi":
                    float(
                        row[
                            "net_pnl"
                        ]
                    )
                    / capital_used,
            }
        )

    return events


def run_once(
    connection,
    *,
    now_ms=None,
    starting_cash=(
        DEFAULT_STARTING_CASH
    ),
    trade_notional=(
        DEFAULT_TRADE_NOTIONAL
    ),
):
    if now_ms is None:
        now_ms = int(
            time.time()
            * 1000
        )

    accounts = ensure_accounts(
        connection,
        now_ms=now_ms,
        starting_cash=starting_cash,
        trade_notional=trade_notional,
    )

    signals = discover_signals(
        connection,
        now_ms=now_ms,
    )

    entries = process_entries(
        connection,
        now_ms=now_ms,
    )

    exits = process_open_trades(
        connection,
        now_ms=now_ms,
    )

    settlements = settle_positions(
        connection,
        now_ms=now_ms,
    )

    connection.commit()

    return {
        "accounts_created":
            accounts,

        "signals_created":
            signals,

        "entries_changed":
            entries,

        "exits_changed":
            exits,

        "settlements":
            settlements,
    }


def print_summary(
    connection,
):
    accounts = connection.execute(
        """
        SELECT
            COUNT(*)
        FROM paper_accounts
        WHERE enabled = 1
        """
    ).fetchone()[0]

    waiting = connection.execute(
        """
        SELECT COUNT(*)
        FROM paper_trades
        WHERE state = 'WAITING_ENTRY'
        """
    ).fetchone()[0]

    open_count = connection.execute(
        """
        SELECT COUNT(*)
        FROM paper_trades
        WHERE state IN (
            'OPEN',
            'STOP_EXIT'
        )
        """
    ).fetchone()[0]

    closed = connection.execute(
        """
        SELECT COUNT(*)
        FROM paper_trades
        WHERE state = 'CLOSED'
        """
    ).fetchone()[0]

    no_fill = connection.execute(
        """
        SELECT COUNT(*)
        FROM paper_trades
        WHERE state IN (
            'NO_FILL',
            'NO_CAPITAL'
        )
        """
    ).fetchone()[0]

    best = connection.execute(
        """
        SELECT
            strategy_key,
            cash,
            realized_pnl

        FROM paper_accounts

        WHERE enabled = 1

        ORDER BY cash DESC
        LIMIT 1
        """
    ).fetchone()

    best_text = (
        "none"
        if best is None
        else (
            f"{best['strategy_key']} "
            f"${float(best['cash']):.4f}"
        )
    )

    print(
        "PAPER BROKER | "
        f"accounts={accounts} | "
        f"waiting={waiting} | "
        f"open={open_count} | "
        f"closed={closed} | "
        f"no_fill={no_fill} | "
        f"best={best_text}"
    )


def run_loop(
    connection,
    *,
    interval,
    starting_cash,
    trade_notional,
):
    last_log = 0.0

    while True:
        result = run_once(
            connection,
            starting_cash=(
                starting_cash
            ),
            trade_notional=(
                trade_notional
            ),
        )

        now = time.monotonic()

        if (
            any(
                result.values()
            )
            or now
            - last_log
            >= 10
        ):
            print_summary(
                connection
            )

            last_log = now

        time.sleep(
            interval
        )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--db",
        required=True,
    )

    parser.add_argument(
        "--once",
        action="store_true",
    )

    parser.add_argument(
        "--interval",
        type=float,
        default=.25,
    )

    parser.add_argument(
        "--starting-cash",
        type=float,
        default=(
            DEFAULT_STARTING_CASH
        ),
    )

    parser.add_argument(
        "--trade-notional",
        type=float,
        default=(
            DEFAULT_TRADE_NOTIONAL
        ),
    )

    args = parser.parse_args()

    connection = connect(
        args.db
    )

    try:
        init_db(
            connection
        )

        if args.once:
            print(
                run_once(
                    connection,

                    starting_cash=(
                        args.starting_cash
                    ),

                    trade_notional=(
                        args.trade_notional
                    ),
                )
            )

            print_summary(
                connection
            )

            return

        run_loop(
            connection,

            interval=args.interval,

            starting_cash=(
                args.starting_cash
            ),

            trade_notional=(
                args.trade_notional
            ),
        )

    finally:
        connection.close()


if __name__ == "__main__":
    main()


def build_paper_dashboard_state(
    connection,
    *,
    recent_limit=20,
):
    """
    Build a read-only view of the realistic paper
    accounts.

    Equity = cash + executable-bid value of currently
    open paper positions.
    """

    account_rows = connection.execute(
        """
        SELECT
            accounts.*,

            registry.family,
            registry.description

        FROM paper_accounts AS accounts

        JOIN shadow_strategy_registry AS registry
          ON registry.strategy_key
             = accounts.strategy_key

        WHERE accounts.enabled = 1

        ORDER BY accounts.strategy_key
        """
    ).fetchall()

    trade_rows = connection.execute(
        """
        SELECT *
        FROM paper_trades
        ORDER BY signal_ts_ms, paper_trade_id
        """
    ).fetchall()

    by_strategy = {}

    for account in account_rows:
        key = str(
            account[
                "strategy_key"
            ]
        )

        by_strategy[key] = {
            "strategy_key":
                key,

            "family":
                str(
                    account[
                        "family"
                    ]
                ),

            "description":
                str(
                    account[
                        "description"
                    ]
                ),

            "starting_cash":
                float(
                    account[
                        "starting_cash"
                    ]
                ),

            "cash":
                float(
                    account[
                        "cash"
                    ]
                ),

            "realized_pnl":
                float(
                    account[
                        "realized_pnl"
                    ]
                ),

            "trade_notional":
                float(
                    account[
                        "trade_notional"
                    ]
                ),

            "created_at_ms":
                int(
                    account[
                        "created_at_ms"
                    ]
                ),

            "signals":
                0,

            "closed":
                0,

            "wins":
                0,

            "losses":
                0,

            "breakeven":
                0,

            "open":
                0,

            "no_fill":
                0,

            "open_value":
                0.0,

            "net_pnl":
                0.0,
        }

    latest_book_cache = {}

    def latest_book(
        market_ticker,
    ):
        ticker = str(
            market_ticker
        )

        if ticker in latest_book_cache:
            return latest_book_cache[
                ticker
            ]

        row = connection.execute(
            """
            SELECT *
            FROM topbook_snapshots

            WHERE market_ticker = ?

            ORDER BY ts_ms DESC
            LIMIT 1
            """,
            (
                ticker,
            ),
        ).fetchone()

        latest_book_cache[
            ticker
        ] = row

        return row

    for trade in trade_rows:
        strategy_key = str(
            trade[
                "strategy_key"
            ]
        )

        account = by_strategy.get(
            strategy_key
        )

        if account is None:
            continue

        account[
            "signals"
        ] += 1

        state = str(
            trade[
                "state"
            ]
        )

        if state == "CLOSED":
            account[
                "closed"
            ] += 1

            pnl = float(
                trade[
                    "net_pnl"
                ]
                or 0.0
            )

            account[
                "net_pnl"
            ] += pnl

            if pnl > EPSILON:
                account[
                    "wins"
                ] += 1

            elif pnl < -EPSILON:
                account[
                    "losses"
                ] += 1

            else:
                account[
                    "breakeven"
                ] += 1

        elif state in {
            "OPEN",
            "STOP_EXIT",
        }:
            account[
                "open"
            ] += 1

            remaining = float(
                trade[
                    "remaining_count"
                ]
                or 0.0
            )

            if remaining <= EPSILON:
                continue

            book_row = latest_book(
                trade[
                    "market_ticker"
                ]
            )

            if book_row is None:
                continue

            book = side_book(
                book_row,
                trade[
                    "side"
                ],
            )

            account[
                "open_value"
            ] += (
                remaining
                * float(
                    book[
                        "bid"
                    ]
                )
            )

        elif state in {
            "NO_FILL",
            "NO_CAPITAL",
        }:
            account[
                "no_fill"
            ] += 1

    accounts = list(
        by_strategy.values()
    )

    for account in accounts:
        account[
            "equity"
        ] = (
            account[
                "cash"
            ]
            + account[
                "open_value"
            ]
        )

        account[
            "equity_pnl"
        ] = (
            account[
                "equity"
            ]
            - account[
                "starting_cash"
            ]
        )

        closed = int(
            account[
                "closed"
            ]
        )

        account[
            "win_rate"
        ] = (
            None
            if closed <= 0
            else (
                account[
                    "wins"
                ]
                / closed
            )
        )

    accounts.sort(
        key=lambda row: (
            -float(
                row[
                    "equity"
                ]
            ),

            -int(
                row[
                    "closed"
                ]
            ),

            row[
                "strategy_key"
            ],
        )
    )

    recent_rows = connection.execute(
        """
        SELECT
            paper_trade_id,
            strategy_key,
            family,

            market_ticker,
            side,

            signal_ts_ms,

            requested_count,
            filled_count,

            entry_avg_price,
            entry_notional,
            entry_fee,

            tp_price,
            sl_price,

            state,

            exit_reason,
            exit_avg_price,
            exit_fee,

            gross_pnl,
            net_pnl,

            updated_at_ms

        FROM paper_trades

        ORDER BY
            updated_at_ms DESC,
            paper_trade_id DESC

        LIMIT ?
        """,
        (
            int(
                recent_limit
            ),
        ),
    ).fetchall()

    recent = [
        dict(
            row
        )
        for row
        in recent_rows
    ]

    total_starting = sum(
        row[
            "starting_cash"
        ]
        for row
        in accounts
    )

    total_cash = sum(
        row[
            "cash"
        ]
        for row
        in accounts
    )

    total_open_value = sum(
        row[
            "open_value"
        ]
        for row
        in accounts
    )

    total_equity = (
        total_cash
        + total_open_value
    )

    states = {
        "WAITING_ENTRY": 0,
        "OPEN": 0,
        "STOP_EXIT": 0,
        "CLOSED": 0,
        "NO_FILL": 0,
        "NO_CAPITAL": 0,
    }

    for trade in trade_rows:
        state = str(
            trade[
                "state"
            ]
        )

        states[
            state
        ] = (
            states.get(
                state,
                0,
            )
            + 1
        )

    return {
        "accounts":
            accounts,

        "recent":
            recent,

        "account_count":
            len(
                accounts
            ),

        "signal_count":
            len(
                trade_rows
            ),

        "closed_count":
            states.get(
                "CLOSED",
                0,
            ),

        "open_count":
            (
                states.get(
                    "OPEN",
                    0,
                )
                + states.get(
                    "STOP_EXIT",
                    0,
                )
            ),

        "waiting_count":
            states.get(
                "WAITING_ENTRY",
                0,
            ),

        "no_fill_count":
            (
                states.get(
                    "NO_FILL",
                    0,
                )
                + states.get(
                    "NO_CAPITAL",
                    0,
                )
            ),

        "total_starting_cash":
            total_starting,

        "total_cash":
            total_cash,

        "total_open_value":
            total_open_value,

        "total_equity":
            total_equity,

        "total_equity_pnl":
            (
                total_equity
                - total_starting
            ),

        "best":
            (
                None
                if not accounts
                else accounts[0]
            ),
    }


def paper_dashboard_signature(
    state,
):
    if not state:
        return None

    best = state.get(
        "best"
    )

    latest = (
        state.get(
            "recent"
        )
        or []
    )

    latest_row = (
        None
        if not latest
        else latest[0]
    )

    return (
        int(
            state.get(
                "account_count",
                0,
            )
        ),

        int(
            state.get(
                "signal_count",
                0,
            )
        ),

        int(
            state.get(
                "closed_count",
                0,
            )
        ),

        int(
            state.get(
                "open_count",
                0,
            )
        ),

        int(
            state.get(
                "no_fill_count",
                0,
            )
        ),

        round(
            float(
                state.get(
                    "total_equity",
                    0.0,
                )
            ),
            6,
        ),

        (
            None
            if best is None
            else (
                best[
                    "strategy_key"
                ],

                round(
                    float(
                        best[
                            "equity"
                        ]
                    ),
                    6,
                ),
            )
        ),

        (
            None
            if latest_row is None
            else (
                latest_row[
                    "paper_trade_id"
                ],

                latest_row[
                    "state"
                ],

                latest_row[
                    "updated_at_ms"
                ],
            )
        ),
    )
