from __future__ import annotations

import argparse
import json
import statistics
import time

from .database import (
    connect,
    init_db,
)

from .trigger_shadow import (
    PROFILES,
)

from .paper_broker import (
    paper_events,
)


def strategy_status(
    *,
    sample_n,
    avg_roi,
    recent_20_avg_roi,
):
    """
    Descriptive research status only.

    None of these statuses authorize live trading.
    """

    if sample_n < 20:
        return "BUILDING"

    if (
        avg_roi is None
        or avg_roi <= 0
    ):
        return "NEGATIVE"

    if sample_n < 50:
        return "WATCH"

    if (
        recent_20_avg_roi
        is not None
        and recent_20_avg_roi <= 0
    ):
        return "COOLING"

    return "PROMISING"


def score_events(
    events,
):
    """
    Each event contains:

        ts
        market_ticker
        roi

    ROI is normalized to $1 of entry notional.

    cumulative_pnl_per_1 therefore means:
    hypothetical cumulative P&L if exactly $1 were
    allocated independently to every signal.

    It is NOT a capital-constrained portfolio simulation.
    """

    if not events:
        return {
            "sample_n": 0,
            "unique_markets": 0,

            "wins": 0,
            "losses": 0,
            "breakeven": 0,

            "win_rate": None,

            "avg_roi": None,
            "median_roi": None,
            "recent_20_avg_roi": None,

            "cumulative_pnl_per_1": 0.0,
            "max_drawdown_per_1": 0.0,

            "status": "BUILDING",
        }

    rois = [
        float(
            event[
                "roi"
            ]
        )
        for event
        in events
    ]

    wins = sum(
        value > 1e-12
        for value
        in rois
    )

    losses = sum(
        value < -1e-12
        for value
        in rois
    )

    breakeven = (
        len(rois)
        - wins
        - losses
    )

    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0

    for roi in rois:
        cumulative += roi

        peak = max(
            peak,
            cumulative,
        )

        max_drawdown = max(
            max_drawdown,
            peak
            - cumulative,
        )

    avg_roi = statistics.fmean(
        rois
    )

    median_roi = statistics.median(
        rois
    )

    recent = rois[
        -20:
    ]

    recent_20_avg_roi = (
        statistics.fmean(
            recent
        )
        if recent
        else None
    )

    return {
        "sample_n":
            len(
                rois
            ),

        "unique_markets":
            len(
                {
                    event[
                        "market_ticker"
                    ]
                    for event
                    in events
                }
            ),

        "wins":
            wins,

        "losses":
            losses,

        "breakeven":
            breakeven,

        "win_rate":
            wins
            / len(
                rois
            ),

        "avg_roi":
            avg_roi,

        "median_roi":
            median_roi,

        "recent_20_avg_roi":
            recent_20_avg_roi,

        "cumulative_pnl_per_1":
            cumulative,

        "max_drawdown_per_1":
            max_drawdown,

        "status":
            strategy_status(
                sample_n=len(
                    rois
                ),

                avg_roi=avg_roi,

                recent_20_avg_roi=(
                    recent_20_avg_roi
                ),
            ),
    }


