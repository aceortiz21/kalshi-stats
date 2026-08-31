from __future__ import annotations

import argparse
import json
import time

from pathlib import Path

from .database import (
    connect,
    init_db,
)

from .paper_broker import (
    build_paper_dashboard_state,
)


FEATURE_COLUMNS = """
    market_ticker,
    ts,
    seconds_remaining,

    yes_bid,
    yes_ask,
    no_bid,
    no_ask,

    threshold,
    spot,

    threshold_distance_dollars,
    threshold_distance_bps,
    threshold_distance_vol60,

    return_30s,
    return_60s,
    return_180s,
    return_300s,

    ema_5_9_bps,
    ema_9_21_bps,

    ema_5_slope_bps,
    ema_9_slope_bps,
    ema_21_slope_bps,

    vwap_distance_60s_bps,
    vwap_distance_300s_bps,

    realized_vol_60s_bps,
    realized_vol_300s_bps,

    range_60s_bps,
    range_300s_bps,

    relative_volume_60s,

    trade_imbalance_60s,
    trade_imbalance_300s,

    book_imbalance_top10
"""


def diagnose_no_fill(
    trade,
    book,
):
    if book is None:
        return {
            "diagnosis":
                "NO_BOOK",

            "observed_ask":
                None,

            "observed_ask_size":
                None,

            "book_ts_ms":
                None,
        }

    side = str(
        trade[
            "side"
        ]
    ).lower()

    ask = float(
        book[
            f"{side}_ask"
        ]
    )

    raw_size = book[
        f"{side}_ask_size"
    ]

    size = (
        None
        if raw_size is None
        else float(
            raw_size
        )
    )

    limit_price = float(
        trade[
            "entry_limit"
        ]
    )

    if (
        size is None
        or size <= 0
    ):
        diagnosis = (
            "NO_OR_ZERO_ASK_DEPTH"
        )

    elif ask > (
        limit_price
        + 1e-12
    ):
        diagnosis = (
            "PRICE_MOVED_ABOVE_IOC_LIMIT"
        )

    elif size < .01:
        diagnosis = (
            "DEPTH_BELOW_MIN_CONTRACT"
        )

    else:
        diagnosis = (
            "OTHER_IOC_NO_FILL"
        )

    return {
        "diagnosis":
            diagnosis,

        "observed_ask":
            ask,

        "observed_ask_size":
            size,

        "book_ts_ms":
            int(
                book[
                    "ts_ms"
                ]
            ),
    }


