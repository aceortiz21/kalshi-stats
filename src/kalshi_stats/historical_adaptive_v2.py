from __future__ import annotations

import argparse
import json
import math

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


DEFAULT_STARTING_CASH = 10.0
DEFAULT_MAX_NOTIONAL = 1.0
DEFAULT_RISK_FRACTION = .10


SELECTORS = {
    "BAYES_FAST": {
        "min_n":
            50,

        "min_markets":
            50,

        "recent_window":
            40,

        "recent_min":
            20,

        "z":
            1.645,

        "min_probability_edge":
            .010,

        "min_entry":
            .20,

        "max_entry":
            .95,
    },

    "BAYES_STRICT": {
        "min_n":
            100,

        "min_markets":
            100,

        "recent_window":
            60,

        "recent_min":
            30,

        "z":
            2.326,

        "min_probability_edge":
            .015,

        "min_entry":
            .20,

        "max_entry":
            .95,
    },
}


def is_settlement_strategy(
    strategy_key,
):
    return str(
        strategy_key
    ).endswith(
        ":settle"
    )


def beta_summary(
    *,
    wins,
    losses,
    z,
    prior_alpha=2.0,
    prior_beta=2.0,
):
    alpha = (
        float(
            prior_alpha
        )
        + int(
            wins
        )
    )

    beta = (
        float(
            prior_beta
        )
        + int(
            losses
        )
    )

    total = (
        alpha
        + beta
    )

    mean = (
        alpha
        / total
    )

    variance = (
        alpha
        * beta
        / (
            total
            * total
            * (
                total + 1.0
            )
        )
    )

    std = math.sqrt(
        max(
            0.0,
            variance,
        )
    )

    lower = max(
        0.0,
        mean
        - float(
            z
        )
        * std,
    )

    return {
        "mean":
            mean,

        "std":
            std,

        "lower":
            lower,

        "alpha":
            alpha,

        "beta":
            beta,
    }


class SettlementEvidence:
    def __init__(
        self,
        recent_window,
    ):
        self.n = 0
        self.wins = 0
        self.losses = 0

        self.markets = set()

        self.recent = deque(
            maxlen=int(
                recent_window
            )
        )

    def add(
        self,
        trade,
    ):
        win = (
            float(
                trade[
                    "exit_price"
                ]
            )
            > .5
        )

        self.n += 1

        if win:
            self.wins += 1
        else:
            self.losses += 1

        self.markets.add(
            str(
                trade[
                    "market_ticker"
                ]
            )
        )

        self.recent.append(
            bool(
                win
            )
        )

    @property
    def recent_wins(
        self,
    ):
        return sum(
            1
            for value in self.recent
            if value
        )

    @property
    def recent_losses(
        self,
    ):
        return (
            len(
                self.recent
            )
            - self.recent_wins
        )


def candidate_break_even_probability(
    trade,
):
    count = float(
        trade[
            "count"
        ]
    )

    if count <= 0:
        return None

    entry_cost = (
        float(
            trade[
                "entry_notional"
            ]
        )
        + float(
            trade[
                "entry_fee"
            ]
        )
    )

    return (
        entry_cost
        / count
    )


