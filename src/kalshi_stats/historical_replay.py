from __future__ import annotations

import argparse
import csv
import json
import statistics
import time

from collections import defaultdict
from pathlib import Path

from .database import (
    connect,
    init_db,
)

from .paper_broker import (
    floor_contract_count,
    taker_fee_estimate,
)

from .strategy_zoo import (
    EXIT_RULES,
    PRICE_BANDS as GRID_PRICE_BANDS,
    TIME_BANDS as GRID_TIME_BANDS,
    grid_strategy_key,
)

from .tail_zoo import (
    HIGH_BANDS,
    HIGH_RULES,
    LOW_BANDS,
    LOW_TARGET_MULTIPLIERS,
    TIME_BANDS as TAIL_TIME_BANDS,
    tail_strategy_key,
)


TRADE_NOTIONAL = 1.00


FEATURE_COLUMNS = (
    "threshold_distance_bps",
    "threshold_distance_vol60",

    "return_30s",
    "return_60s",
    "return_180s",
    "return_300s",

    "ema_5s_9s_bps",
    "ema_9s_21s_bps",

    "ema_5s_slope_bps",
    "ema_9s_slope_bps",
    "ema_21s_slope_bps",

    "vwap_distance_60s_bps",
    "vwap_distance_300s_bps",

    "realized_vol_60s_bps",
    "realized_vol_300s_bps",

    "range_60s_bps",
    "range_300s_bps",

    "relative_volume_60s",
)


def find_band(
    value,
    bands,
):
    if value is None:
        return None

    value = float(
        value
    )

    for (
        name,
        low,
        high,
    ) in bands:

        if (
            float(low)
            <= value
            <= float(high)
        ):
            return (
                name,
                float(low),
                float(high),
            )

    return None


def side_price(
    row,
    side,
    kind,
):
    side = str(
        side
    ).lower()

    kind = str(
        kind
    ).lower()

    yes_bid = row[
        "yes_bid_close"
    ]

    yes_ask = row[
        "yes_ask_close"
    ]

    if (
        yes_bid is None
        or yes_ask is None
    ):
        return None

    yes_bid = float(
        yes_bid
    )

    yes_ask = float(
        yes_ask
    )

    if side == "yes":
        value = (
            yes_bid
            if kind == "bid"
            else yes_ask
        )

    elif side == "no":
        value = (
            1.0 - yes_ask
            if kind == "bid"
            else 1.0 - yes_bid
        )

    else:
        raise ValueError(
            f"unknown side: {side}"
        )

    return max(
        0.0,
        min(
            1.0,
            float(
                value
            ),
        ),
    )


def settlement_result(
    connection,
    market_ticker,
    cache,
):
    if market_ticker in cache:
        return cache[
            market_ticker
        ]

    row = connection.execute(
        """
        SELECT result

        FROM markets

        WHERE ticker = ?
        """,
        (
            market_ticker,
        ),
    ).fetchone()

    result = (
        None
        if row is None
        else str(
            row[
                "result"
            ]
            or ""
        ).lower()
    )

    if result not in {
        "yes",
        "no",
    }:
        result = None

    cache[
        market_ticker
    ] = result

    return result


def entry_features(
    row,
):
    return {
        key:
            row[
                key
            ]
        for key in FEATURE_COLUMNS
    }