def write_snapshot(
    *,
    db,
    output,
):
    connection = connect(
        db
    )

    try:
        init_db(
            connection
        )

        def rows(
            sql,
            params=(),
        ):
            return [
                dict(
                    row
                )
                for row
                in connection.execute(
                    sql,
                    params,
                ).fetchall()
            ]

        paper = (
            build_paper_dashboard_state(
                connection,
                recent_limit=250,
            )
        )

        registry = rows(
            """
            SELECT *
            FROM shadow_strategy_registry
            ORDER BY
                family,
                strategy_key
            """
        )

        accounts = rows(
            """
            SELECT *
            FROM paper_accounts
            ORDER BY strategy_key
            """
        )

        trades = rows(
            """
            SELECT *
            FROM paper_trades
            ORDER BY
                signal_ts_ms,
                paper_trade_id
            """
        )

        fills = rows(
            """
            SELECT *
            FROM paper_fills
            ORDER BY
                ts_ms,
                paper_fill_id
            """
        )

        cursors = rows(
            """
            SELECT *
            FROM paper_scan_cursors
            ORDER BY family
            """
        )

        latest_scores = rows(
            """
            SELECT score.*

            FROM shadow_strategy_score_snapshots
                AS score

            JOIN (
                SELECT
                    strategy_key,
                    MAX(
                        snapshot_ts_ms
                    ) AS latest_ts

                FROM
                    shadow_strategy_score_snapshots

                GROUP BY strategy_key
            ) AS latest

              ON latest.strategy_key
                 =
                 score.strategy_key

             AND latest.latest_ts
                 =
                 score.snapshot_ts_ms

            ORDER BY
                score.sample_n DESC,
                score.avg_roi DESC
            """
        )

        trade_context = []
        no_fill_diagnostics = []

        for trade in trades:
            item = dict(
                trade
            )

            feature = (
                connection.execute(
                    f"""
                    SELECT
                        {FEATURE_COLUMNS}

                    FROM
                        market_feature_snapshots

                    WHERE market_ticker = ?
                      AND ts <= ?

                    ORDER BY ts DESC

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
                    ),
                ).fetchone()
            )

            item[
                "signal_features"
            ] = (
                None
                if feature is None
                else dict(
                    feature
                )
            )

            trade_context.append(
                item
            )

            if trade[
                "state"
            ] not in {
                "NO_FILL",
                "NO_CAPITAL",
            }:
                continue

            book = (
                connection.execute(
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
                        + 2000,
                    ),
                ).fetchone()
            )

            diagnostic = (
                diagnose_no_fill(
                    trade,
                    book,
                )
            )

            book_ts = (
                diagnostic[
                    "book_ts_ms"
                ]
            )

            observed_ask = (
                diagnostic[
                    "observed_ask"
                ]
            )

            no_fill_diagnostics.append(
                {
                    "paper_trade_id":
                        trade[
                            "paper_trade_id"
                        ],

                    "strategy_key":
                        trade[
                            "strategy_key"
                        ],

                    "family":
                        trade[
                            "family"
                        ],

                    "market_ticker":
                        trade[
                            "market_ticker"
                        ],

                    "side":
                        trade[
                            "side"
                        ],

                    "signal_ts_ms":
                        trade[
                            "signal_ts_ms"
                        ],

                    "entry_limit":
                        trade[
                            "entry_limit"
                        ],

                    "entry_status":
                        trade[
                            "entry_status"
                        ],

                    **diagnostic,

                    "book_delay_ms":
                        (
                            None
                            if book_ts is None
                            else (
                                int(
                                    book_ts
                                )
                                - int(
                                    trade[
                                        "signal_ts_ms"
                                    ]
                                )
                            )
                        ),

                    "ask_move":
                        (
                            None
                            if observed_ask
                            is None
                            else (
                                float(
                                    observed_ask
                                )
                                - float(
                                    trade[
                                        "entry_limit"
                                    ]
                                )
                            )
                        ),
                }
            )

        tickers = sorted(
            {
                str(
                    trade[
                        "market_ticker"
                    ]
                )
                for trade in trades
            }
        )

        market_results = []

        if tickers:
            placeholders = ",".join(
                "?"
                for _ in tickers
            )

            market_results = rows(
                f"""
                SELECT
                    ticker,
                    result,
                    reference_price,
                    final_price,
                    close_time,
                    settlement_ts

                FROM markets

                WHERE ticker IN (
                    {placeholders}
                )

                ORDER BY close_time
                """,
                tickers,
            )

        state_counts = {}

        for trade in trades:
            key = (
                f"{trade['family']}:"
                f"{trade['state']}"
            )

            state_counts[
                key
            ] = (
                state_counts.get(
                    key,
                    0,
                )
                + 1
            )

        payload = {
            "snapshot_schema_version":
                1,

            "generated_at_ms":
                int(
                    time.time()
                    * 1000
                ),

            "summary": {
                "strategy_count":
                    len(
                        registry
                    ),

                "account_count":
                    len(
                        accounts
                    ),

                "trade_count":
                    len(
                        trades
                    ),

                "fill_count":
                    len(
                        fills
                    ),

                "no_fill_count":
                    len(
                        no_fill_diagnostics
                    ),

                "state_counts":
                    state_counts,
            },

            "paper_dashboard":
                paper,

            "strategy_registry":
                registry,

            "paper_accounts":
                accounts,

            "paper_trades":
                trade_context,

            "paper_fills":
                fills,

            "no_fill_diagnostics":
                no_fill_diagnostics,

            "latest_shadow_scores":
                latest_scores,

            "paper_scan_cursors":
                cursors,

            "market_results":
                market_results,
        }

        output = Path(
            output
        )

        output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        output.write_text(
            json.dumps(
                payload,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        return payload

    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--db",
        default=(
            "data/"
            "kalshi_stats_snapshot.sqlite"
        ),
    )

    parser.add_argument(
        "--out",
        default=(
            "reports/"
            "paper_engine_snapshot.json"
        ),
    )

    parser.add_argument(
        "--watch-seconds",
        type=float,
        default=0,
    )

    args = parser.parse_args()

    while True:
        payload = write_snapshot(
            db=args.db,
            output=args.out,
        )

        summary = payload[
            "summary"
        ]

        print(
            "PAPER SNAPSHOT | "
            f"strategies="
            f"{summary['strategy_count']} | "
            f"trades="
            f"{summary['trade_count']} | "
            f"fills="
            f"{summary['fill_count']} | "
            f"no_fill="
            f"{summary['no_fill_count']} | "
            f"out={args.out}"
        )

        if (
            args.watch_seconds
            <= 0
        ):
            break

        time.sleep(
            args.watch_seconds
        )


if __name__ == "__main__":
    main()