def score_candidate(
    evidence,
    trade,
    config,
):
    if not is_settlement_strategy(
        trade[
            "strategy_key"
        ]
    ):
        return None

    entry_price = float(
        trade[
            "entry_price"
        ]
    )

    if (
        entry_price
        < float(
            config[
                "min_entry"
            ]
        )
        or entry_price
        > float(
            config[
                "max_entry"
            ]
        )
    ):
        return None

    if (
        evidence.n
        < int(
            config[
                "min_n"
            ]
        )
    ):
        return None

    if (
        len(
            evidence.markets
        )
        < int(
            config[
                "min_markets"
            ]
        )
    ):
        return None

    if (
        len(
            evidence.recent
        )
        < int(
            config[
                "recent_min"
            ]
        )
    ):
        return None

    long_stats = beta_summary(
        wins=evidence.wins,
        losses=evidence.losses,
        z=config[
            "z"
        ],
    )

    recent_stats = beta_summary(
        wins=(
            evidence.recent_wins
        ),
        losses=(
            evidence.recent_losses
        ),
        z=config[
            "z"
        ],
    )

    break_even = (
        candidate_break_even_probability(
            trade
        )
    )

    if break_even is None:
        return None

    conservative_probability = min(
        long_stats[
            "lower"
        ],
        recent_stats[
            "lower"
        ],
    )

    probability_edge = (
        conservative_probability
        - break_even
    )

    if (
        probability_edge
        <= float(
            config[
                "min_probability_edge"
            ]
        )
    ):
        return None

    # Conservative expected ROI using the
    # posterior lower probability rather than
    # historical jackpot-sized mean ROI.
    conservative_roi = (
        probability_edge
        / max(
            break_even,
            1e-9,
        )
    )

    return {
        "score":
            conservative_roi,

        "probability_edge":
            probability_edge,

        "break_even_probability":
            break_even,

        "long_mean":
            long_stats[
                "mean"
            ],

        "long_lower":
            long_stats[
                "lower"
            ],

        "recent_mean":
            recent_stats[
                "mean"
            ],

        "recent_lower":
            recent_stats[
                "lower"
            ],

        "n":
            evidence.n,

        "unique_markets":
            len(
                evidence.markets
            ),

        "recent_n":
            len(
                evidence.recent
            ),
    }