def simulate_trade(
    connection,
    *,
    market_ticker,
    side,
    path,
    entry_index,
    entry_price,
    tp_price,
    sl_price,
    strategy_key,
    family,
    settlement_cache,
):
    count = floor_contract_count(
        TRADE_NOTIONAL,
        entry_price,
    )

    if count < 0.01:
        return None

    entry_notional = (
        count
        * float(
            entry_price
        )
    )

    entry_fee = taker_fee_estimate(
        count,
        entry_price,
    )

    exit_price = None
    exit_ts = None
    exit_reason = None
    exit_fee = 0.0

    for future in path[
        entry_index + 1:
    ]:
        bid = side_price(
            future,
            side,
            "bid",
        )

        if bid is None:
            continue

        if (
            tp_price is not None
            and bid
            >= float(
                tp_price
            )
        ):
            exit_price = float(
                tp_price
            )

            exit_ts = int(
                future[
                    "observed_ts"
                ]
            )

            exit_reason = "TP"

            exit_fee = (
                taker_fee_estimate(
                    count,
                    exit_price,
                )
            )

            break

        if (
            sl_price is not None
            and bid
            <= float(
                sl_price
            )
        ):
            # Stops trigger at the condition but
            # execute at the observed executable bid.
            exit_price = float(
                bid
            )

            exit_ts = int(
                future[
                    "observed_ts"
                ]
            )

            exit_reason = "STOP"

            exit_fee = (
                taker_fee_estimate(
                    count,
                    exit_price,
                )
            )

            break

    if exit_reason is None:
        # CRITICAL:
        #
        # Settlement is not read until the entire
        # post-entry price path has been processed.
        #
        # The entry decision therefore has no access
        # to the historical outcome.
        result = settlement_result(
            connection,
            market_ticker,
            settlement_cache,
        )

        if result is None:
            return None

        exit_price = (
            1.0
            if result
            == str(
                side
            ).lower()
            else 0.0
        )

        exit_ts = int(
            path[-1][
                "observed_ts"
            ]
        )

        exit_reason = "SETTLEMENT"

        # Ordinary binary settlement is not modeled
        # with a second trade fee.
        exit_fee = 0.0

    exit_notional = (
        count
        * float(
            exit_price
        )
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

    capital = (
        entry_notional
        + entry_fee
    )

    roi = (
        None
        if capital <= 0
        else (
            net_pnl
            / capital
        )
    )

    entry_row = path[
        entry_index
    ]

    result = {
        "strategy_key":
            strategy_key,

        "family":
            family,

        "market_ticker":
            market_ticker,

        "side":
            str(
                side
            ).lower(),

        "signal_ts":
            int(
                entry_row[
                    "observed_ts"
                ]
            ),

        "seconds_remaining":
            float(
                entry_row[
                    "seconds_remaining"
                ]
            ),

        "entry_price":
            float(
                entry_price
            ),

        "count":
            float(
                count
            ),

        "entry_notional":
            float(
                entry_notional
            ),

        "entry_fee":
            float(
                entry_fee
            ),

        "tp_price":
            (
                None
                if tp_price is None
                else float(
                    tp_price
                )
            ),

        "sl_price":
            (
                None
                if sl_price is None
                else float(
                    sl_price
                )
            ),

        "exit_ts":
            int(
                exit_ts
            ),

        "exit_price":
            float(
                exit_price
            ),

        "exit_reason":
            exit_reason,

        "exit_fee":
            float(
                exit_fee
            ),

        "gross_pnl":
            float(
                gross_pnl
            ),

        "net_pnl":
            float(
                net_pnl
            ),

        "roi":
            float(
                roi
            ),
    }

    result.update(
        entry_features(
            entry_row
        )
    )

    return result


def sample_stats(
    trades,
):
    if not trades:
        return {
            "n":
                0,

            "unique_markets":
                0,

            "wins":
                0,

            "losses":
                0,

            "breakeven":
                0,

            "win_rate":
                None,

            "avg_roi":
                None,

            "median_roi":
                None,

            "net_pnl":
                0.0,

            "avg_net_pnl":
                None,

            "max_drawdown_per_1":
                0.0,
        }

    rois = [
        float(
            trade[
                "roi"
            ]
        )
        for trade in trades
    ]

    pnls = [
        float(
            trade[
                "net_pnl"
            ]
        )
        for trade in trades
    ]

    wins = sum(
        1
        for value in pnls
        if value > 1e-12
    )

    losses = sum(
        1
        for value in pnls
        if value < -1e-12
    )

    breakeven = (
        len(
            trades
        )
        - wins
        - losses
    )

    running = 0.0
    peak = 0.0
    max_drawdown = 0.0

    for trade in sorted(
        trades,
        key=lambda row: (
            row[
                "signal_ts"
            ],
            row[
                "market_ticker"
            ],
            row[
                "side"
            ],
        ),
    ):
        running += float(
            trade[
                "roi"
            ]
        )

        peak = max(
            peak,
            running,
        )

        max_drawdown = max(
            max_drawdown,
            peak - running,
        )

    return {
        "n":
            len(
                trades
            ),

        "unique_markets":
            len(
                {
                    trade[
                        "market_ticker"
                    ]
                    for trade in trades
                }
            ),

        "wins":
            wins,

        "losses":
            losses,

        "breakeven":
            breakeven,

        "win_rate":
            (
                wins
                / len(
                    trades
                )
            ),

        "avg_roi":
            statistics.fmean(
                rois
            ),

        "median_roi":
            statistics.median(
                rois
            ),

        "net_pnl":
            sum(
                pnls
            ),

        "avg_net_pnl":
            statistics.fmean(
                pnls
            ),

        "max_drawdown_per_1":
            max_drawdown,
    }


def replay(
    connection,
    *,
    min_n=20,
):
    rows = connection.execute(
        """
        SELECT
            market_ticker,
            observed_ts,

            yes_bid_close,
            yes_ask_close,

            seconds_remaining,

            threshold_distance_bps,
            threshold_distance_vol60,

            return_30s,
            return_60s,
            return_180s,
            return_300s,

            ema_5s_9s_bps,
            ema_9s_21s_bps,

            ema_5s_slope_bps,
            ema_9s_slope_bps,
            ema_21s_slope_bps,

            vwap_distance_60s_bps,
            vwap_distance_300s_bps,

            realized_vol_60s_bps,
            realized_vol_300s_bps,

            range_60s_bps,
            range_300s_bps,

            relative_volume_60s

        FROM historical_market_features

        ORDER BY
            observed_ts,
            market_ticker
        """
    ).fetchall()

    paths = defaultdict(
        list
    )

    for row in rows:
        paths[
            str(
                row[
                    "market_ticker"
                ]
            )
        ].append(
            row
        )

    market_order = sorted(
        paths,
        key=lambda ticker: int(
            paths[
                ticker
            ][0][
                "observed_ts"
            ]
        ),
    )

    split_index = int(
        len(
            market_order
        )
        * .70
    )

    early_markets = set(
        market_order[
            :split_index
        ]
    )

    late_markets = set(
        market_order[
            split_index:
        ]
    )

    settlements = {}

    trades = []

    for market_index, ticker in enumerate(
        market_order,
        start=1,
    ):
        path = sorted(
            paths[
                ticker
            ],
            key=lambda row: int(
                row[
                    "observed_ts"
                ]
            ),
        )

        for side in (
            "yes",
            "no",
        ):
            seen = set()

            for index, row in enumerate(
                path
            ):
                entry = side_price(
                    row,
                    side,
                    "ask",
                )

                if (
                    entry is None
                    or entry <= 0
                    or entry >= 1
                ):
                    continue

                seconds = float(
                    row[
                        "seconds_remaining"
                    ]
                )

                # ==================================
                # GRID_V1
                # ==================================

                price_band = find_band(
                    entry,
                    GRID_PRICE_BANDS,
                )

                time_band = find_band(
                    seconds,
                    GRID_TIME_BANDS,
                )

                if (
                    price_band is not None
                    and time_band is not None
                ):
                    price_name = (
                        price_band[0]
                    )

                    time_name = (
                        time_band[0]
                    )

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

                        if strategy_key in seen:
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

                            if (
                                tp_price > .99
                                or sl_price < .01
                            ):
                                continue

                        seen.add(
                            strategy_key
                        )

                        trade = simulate_trade(
                            connection,

                            market_ticker=ticker,
                            side=side,

                            path=path,
                            entry_index=index,

                            entry_price=entry,

                            tp_price=tp_price,
                            sl_price=sl_price,

                            strategy_key=(
                                strategy_key
                            ),

                            family="GRID_V1",

                            settlement_cache=(
                                settlements
                            ),
                        )

                        if trade is not None:
                            trades.append(
                                trade
                            )

                # ==================================
                # TAIL_V1
                # ==================================

                tail_time = find_band(
                    seconds,
                    TAIL_TIME_BANDS,
                )

                if tail_time is None:
                    continue

                time_name = (
                    tail_time[0]
                )

                low_band = find_band(
                    entry,
                    LOW_BANDS,
                )

                if low_band is not None:
                    price_name = (
                        low_band[0]
                    )

                    for multiplier in (
                        LOW_TARGET_MULTIPLIERS
                    ):
                        exit_id = (
                            "x"
                            + str(
                                multiplier
                            ).replace(
                                ".",
                                "_",
                            )
                        )

                        strategy_key = (
                            tail_strategy_key(
                                "LOW",
                                price_name,
                                time_name,
                                exit_id,
                            )
                        )

                        if strategy_key in seen:
                            continue

                        target = (
                            entry
                            * float(
                                multiplier
                            )
                        )

                        if target > .999:
                            continue

                        seen.add(
                            strategy_key
                        )

                        trade = simulate_trade(
                            connection,

                            market_ticker=ticker,
                            side=side,

                            path=path,
                            entry_index=index,

                            entry_price=entry,

                            tp_price=target,
                            sl_price=None,

                            strategy_key=(
                                strategy_key
                            ),

                            family="TAIL_V1",

                            settlement_cache=(
                                settlements
                            ),
                        )

                        if trade is not None:
                            trades.append(
                                trade
                            )

                    strategy_key = (
                        tail_strategy_key(
                            "LOW",
                            price_name,
                            time_name,
                            "settle",
                        )
                    )

                    if strategy_key not in seen:
                        seen.add(
                            strategy_key
                        )

                        trade = simulate_trade(
                            connection,

                            market_ticker=ticker,
                            side=side,

                            path=path,
                            entry_index=index,

                            entry_price=entry,

                            tp_price=None,
                            sl_price=None,

                            strategy_key=(
                                strategy_key
                            ),

                            family="TAIL_V1",

                            settlement_cache=(
                                settlements
                            ),
                        )

                        if trade is not None:
                            trades.append(
                                trade
                            )

                high_band = find_band(
                    entry,
                    HIGH_BANDS,
                )

                if high_band is not None:
                    price_name = (
                        high_band[0]
                    )

                    for rule in HIGH_RULES:
                        strategy_key = (
                            tail_strategy_key(
                                "HIGH",
                                price_name,
                                time_name,
                                rule[
                                    "id"
                                ],
                            )
                        )

                        if strategy_key in seen:
                            continue

                        if (
                            rule[
                                "tp_delta"
                            ]
                            is None
                        ):
                            tp_price = None
                            sl_price = None

                        else:
                            tp_price = (
                                entry
                                + float(
                                    rule[
                                        "tp_delta"
                                    ]
                                )
                            )

                            sl_price = (
                                entry
                                - float(
                                    rule[
                                        "sl_delta"
                                    ]
                                )
                            )

                            if (
                                tp_price > .999
                                or sl_price < .001
                            ):
                                continue

                        seen.add(
                            strategy_key
                        )

                        trade = simulate_trade(
                            connection,

                            market_ticker=ticker,
                            side=side,

                            path=path,
                            entry_index=index,

                            entry_price=entry,

                            tp_price=tp_price,
                            sl_price=sl_price,

                            strategy_key=(
                                strategy_key
                            ),

                            family="TAIL_V1",

                            settlement_cache=(
                                settlements
                            ),
                        )

                        if trade is not None:
                            trades.append(
                                trade
                            )

        if (
            market_index % 100
            == 0
        ):
            print(
                "REPLAY | "
                f"{market_index}/"
                f"{len(market_order)} markets | "
                f"{len(trades):,} trades"
            )

    grouped = defaultdict(
        list
    )

    for trade in trades:
        grouped[
            trade[
                "strategy_key"
            ]
        ].append(
            trade
        )

    summaries = []

    late_min_n = max(
        5,
        int(
            min_n
            * .30
        ),
    )

    for (
        strategy_key,
        strategy_trades,
    ) in grouped.items():

        ordered = sorted(
            strategy_trades,
            key=lambda row: int(
                row[
                    "signal_ts"
                ]
            ),
        )

        early = [
            trade
            for trade in ordered
            if trade[
                "market_ticker"
            ]
            in early_markets
        ]

        late = [
            trade
            for trade in ordered
            if trade[
                "market_ticker"
            ]
            in late_markets
        ]

        full_stats = sample_stats(
            ordered
        )

        early_stats = sample_stats(
            early
        )

        late_stats = sample_stats(
            late
        )

        if (
            full_stats[
                "n"
            ]
            < min_n
            or late_stats[
                "n"
            ]
            < late_min_n
        ):
            status = (
                "INSUFFICIENT"
            )

        elif (
            early_stats[
                "avg_roi"
            ]
            is not None
            and late_stats[
                "avg_roi"
            ]
            is not None
            and early_stats[
                "avg_roi"
            ] > 0
            and late_stats[
                "avg_roi"
            ] > 0
        ):
            status = (
                "EARLY_LATE_POSITIVE"
            )

        elif (
            late_stats[
                "avg_roi"
            ]
            is not None
            and late_stats[
                "avg_roi"
            ] > 0
        ):
            status = (
                "LATE_POSITIVE_ONLY"
            )

        else:
            status = "NEGATIVE"

        conservative_score = None

        if (
            early_stats[
                "avg_roi"
            ]
            is not None
            and late_stats[
                "avg_roi"
            ]
            is not None
        ):
            conservative_score = min(
                early_stats[
                    "avg_roi"
                ],
                late_stats[
                    "avg_roi"
                ],
            )

        summaries.append(
            {
                "strategy_key":
                    strategy_key,

                "family":
                    ordered[0][
                        "family"
                    ],

                "status":
                    status,

                "conservative_score":
                    conservative_score,

                "full":
                    full_stats,

                "early_70pct":
                    early_stats,

                "late_30pct":
                    late_stats,
            }
        )

    summaries.sort(
        key=lambda row: (
            row[
                "status"
            ]
            == "EARLY_LATE_POSITIVE",

            (
                float(
                    row[
                        "conservative_score"
                    ]
                )
                if row[
                    "conservative_score"
                ]
                is not None
                else -999.0
            ),

            row[
                "full"
            ][
                "n"
            ],
        ),
        reverse=True,
    )

    return {
        "markets":
            market_order,

        "early_market_count":
            len(
                early_markets
            ),

        "late_market_count":
            len(
                late_markets
            ),

        "trades":
            trades,

        "summaries":
            summaries,
    }


def write_trade_csv(
    path,
    trades,
):
    fields = [
        "strategy_key",
        "family",

        "market_ticker",
        "side",

        "signal_ts",
        "seconds_remaining",

        "entry_price",
        "count",
        "entry_notional",
        "entry_fee",

        "tp_price",
        "sl_price",

        "exit_ts",
        "exit_price",
        "exit_reason",
        "exit_fee",

        "gross_pnl",
        "net_pnl",
        "roi",

        *FEATURE_COLUMNS,
    ]

    with Path(
        path
    ).open(
        "w",
        newline="",
        encoding="utf-8",
    ) as handle:

        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
        )

        writer.writeheader()

        for trade in trades:
            writer.writerow(
                {
                    field:
                        trade.get(
                            field
                        )
                    for field in fields
                }
            )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--db",
        required=True,
    )

    parser.add_argument(
        "--out",
        default=(
            "reports/"
            "historical_replay_summary.json"
        ),
    )

    parser.add_argument(
        "--trades-out",
        default=(
            "reports/"
            "historical_replay_trades.csv"
        ),
    )

    parser.add_argument(
        "--min-n",
        type=int,
        default=20,
    )

    parser.add_argument(
        "--top",
        type=int,
        default=30,
    )

    args = parser.parse_args()

    started = time.time()

    connection = connect(
        args.db
    )

    try:
        init_db(
            connection
        )

        result = replay(
            connection,
            min_n=args.min_n,
        )

    finally:
        connection.close()

    write_trade_csv(
        args.trades_out,
        result[
            "trades"
        ],
    )

    summary_payload = {
        "generated_at_ms":
            int(
                time.time()
                * 1000
            ),

        "evidence_type":
            (
                "RETROSPECTIVE_"
                "CHRONOLOGICAL_REPLAY"
            ),

        "execution_model":
            (
                "historical sampled "
                "bid/ask; $1 notional; "
                "fee estimate; no historical "
                "depth/queue/IOC latency"
            ),

        "lookahead_rule":
            (
                "entry uses only current "
                "historical row; path is "
                "walked chronologically; "
                "settlement result is read "
                "only after path processing"
            ),

        "market_count":
            len(
                result[
                    "markets"
                ]
            ),

        "early_market_count":
            result[
                "early_market_count"
            ],

        "late_market_count":
            result[
                "late_market_count"
            ],

        "trade_count":
            len(
                result[
                    "trades"
                ]
            ),

        "strategy_count":
            len(
                result[
                    "summaries"
                ]
            ),

        "runtime_seconds":
            (
                time.time()
                - started
            ),

        "summaries":
            result[
                "summaries"
            ],
    }

    output = Path(
        args.out
    )

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output.write_text(
        json.dumps(
            summary_payload,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    print()
    print(
        "=" * 118
    )

    print(
        "HISTORICAL CHRONOLOGICAL REPLAY"
    )

    print(
        "=" * 118
    )

    print(
        f"Markets: "
        f"{summary_payload['market_count']:,}"
    )

    print(
        f"Early / late split: "
        f"{summary_payload['early_market_count']:,}"
        f" / "
        f"{summary_payload['late_market_count']:,}"
    )

    print(
        f"Closed simulated trades: "
        f"{summary_payload['trade_count']:,}"
    )

    print(
        f"Strategies with evidence: "
        f"{summary_payload['strategy_count']:,}"
    )

    print()

    stable = [
        row
        for row in result[
            "summaries"
        ]
        if row[
            "status"
        ]
        == "EARLY_LATE_POSITIVE"
    ]

    print(
        "EARLY + LATE POSITIVE "
        f"STRATEGIES: {len(stable)}"
    )

    print()

    for index, row in enumerate(
        stable[
            :args.top
        ],
        start=1,
    ):
        full = row[
            "full"
        ]

        early = row[
            "early_70pct"
        ]

        late = row[
            "late_30pct"
        ]

        print(
            f"{index:>2}. "
            f"{row['strategy_key']:<50} "
            f"| N={full['n']:<5} "
            f"| full={full['avg_roi'] * 100:+7.2f}% "
            f"| early={early['avg_roi'] * 100:+7.2f}% "
            f"| late={late['avg_roi'] * 100:+7.2f}% "
            f"| lateN={late['n']:<4}"
        )

    print()

    print(
        f"Summary: {args.out}"
    )

    print(
        f"Trades:  {args.trades_out}"
    )

    print(
        "\nRESEARCH ONLY: replay results "
        "do not count as prospective proof."
    )


if __name__ == "__main__":
    main()
