from __future__ import annotations

import argparse
import heapq
import json
import math
import statistics

from collections import (
    Counter,
    defaultdict,
    deque,
)

from pathlib import Path

from .database import (
    connect,
    init_db,
)

from .historical_replay import (
    replay,
)


STARTING_CASH = 10.0


class Evidence:
    def __init__(self):
        self.n = 0
        self.total = 0.0
        self.total_sq = 0.0

        self.recent = deque(
            maxlen=20
        )

        self.markets = set()

    def add(
        self,
        trade,
    ):
        roi = float(
            trade[
                "roi"
            ]
        )

        self.n += 1

        self.total += roi

        self.total_sq += (
            roi * roi
        )

        self.recent.append(
            roi
        )

        self.markets.add(
            trade[
                "market_ticker"
            ]
        )

    @property
    def mean(self):
        if self.n == 0:
            return None

        return (
            self.total
            / self.n
        )

    @property
    def std(self):
        if self.n < 2:
            return None

        variance = (
            self.total_sq
            - (
                self.total
                * self.total
                / self.n
            )
        ) / (
            self.n - 1
        )

        return math.sqrt(
            max(
                0.0,
                variance,
            )
        )

    @property
    def se(self):
        if (
            self.n < 2
            or self.std is None
        ):
            return None

        return (
            self.std
            / math.sqrt(
                self.n
            )
        )

    @property
    def recent_mean(self):
        if not self.recent:
            return None

        return statistics.fmean(
            self.recent
        )


SELECTORS = {
    "FAST": {
        "min_n":
            20,

        "min_markets":
            20,

        "z":
            0.0,

        "require_lcb_positive":
            False,
    },

    "STRICT": {
        "min_n":
            50,

        "min_markets":
            50,

        # Approximate one-sided 90% lower bound.
        "z":
            1.282,

        "require_lcb_positive":
            True,
    },
}


def score_candidate(
    evidence,
    config,
):
    if (
        evidence.n
        < config[
            "min_n"
        ]
    ):
        return None

    if (
        len(
            evidence.markets
        )
        < config[
            "min_markets"
        ]
    ):
        return None

    mean = evidence.mean

    recent = (
        evidence.recent_mean
    )

    if (
        mean is None
        or mean <= 0
        or recent is None
        or recent <= 0
    ):
        return None

    se = evidence.se

    if se is None:
        return None

    lower_bound = (
        mean
        - config[
            "z"
        ]
        * se
    )

    if (
        config[
            "require_lcb_positive"
        ]
        and lower_bound <= 0
    ):
        return None

    return {
        "score":
            lower_bound,

        "mean_roi":
            mean,

        "recent20_roi":
            recent,

        "se":
            se,

        "n":
            evidence.n,

        "unique_markets":
            len(
                evidence.markets
            ),
    }


def settle_account_if_ready(
    state,
    now_ts,
):
    pending = state[
        "pending"
    ]

    if pending is None:
        return

    if int(
        pending[
            "exit_ts"
        ]
    ) >= int(
        now_ts
    ):
        return

    pnl = float(
        pending[
            "net_pnl"
        ]
    )

    state[
        "cash"
    ] += pnl

    state[
        "realized_pnl"
    ] += pnl

    state[
        "closed"
    ] += 1

    if pnl > 1e-12:
        state[
            "wins"
        ] += 1

    elif pnl < -1e-12:
        state[
            "losses"
        ] += 1

    else:
        state[
            "breakeven"
        ] += 1

    state[
        "peak_cash"
    ] = max(
        state[
            "peak_cash"
        ],
        state[
            "cash"
        ],
    )

    state[
        "max_drawdown"
    ] = max(
        state[
            "max_drawdown"
        ],
        state[
            "peak_cash"
        ]
        - state[
            "cash"
        ],
    )

    state[
        "history"
    ].append(
        {
            **pending,

            "cash_after":
                state[
                    "cash"
                ],
        }
    )

    state[
        "pending"
    ] = None