def settle_if_ready(
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
            "scaled_net_pnl"
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

    drawdown = (
        state[
            "peak_cash"
        ]
        - state[
            "cash"
        ]
    )

    state[
        "max_drawdown"
    ] = max(
        state[
            "max_drawdown"
        ],
        drawdown,
    )

    if (
        state[
            "peak_cash"
        ]
        > 0
    ):
        state[
            "max_drawdown_pct"
        ] = max(
            state[
                "max_drawdown_pct"
            ],

            drawdown
            / state[
                "peak_cash"
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
    starting_cash,
    max_notional,
    risk_fraction,
):
    settlement_trades = [
        trade
        for trade in trades
        if is_settlement_strategy(
            trade[
                "strategy_key"
            ]
        )
    ]

    by_signal = defaultdict(
        list
    )

    for trade in settlement_trades:
        by_signal[
            int(
                trade[
                    "signal_ts"
                ]
            )
        ].append(
            trade
        )

    completions = sorted(
        settlement_trades,
        key=lambda trade: (
            int(
                trade[
                    "exit_ts"
                ]
            ),

            int(
                trade[
                    "signal_ts"
                ]
            ),
        ),
    )

    completion_index = 0

    evidence = defaultdict(
        lambda: SettlementEvidence(
            config[
                "recent_window"
            ]
        )
    )

    state = {
        "name":
            name,

        "starting_cash":
            float(
                starting_cash
            ),

        "cash":
            float(
                starting_cash
            ),

        "realized_pnl":
            0.0,

        "peak_cash":
            float(
                starting_cash
            ),

        "max_drawdown":
            0.0,

        "max_drawdown_pct":
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

        "too_small_events":
            0,

        "pending":
            None,

        "history":
            [],
    }

    for signal_ts in sorted(
        by_signal
    ):
        settle_if_ready(
            state,
            signal_ts,
        )

        # Strict walk-forward guard:
        # only trades completely resolved BEFORE
        # this decision can enter evidence.
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
                trade,
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

                    int(
                        stats[
                            "n"
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

        if state[
            "pending"
        ] is not None:
            state[
                "busy_events"
            ] += 1

            continue

        candidates.sort(
            reverse=True,
            key=lambda item: (
                item[0],
                item[1],
                item[2],
            ),
        )

        (
            _score,
            _n,
            _key,
            selected,
            stats,
        ) = candidates[0]

        target_risk = min(
            float(
                max_notional
            ),

            float(
                state[
                    "cash"
                ]
            )
            * float(
                risk_fraction
            ),
        )

        if target_risk < .01:
            state[
                "too_small_events"
            ] += 1

            continue

        baseline_cost = (
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

        if baseline_cost <= 0:
            continue

        scale = (
            target_risk
            / baseline_cost
        )

        scaled_pnl = (
            float(
                selected[
                    "net_pnl"
                ]
            )
            * scale
        )

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

            "target_risk":
                target_risk,

            "baseline_cost":
                baseline_cost,

            "size_scale":
                scale,

            "baseline_net_pnl":
                float(
                    selected[
                        "net_pnl"
                    ]
                ),

            "scaled_net_pnl":
                scaled_pnl,

            "selector_score":
                float(
                    stats[
                        "score"
                    ]
                ),

            "probability_edge":
                float(
                    stats[
                        "probability_edge"
                    ]
                ),

            "break_even_probability":
                float(
                    stats[
                        "break_even_probability"
                    ]
                ),

            "long_mean":
                float(
                    stats[
                        "long_mean"
                    ]
                ),

            "long_lower":
                float(
                    stats[
                        "long_lower"
                    ]
                ),

            "recent_mean":
                float(
                    stats[
                        "recent_mean"
                    ]
                ),

            "recent_lower":
                float(
                    stats[
                        "recent_lower"
                    ]
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
        }

    if state[
        "pending"
    ] is not None:
        settle_if_ready(
            state,

            int(
                state[
                    "pending"
                ][
                    "exit_ts"
                ]
            )
            + 1,
        )

    counts = Counter(
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
        counts.most_common()
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
            "historical_adaptive_v2_summary.json"
        ),
    )

    parser.add_argument(
        "--starting-cash",
        type=float,
        default=DEFAULT_STARTING_CASH,
    )

    parser.add_argument(
        "--max-notional",
        type=float,
        default=DEFAULT_MAX_NOTIONAL,
    )

    parser.add_argument(
        "--risk-fraction",
        type=float,
        default=DEFAULT_RISK_FRACTION,
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
            f"Running {name}..."
        )

        results[
            name
        ] = run_selector(
            trades,

            name=name,
            config=config,

            starting_cash=(
                args.starting_cash
            ),

            max_notional=(
                args.max_notional
            ),

            risk_fraction=(
                args.risk_fraction
            ),
        )

    payload = {
        "evidence_type":
            (
                "RETROSPECTIVE_"
                "STRICT_WALK_FORWARD_"
                "BAYESIAN_ADAPTIVE_V2"
            ),

        "execution_warning":
            (
                "position-size scaling is linear; "
                "historical depth, queue position, "
                "and IOC latency are unavailable"
            ),

        "starting_cash":
            args.starting_cash,

        "max_notional":
            args.max_notional,

        "risk_fraction":
            args.risk_fraction,

        "shadow_trade_count":
            len(
                trades
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
        "=" * 100
    )

    print(
        "BAYESIAN WALK-FORWARD ADAPTIVE V2"
    )

    print(
        "=" * 100
    )

    for (
        name,
        result,
    ) in results.items():

        print()
        print(
            name
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
            f"{result['return_pct']:+.2f}%"
        )

        print(
            f"  Trades:      "
            f"{result['closed']}"
        )

        print(
            f"  W/L:         "
            f"{result['wins']}/"
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
            f"${result['max_drawdown']:.2f} "
            f"("
            f"{result['max_drawdown_pct'] * 100:.1f}%"
            f")"
        )

        print(
            f"  Passes:      "
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
                f"    "
                f"{count:>4}  "
                f"{strategy}"
            )

    print()
    print(
        f"Wrote {output}"
    )


if __name__ == "__main__":
    main()