def register_strategy(
    connection,
    *,
    strategy_key,
    family,
    version,
    description,
    definition,
    evidence_basis,
    now_ms,
):
    """
    INSERT OR IGNORE is deliberate.

    Once a challenger exists, its shadow_start_ms is
    immutable. Restarting the program must never move
    the start line backward or forward.
    """

    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO
        shadow_strategy_registry (
            strategy_key,

            family,
            version,

            description,
            definition_json,

            evidence_basis,

            created_at_ms,

            discovery_cutoff_ms,
            shadow_start_ms,

            enabled
        )
        VALUES (
            ?,

            ?, ?,

            ?, ?,

            ?,

            ?,

            ?,
            ?,

            1
        )
        """,
        (
            strategy_key,

            family,
            int(
                version
            ),

            description,

            json.dumps(
                definition,
                sort_keys=True,
            ),

            evidence_basis,

            int(
                now_ms
            ),

            int(
                now_ms
            )
            - 1,

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


def register_current_candidates(
    connection,
    *,
    now_ms,
):
    inserted = 0

    # --------------------------------------------
    # Main trigger challengers
    # --------------------------------------------

    for profile in PROFILES:
        profile_id = str(
            profile[
                "profile_id"
            ]
        )

        strategy_key = (
            "main:"
            + profile_id
            + ":v1"
        )

        inserted += register_strategy(
            connection,

            strategy_key=(
                strategy_key
            ),

            family=(
                "MAIN_TRIGGER"
            ),

            version=1,

            description=(
                "60-69c / 5-10m / "
                "+25c -5c / "
                + profile_id
            ),

            definition={
                "profile_id":
                    profile_id,

                "price_low":
                    0.60,

                "price_high":
                    0.69,

                "time_low":
                    300,

                "time_high":
                    599,

                "tp_delta":
                    0.25,

                "sl_delta":
                    0.05,
            },

            evidence_basis=(
                "EXECUTABLE_BID_SHADOW"
            ),

            now_ms=now_ms,
        )

    # --------------------------------------------
    # Historical micro leads become challengers.
    #
    # These are frozen definitions. They begin
    # official forward shadow evaluation NOW.
    # --------------------------------------------

    leads = connection.execute(
        """
        SELECT *
        FROM micro_multiplier_atlas

        WHERE observations >= 50
          AND ci_low > break_even_touch

        ORDER BY
            entry_price_key,
            time_bucket,
            target_price_key
        """
    ).fetchall()

    for row in leads:
        entry_key = int(
            row[
                "entry_price_key"
            ]
        )

        target_key = int(
            row[
                "target_price_key"
            ]
        )

        bucket = str(
            row[
                "time_bucket"
            ]
        )

        strategy_key = (
            f"micro:{entry_key}:"
            f"{bucket}:"
            f"{target_key}:v1"
        )

        entry_cents = (
            float(
                row[
                    "entry_price"
                ]
            )
            * 100
        )

        target_cents = (
            float(
                row[
                    "target_price"
                ]
            )
            * 100
        )

        inserted += register_strategy(
            connection,

            strategy_key=strategy_key,

            family=(
                "MICRO_MULTIPLIER"
            ),

            version=1,

            description=(
                f"{entry_cents:.1f}c "
                f"-> {target_cents:.1f}c "
                f"/ {bucket}"
            ),

            definition={
                "entry_price_key":
                    entry_key,

                "time_bucket":
                    bucket,

                "target_price_key":
                    target_key,

                "target_price":
                    float(
                        row[
                            "target_price"
                        ]
                    ),

                "historical_n":
                    int(
                        row[
                            "observations"
                        ]
                    ),

                "historical_touch_rate":
                    float(
                        row[
                            "touch_rate"
                        ]
                    ),

                "historical_ci_low":
                    float(
                        row[
                            "ci_low"
                        ]
                    ),

                "gross_break_even":
                    float(
                        row[
                            "break_even_touch"
                        ]
                    ),
            },

            evidence_basis=(
                "ASK_ENTRY_PLUS_EXECUTABLE_BID_TOUCH_PROXY"
            ),

            now_ms=now_ms,
        )

    return inserted


def main_events(
    connection,
    *,
    definition,
    shadow_start_ms,
):
    profile_id = str(
        definition[
            "profile_id"
        ]
    )

    rows = connection.execute(
        """
        SELECT
            confirmations.confirmed_at_ms
                AS ts,

            confirmations.market_ticker,

            confirmations.entry_ask,
            confirmations.seconds_remaining,

            intents.entry_notional,
            intents.bid_proxy_gross_pnl

        FROM shadow_execution_intents
            AS intents

        JOIN main_trigger_confirmations
            AS confirmations

          ON confirmations.confirmation_id
             =
             intents.confirmation_id

        WHERE intents.profile_id = ?

          AND intents.status = 'CLOSED'

          AND confirmations.confirmed_at_ms
              >= ?

          AND intents.entry_notional > 0

          AND intents.bid_proxy_gross_pnl
              IS NOT NULL

        ORDER BY
            confirmations.confirmed_at_ms,
            intents.shadow_intent_id
        """,
        (
            profile_id,
            int(
                shadow_start_ms
            ),
        ),
    ).fetchall()

    events = []

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
        entry_ask = float(
            row[
                "entry_ask"
            ]
        )

        seconds_remaining = float(
            row[
                "seconds_remaining"
            ]
        )

        if (
            entry_low is not None
            and entry_ask
            < float(entry_low)
        ):
            continue

        if (
            entry_high is not None
            and entry_ask
            > float(entry_high)
        ):
            continue

        if (
            seconds_low is not None
            and seconds_remaining
            < float(seconds_low)
        ):
            continue

        if (
            seconds_high is not None
            and seconds_remaining
            > float(seconds_high)
        ):
            continue

        notional = float(
            row[
                "entry_notional"
            ]
        )

        pnl = float(
            row[
                "bid_proxy_gross_pnl"
            ]
        )

        if notional <= 0:
            continue

        events.append(
            {
                "ts":
                    int(
                        row[
                            "ts"
                        ]
                    ),

                "market_ticker":
                    str(
                        row[
                            "market_ticker"
                        ]
                    ),

                "roi":
                    pnl
                    / notional,
            }
        )

    return events


def micro_events(
    connection,
    *,
    definition,
    shadow_start_ms,
):
    entry_key = int(
        definition[
            "entry_price_key"
        ]
    )

    target_key = int(
        definition[
            "target_price_key"
        ]
    )

    bucket = str(
        definition[
            "time_bucket"
        ]
    )

    target_price = float(
        definition[
            "target_price"
        ]
    )

    rows = connection.execute(
        """
        SELECT
            opportunities.detected_at_ms
                AS ts,

            opportunities.market_ticker,
            opportunities.entry_ask,

            targets.status

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

          AND targets.status
              IN (
                  'HIT',
                  'MISS'
              )

        ORDER BY
            opportunities.detected_at_ms,
            opportunities.micro_opportunity_id
        """,
        (
            entry_key,
            bucket,
            target_key,
            int(
                shadow_start_ms
            ),
        ),
    ).fetchall()

    events = []

    for row in rows:
        entry_price = float(
            row[
                "entry_ask"
            ]
        )

        if entry_price <= 0:
            continue

        if (
            str(
                row[
                    "status"
                ]
            )
            == "HIT"
        ):
            roi = (
                target_price
                / entry_price
                - 1.0
            )

        else:
            # Conservative micro model:
            # no target hit => entire entry lost.
            roi = -1.0

        events.append(
            {
                "ts":
                    int(
                        row[
                            "ts"
                        ]
                    ),

                "market_ticker":
                    str(
                        row[
                            "market_ticker"
                        ]
                    ),

                "roi":
                    roi,
            }
        )

    return events


def events_for_strategy(
    connection,
    registry_row,
):
    """
    Official forward Shadow Lab evidence now comes
    from realistic PaperBroker trades.

    Historical/proxy research remains elsewhere in
    the database but does not score this forward lab.
    """

    strategy_key = str(
        registry_row[
            "strategy_key"
        ]
    )

    shadow_start_ms = int(
        registry_row[
            "shadow_start_ms"
        ]
    )

    return paper_events(
        connection,
        strategy_key,
        after_ms=shadow_start_ms,
    )



def save_score(
    connection,
    *,
    strategy_key,
    score,
    now_ms,
):
    last = connection.execute(
        """
        SELECT *
        FROM shadow_strategy_score_snapshots

        WHERE strategy_key = ?

        ORDER BY snapshot_ts_ms DESC
        LIMIT 1
        """,
        (
            strategy_key,
        ),
    ).fetchone()

    signature = (
        int(
            score[
                "sample_n"
            ]
        ),

        round(
            float(
                score[
                    "cumulative_pnl_per_1"
                ]
            ),
            12,
        ),

        score[
            "status"
        ],
    )

    if last is not None:
        last_signature = (
            int(
                last[
                    "sample_n"
                ]
            ),

            round(
                float(
                    last[
                        "cumulative_pnl_per_1"
                    ]
                ),
                12,
            ),

            str(
                last[
                    "status"
                ]
            ),
        )

        if signature == last_signature:
            return 0

    connection.execute(
        """
        INSERT INTO
        shadow_strategy_score_snapshots (
            strategy_key,
            snapshot_ts_ms,

            sample_n,
            unique_markets,

            wins,
            losses,
            breakeven,

            win_rate,

            avg_roi,
            median_roi,
            recent_20_avg_roi,

            cumulative_pnl_per_1,
            max_drawdown_per_1,

            status
        )
        VALUES (
            ?, ?,

            ?, ?,

            ?, ?, ?,

            ?,

            ?, ?, ?,

            ?, ?,

            ?
        )
        """,
        (
            strategy_key,
            int(
                now_ms
            ),

            score[
                "sample_n"
            ],

            score[
                "unique_markets"
            ],

            score[
                "wins"
            ],

            score[
                "losses"
            ],

            score[
                "breakeven"
            ],

            score[
                "win_rate"
            ],

            score[
                "avg_roi"
            ],

            score[
                "median_roi"
            ],

            score[
                "recent_20_avg_roi"
            ],

            score[
                "cumulative_pnl_per_1"
            ],

            score[
                "max_drawdown_per_1"
            ],

            score[
                "status"
            ],
        ),
    )

    return 1


def run_once(
    connection,
    *,
    now_ms=None,
):
    if now_ms is None:
        now_ms = int(
            time.time()
            * 1000
        )

    registered = (
        register_current_candidates(
            connection,
            now_ms=now_ms,
        )
    )

    strategies = connection.execute(
        """
        SELECT *
        FROM shadow_strategy_registry

        WHERE enabled = 1

        ORDER BY
            family,
            strategy_key
        """
    ).fetchall()

    updated = 0

    for strategy in strategies:
        events = events_for_strategy(
            connection,
            strategy,
        )

        score = score_events(
            events
        )

        updated += save_score(
            connection,

            strategy_key=(
                strategy[
                    "strategy_key"
                ]
            ),

            score=score,

            now_ms=now_ms,
        )

    connection.commit()

    return {
        "registered":
            registered,

        "strategies":
            len(
                strategies
            ),

        "scores_updated":
            updated,
    }


def build_shadow_lab_state(
    connection,
):
    rows = connection.execute(
        """
        SELECT
            registry.*,

            scores.sample_n,
            scores.unique_markets,

            scores.wins,
            scores.losses,
            scores.breakeven,

            scores.win_rate,

            scores.avg_roi,
            scores.median_roi,
            scores.recent_20_avg_roi,

            scores.cumulative_pnl_per_1,
            scores.max_drawdown_per_1,

            scores.status

        FROM shadow_strategy_registry
            AS registry

        LEFT JOIN
        shadow_strategy_score_snapshots
            AS scores

          ON scores.strategy_key
             =
             registry.strategy_key

         AND scores.snapshot_ts_ms = (
            SELECT MAX(
                latest.snapshot_ts_ms
            )

            FROM shadow_strategy_score_snapshots
                AS latest

            WHERE latest.strategy_key
                  =
                  registry.strategy_key
         )

        WHERE registry.enabled = 1
        """
    ).fetchall()

    output = [
        dict(
            row
        )
        for row
        in rows
    ]

    output.sort(
        key=lambda row: (
            -int(
                row.get(
                    "sample_n"
                )
                or 0
            ),

            -float(
                row.get(
                    "avg_roi"
                )
                or -999999
            ),

            row[
                "strategy_key"
            ],
        )
    )

    return output


def shadow_lab_signature(
    state,
):
    return tuple(
        (
            row[
                "strategy_key"
            ],

            row.get(
                "sample_n"
            ),

            row.get(
                "status"
            ),

            row.get(
                "cumulative_pnl_per_1"
            ),
        )
        for row
        in state
    )


def run_loop(
    connection,
    *,
    interval=30.0,
):
    while True:
        result = run_once(
            connection
        )

        print(
            "SHADOW LAB | "
            f"registered="
            f"{result['registered']} | "
            f"strategies="
            f"{result['strategies']} | "
            f"scores_updated="
            f"{result['scores_updated']}"
        )

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
        default=30.0,
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
                    connection
                )
            )
            return

        run_loop(
            connection,
            interval=args.interval,
        )

    finally:
        connection.close()


if __name__ == "__main__":
    main()