def run_selector(
    trades,
    *,
    name,
    config,
):
    # --------------------------------------------
    # Shadow evidence
    #
    # Every strategy continues to hypothetically
    # trade even when the adaptive account does
    # not select it.
    #
    # Only CLOSED outcomes with exit_ts strictly
    # earlier than the current decision timestamp
    # are allowed into evidence.
    # --------------------------------------------

    by_signal = defaultdict(
        list
    )

    for trade in trades:
        by_signal[
            int(
                trade[
                    "signal_ts"
                ]
            )
        ].append(
            trade
        )

    signal_times = sorted(
        by_signal
    )

    completions = sorted(
        trades,
        key=lambda row: (
            int(
                row[
                    "exit_ts"
                ]
            ),
            int(
                row[
                    "signal_ts"
                ]
            ),
        ),
    )

    completion_index = 0

    evidence = defaultdict(
        Evidence
    )

    state = {
        "name":
            name,

        "starting_cash":
            STARTING_CASH,

        "cash":
            STARTING_CASH,

        "realized_pnl":
            0.0,

        "peak_cash":
            STARTING_CASH,

        "max_drawdown":
            0.0,

        "closed":
            0,

        "wins":
            0,

        "losses":
            0,

        "breakeven":
            0,

        "eligible_events":
            0,

        "pass_events":
            0,

        "busy_events":
            0,

        "no_capital_events":
            0,

        "pending":
            None,

        "history":
            [],
    }

    for signal_ts in signal_times:

        # Settle the adaptive account first if its
        # chosen position is already finished.
        settle_account_if_ready(
            state,
            signal_ts,
        )

        # Add ONLY hypothetical shadow outcomes
        # that were completely known before now.
        while (
            completion_index
            < len(
                completions
            )
            and int(
                completions[
                    completion_index
                ][
                    "exit_ts"
                ]
            )
            < signal_ts
        ):
            completed = (
                completions[
                    completion_index
                ]
            )

            evidence[
                completed[
                    "strategy_key"
                ]
            ].add(
                completed
            )

            completion_index += 1

        candidates = []

        for trade in by_signal[
            signal_ts
        ]:
            stats = score_candidate(
                evidence[
                    trade[
                        "strategy_key"
                    ]
                ],
                config,
            )

            if stats is None:
                continue

            candidates.append(
                (
                    float(
                        stats[
                            "score"
                        ]
                    ),

                    str(
                        trade[
                            "strategy_key"
                        ]
                    ),

                    trade,

                    stats,
                )
            )

        if not candidates:
            state[
                "pass_events"
            ] += 1
            continue

        state[
            "eligible_events"
        ] += 1

        # The single account cannot enter while a
        # previous selected position remains open.
        if state[
            "pending"
        ] is not None:
            state[
                "busy_events"
            ] += 1
            continue

        candidates.sort(
            key=lambda item: (
                item[0],
                item[3][
                    "n"
                ],
                item[1],
            ),
            reverse=True,
        )

        (
            score,
            _strategy_name,
            selected,
            stats,
        ) = candidates[0]

        required_cash = (
            float(
                selected[
                    "entry_notional"
                ]
            )
            + float(
                selected[
                    "entry_fee"
                ]
            )
        )

        if (
            required_cash
            > state[
                "cash"
            ]
            + 1e-12
        ):
            state[
                "no_capital_events"
            ] += 1
            continue

        state[
            "pending"
        ] = {
            "signal_ts":
                int(
                    selected[
                        "signal_ts"
                    ]
                ),

            "exit_ts":
                int(
                    selected[
                        "exit_ts"
                    ]
                ),

            "market_ticker":
                selected[
                    "market_ticker"
                ],

            "side":
                selected[
                    "side"
                ],

            "strategy_key":
                selected[
                    "strategy_key"
                ],

            "entry_price":
                float(
                    selected[
                        "entry_price"
                    ]
                ),

            "exit_price":
                float(
                    selected[
                        "exit_price"
                    ]
                ),

            "exit_reason":
                selected[
                    "exit_reason"
                ],

            "net_pnl":
                float(
                    selected[
                        "net_pnl"
                    ]
                ),

            "selector_score":
                float(
                    score
                ),

            "evidence_n":
                int(
                    stats[
                        "n"
                    ]
                ),

            "evidence_unique_markets":
                int(
                    stats[
                        "unique_markets"
                    ]
                ),

            "past_mean_roi":
                float(
                    stats[
                        "mean_roi"
                    ]
                ),

            "past_recent20_roi":
                float(
                    stats[
                        "recent20_roi"
                    ]
                ),
        }

    # Finish any final adaptive position.
    if state[
        "pending"
    ] is not None:

        final_exit = (
            int(
                state[
                    "pending"
                ][
                    "exit_ts"
                ]
            )
            + 1
        )

        settle_account_if_ready(
            state,
            final_exit,
        )

    strategy_counts = Counter(
        row[
            "strategy_key"
        ]
        for row in state[
            "history"
        ]
    )

    state[
        "strategy_counts"
    ] = dict(
        strategy_counts.most_common()
    )

    state[
        "return_pct"
    ] = (
        (
            state[
                "cash"
            ]
            / state[
                "starting_cash"
            ]
        )
        - 1.0
    ) * 100.0

    state[
        "win_rate"
    ] = (
        None
        if state[
            "closed"
        ] == 0
        else (
            state[
                "wins"
            ]
            / state[
                "closed"
            ]
        )
    )

    state.pop(
        "pending",
        None,
    )

    return state


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
            "historical_adaptive_summary.json"
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

        replay_result = replay(
            connection,
            min_n=20,
        )

    finally:
        connection.close()

    trades = replay_result[
        "trades"
    ]

    results = {}

    for (
        name,
        config,
    ) in SELECTORS.items():

        print(
            f"Running adaptive selector: "
            f"{name}"
        )

        results[
            name
        ] = run_selector(
            trades,

            name=name,
            config=config,
        )

    payload = {
        "evidence_type":
            (
                "RETROSPECTIVE_"
                "STRICT_WALK_FORWARD_"
                "ADAPTIVE_REPLAY"
            ),

        "starting_cash":
            STARTING_CASH,

        "shadow_trade_count":
            len(
                trades
            ),

        "lookahead_rule":
            (
                "selector evidence includes "
                "only shadow trades whose "
                "exit_ts is strictly earlier "
                "than each decision timestamp"
            ),

        "selectors":
            results,
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
            payload,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    print()
    print(
        "=" * 110
    )

    print(
        "STRICT WALK-FORWARD "
        "ADAPTIVE $10 REPLAY"
    )

    print(
        "=" * 110
    )

    for (
        name,
        result,
    ) in results.items():

        print()

        print(
            f"{name}"
        )

        print(
            f"  Start:       "
            f"${result['starting_cash']:.2f}"
        )

        print(
            f"  Finish:      "
            f"${result['cash']:.2f}"
        )

        print(
            f"  Net P&L:     "
            f"${result['realized_pnl']:+.2f}"
        )

        print(
            f"  Return:      "
            f"{result['return_pct']:+.1f}%"
        )

        print(
            f"  Trades:      "
            f"{result['closed']}"
        )

        print(
            f"  W / L:       "
            f"{result['wins']} / "
            f"{result['losses']}"
        )

        if (
            result[
                "win_rate"
            ]
            is not None
        ):
            print(
                f"  Win rate:    "
                f"{result['win_rate'] * 100:.1f}%"
            )

        print(
            f"  Max DD:      "
            f"${result['max_drawdown']:.2f}"
        )

        print(
            f"  Pass events: "
            f"{result['pass_events']:,}"
        )

        print(
            "  Most used:"
        )

        for (
            strategy,
            count,
        ) in list(
            result[
                "strategy_counts"
            ].items()
        )[:10]:

            print(
                f"    {count:>4}  "
                f"{strategy}"
            )

    print()

    print(
        f"Wrote: {output}"
    )

    print(
        "\nRESEARCH ONLY — historical execution "
        "still lacks real IOC latency/depth."
    )


if __name__ == "__main__":
    main()
