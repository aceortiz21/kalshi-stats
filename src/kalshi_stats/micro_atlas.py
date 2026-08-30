from __future__ import annotations

import argparse
import time
from collections import defaultdict

from .analytics import (
    _build_series_map,
    _side_close,
    _side_low_high,
)

from .database import (
    connect,
    init_db,
)

from .micro_multiplier import (
    MICRO_MAX_PRICE,
    MICRO_MIN_PRICE,
    MICRO_TARGET_CENTS,
    price_key,
    time_bucket,
)

from .transition_research import (
    research_markets,
    wilson_interval,
)


EPSILON = 1e-12


def lookup_micro_atlas(
    connection,
    *,
    entry_ask,
    seconds_remaining,
):
    """
    Return historical multiplier paths matching the
    live executable ask at 0.1-cent resolution.
    """

    key = price_key(
        entry_ask
    )

    bucket = time_bucket(
        seconds_remaining
    )

    if bucket == "unknown":
        return []

    return connection.execute(
        """
        SELECT *
        FROM micro_multiplier_atlas

        WHERE entry_price_key = ?
          AND time_bucket = ?

        ORDER BY target_price
        """,
        (
            key,
            bucket,
        ),
    ).fetchall()



def build_live_micro_state(
    connection,
    *,
    market_ticker,
    side,
    entry_ask,
    seconds_remaining,
):
    """
    Combine the fixed historical atlas with growing
    prospective executable-bid evidence for one live
    cheap-contract state.
    """

    entry_ask = float(
        entry_ask
    )

    if not (
        MICRO_MIN_PRICE
        <= entry_ask
        <= MICRO_MAX_PRICE
    ):
        return None

    entry_key = price_key(
        entry_ask
    )

    bucket = time_bucket(
        seconds_remaining
    )

    if bucket == "unknown":
        return None

    atlas_rows = lookup_micro_atlas(
        connection,
        entry_ask=entry_ask,
        seconds_remaining=seconds_remaining,
    )

    prospective_rows = (
        connection.execute(
            """
            SELECT
                CAST(
                    ROUND(
                        targets.target_price
                        * 1000
                    )
                    AS INTEGER
                ) AS target_key,

                COUNT(*) AS total,

                SUM(
                    CASE
                        WHEN targets.status
                             IN ('HIT', 'MISS')
                        THEN 1
                        ELSE 0
                    END
                ) AS completed,

                SUM(
                    CASE
                        WHEN targets.status = 'HIT'
                        THEN 1
                        ELSE 0
                    END
                ) AS hits,

                SUM(
                    CASE
                        WHEN targets.status = 'INCOMPLETE'
                        THEN 1
                        ELSE 0
                    END
                ) AS incomplete

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

            GROUP BY target_key
            """,
            (
                entry_key,
                bucket,
            ),
        ).fetchall()
    )

    prospective = {}

    for row in prospective_rows:
        target_key = int(
            row["target_key"]
        )

        completed = int(
            row["completed"]
            or 0
        )

        hits = int(
            row["hits"]
            or 0
        )

        if completed:
            rate = (
                hits
                / completed
            )

            ci_low, ci_high = (
                wilson_interval(
                    hits,
                    completed,
                )
            )

        else:
            rate = None
            ci_low = None
            ci_high = None

        prospective[
            target_key
        ] = {
            "total":
                int(
                    row["total"]
                    or 0
                ),

            "completed":
                completed,

            "hits":
                hits,

            "incomplete":
                int(
                    row["incomplete"]
                    or 0
                ),

            "touch_rate":
                rate,

            "ci_low":
                ci_low,

            "ci_high":
                ci_high,
        }

    current_tracked = (
        connection.execute(
            """
            SELECT 1

            FROM micro_multiplier_opportunities

            WHERE market_ticker = ?
              AND side = ?
              AND entry_price_key = ?
              AND time_bucket = ?

            LIMIT 1
            """,
            (
                str(
                    market_ticker
                ),
                str(
                    side
                ).lower(),
                entry_key,
                bucket,
            ),
        ).fetchone()
        is not None
    )

    combined = []

    for atlas in atlas_rows:
        target_key = int(
            atlas[
                "target_price_key"
            ]
        )

        live = prospective.get(
            target_key,
            {
                "total": 0,
                "completed": 0,
                "hits": 0,
                "incomplete": 0,
                "touch_rate": None,
                "ci_low": None,
                "ci_high": None,
            },
        )

        historical_lead = (
            int(
                atlas[
                    "observations"
                ]
            )
            >= 50
            and float(
                atlas[
                    "ci_low"
                ]
            )
            > float(
                atlas[
                    "break_even_touch"
                ]
            )
        )

        live_validated = (
            live[
                "completed"
            ]
            >= 50
            and live[
                "ci_low"
            ]
            is not None
            and live[
                "ci_low"
            ]
            > float(
                atlas[
                    "break_even_touch"
                ]
            )
        )

        if live_validated:
            status = (
                "LIVE-VALIDATED MICRO"
            )

        elif historical_lead:
            status = (
                "HISTORICAL MICRO LEAD"
            )

        else:
            status = "RESEARCH"

        combined.append(
            {
                "target_price":
                    float(
                        atlas[
                            "target_price"
                        ]
                    ),

                "target_price_key":
                    target_key,

                "multiplier":
                    float(
                        atlas[
                            "multiplier"
                        ]
                    ),

                "observations":
                    int(
                        atlas[
                            "observations"
                        ]
                    ),

                "historical_hits":
                    int(
                        atlas[
                            "hits"
                        ]
                    ),

                "touch_rate":
                    float(
                        atlas[
                            "touch_rate"
                        ]
                    ),

                "ci_low":
                    float(
                        atlas[
                            "ci_low"
                        ]
                    ),

                "ci_high":
                    float(
                        atlas[
                            "ci_high"
                        ]
                    ),

                "break_even_touch":
                    float(
                        atlas[
                            "break_even_touch"
                        ]
                    ),

                "conservative_edge":
                    float(
                        atlas[
                            "conservative_edge"
                        ]
                    ),

                "limit_only_roi":
                    float(
                        atlas[
                            "limit_only_roi"
                        ]
                    ),

                "live_total":
                    live[
                        "total"
                    ],

                "live_completed":
                    live[
                        "completed"
                    ],

                "live_hits":
                    live[
                        "hits"
                    ],

                "live_incomplete":
                    live[
                        "incomplete"
                    ],

                "live_touch_rate":
                    live[
                        "touch_rate"
                    ],

                "live_ci_low":
                    live[
                        "ci_low"
                    ],

                "live_ci_high":
                    live[
                        "ci_high"
                    ],

                "status":
                    status,
            }
        )

    source_market_count = (
        int(
            atlas_rows[0][
                "source_market_count"
            ]
        )
        if atlas_rows
        else 0
    )

    generated_at_ms = (
        int(
            atlas_rows[0][
                "generated_at_ms"
            ]
        )
        if atlas_rows
        else None
    )

    return {
        "market_ticker":
            str(
                market_ticker
            ),

        "side":
            str(
                side
            ).lower(),

        "entry_ask":
            entry_ask,

        "entry_price_key":
            entry_key,

        "time_bucket":
            bucket,

        "source_market_count":
            source_market_count,

        "atlas_generated_at_ms":
            generated_at_ms,

        "current_tracked":
            current_tracked,

        "rows":
            combined,
    }


def live_micro_states_signature(
    states,
):
    if not states:
        return ()

    result = []

    for key in sorted(
        states
    ):
        state = states[
            key
        ]

        result.append(
            (
                key,
                state.get(
                    "entry_price_key"
                ),
                state.get(
                    "time_bucket"
                ),
                state.get(
                    "current_tracked"
                ),
                tuple(
                    (
                        row[
                            "target_price_key"
                        ],
                        row[
                            "live_completed"
                        ],
                        row[
                            "live_hits"
                        ],
                        row[
                            "live_incomplete"
                        ],
                        row[
                            "status"
                        ],
                    )
                    for row
                    in state.get(
                        "rows",
                        [],
                    )
                ),
            )
        )

    return tuple(
        result
    )


def build_micro_atlas(
    connection,
):
    """
    Build the historical cheap-contract target atlas.

    IMPORTANT:
    research_markets() excludes the already-spent
    locked-final period. This atlas is discovery
    research, not a new untouched validation set.

    One observation is used per:
        market
        side
        exact 0.1c entry level
        time bucket

    A target counts as HIT if the historical side price
    later reaches that absolute target before expiry.

    Candle trigger bars are excluded from the future
    path to avoid counting movement that occurred before
    the observed candle close.
    """

    markets = research_markets(
        connection
    )

    series_map = _build_series_map(
        connection,
        markets,
    )

    targets = [
        (
            float(cents),
            float(cents) / 100.0,
        )
        for cents
        in MICRO_TARGET_CENTS
    ]

    stats = defaultdict(
        lambda: {
            "observations": 0,
            "hits": 0,
            "markets": set(),
        }
    )

    qualifying_occurrences = 0

    for market in markets:
        ticker = str(
            market["ticker"]
        )

        series = series_map.get(
            ticker
        )

        if not series:
            continue

        for side in (
            "yes",
            "no",
        ):
            # One occurrence for each exact
            # entry-level/time-bucket state.
            seen = set()

            for index, observation in enumerate(
                series
            ):
                bucket = time_bucket(
                    observation.seconds_remaining
                )

                if bucket == "unknown":
                    continue

                observed_price = float(
                    _side_close(
                        observation,
                        side,
                    )
                )

                if not (
                    MICRO_MIN_PRICE
                    <= observed_price
                    <= MICRO_MAX_PRICE
                ):
                    continue

                entry_key = price_key(
                    observed_price
                )

                entry_price = (
                    entry_key
                    / 1000.0
                )

                if not (
                    MICRO_MIN_PRICE
                    <= entry_price
                    <= MICRO_MAX_PRICE
                ):
                    continue

                occurrence_key = (
                    entry_key,
                    bucket,
                )

                if occurrence_key in seen:
                    continue

                # A candle's high/low happened sometime
                # BEFORE its close, so don't use the
                # trigger candle as future evidence.
                if (
                    getattr(
                        observation,
                        "source",
                        None,
                    )
                    == "candle"
                ):
                    future = (
                        series[
                            index + 1:
                        ]
                    )

                else:
                    future = (
                        series[
                            index:
                        ]
                    )

                if not future:
                    continue

                seen.add(
                    occurrence_key
                )

                qualifying_occurrences += 1

                future_high = max(
                    float(
                        _side_low_high(
                            future_observation,
                            side,
                        )[1]
                    )
                    for future_observation
                    in future
                )

                for (
                    target_cents,
                    target_price,
                ) in targets:
                    if (
                        target_price
                        <= entry_price
                        + EPSILON
                    ):
                        continue

                    target_key = (
                        price_key(
                            target_price
                        )
                    )

                    key = (
                        entry_key,
                        bucket,
                        target_key,
                    )

                    item = stats[
                        key
                    ]

                    item[
                        "observations"
                    ] += 1

                    item[
                        "markets"
                    ].add(
                        ticker
                    )

                    if (
                        future_high
                        + EPSILON
                        >= target_price
                    ):
                        item[
                            "hits"
                        ] += 1

    now_ms = int(
        time.time()
        * 1000
    )

    connection.execute(
        """
        DELETE FROM
            micro_multiplier_atlas
        """
    )

    rows_written = 0

    for (
        entry_key,
        bucket,
        target_key,
    ), item in stats.items():

        n = int(
            item[
                "observations"
            ]
        )

        hits = int(
            item[
                "hits"
            ]
        )

        if n <= 0:
            continue

        entry_price = (
            entry_key
            / 1000.0
        )

        target_price = (
            target_key
            / 1000.0
        )

        multiplier = (
            target_price
            / entry_price
        )

        touch_rate = (
            hits / n
        )

        ci_low, ci_high = (
            wilson_interval(
                hits,
                n,
            )
        )

        break_even_touch = (
            entry_price
            / target_price
        )

        conservative_edge = (
            ci_low
            - break_even_touch
        )

        # Very conservative model:
        #
        # HIT:
        #   buy at entry, sell at target.
        #
        # MISS:
        #   lose the entire entry.
        #
        # Settlement wins after a miss get zero credit.
        limit_only_ev = (
            touch_rate
            * target_price
            - entry_price
        )

        limit_only_roi = (
            limit_only_ev
            / entry_price
        )

        connection.execute(
            """
            INSERT INTO
            micro_multiplier_atlas (
                entry_price_key,
                time_bucket,
                target_price_key,

                entry_price,
                target_price,
                multiplier,

                observations,
                unique_markets,
                hits,

                touch_rate,
                ci_low,
                ci_high,

                break_even_touch,
                conservative_edge,

                limit_only_ev,
                limit_only_roi,

                source_market_count,
                generated_at_ms
            )
            VALUES (
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?
            )
            """,
            (
                entry_key,
                bucket,
                target_key,

                entry_price,
                target_price,
                multiplier,

                n,
                len(
                    item[
                        "markets"
                    ]
                ),
                hits,

                touch_rate,
                ci_low,
                ci_high,

                break_even_touch,
                conservative_edge,

                limit_only_ev,
                limit_only_roi,

                len(
                    markets
                ),
                now_ms,
            ),
        )

        rows_written += 1

    connection.commit()

    return {
        "markets":
            len(
                markets
            ),

        "occurrences":
            qualifying_occurrences,

        "rows":
            rows_written,
    }


def print_best_rows(
    connection,
):
    rows = connection.execute(
        """
        SELECT *
        FROM micro_multiplier_atlas

        WHERE observations >= 50
          AND ci_low > break_even_touch

        ORDER BY
            conservative_edge DESC,
            limit_only_roi DESC,
            observations DESC

        LIMIT 50
        """
    ).fetchall()

    print()
    print(
        "=" * 128
    )

    print(
        "MICRO MULTIPLIER ATLAS · "
        "STRONGEST DISCOVERY LEADS"
    )

    print(
        "=" * 128
    )

    print(
        f"{'ENTRY':>7}"
        f" {'TARGET':>8}"
        f" {'MULT':>7}"
        f" {'TIME':>8}"
        f" {'N':>6}"
        f" {'HITS':>6}"
        f" {'TOUCH':>9}"
        f" {'CI LOW':>9}"
        f" {'NEEDED':>9}"
        f" {'CI EDGE':>9}"
        f" {'ROI*':>9}"
    )

    print(
        "-" * 128
    )

    for row in rows:
        print(
            f"{row['entry_price'] * 100:>6.1f}c"
            f" "
            f"{row['target_price'] * 100:>7.1f}c"
            f" "
            f"{row['multiplier']:>6.1f}x"
            f" "
            f"{row['time_bucket']:>8}"
            f" "
            f"{row['observations']:>6}"
            f" "
            f"{row['hits']:>6}"
            f" "
            f"{row['touch_rate'] * 100:>8.1f}%"
            f" "
            f"{row['ci_low'] * 100:>8.1f}%"
            f" "
            f"{row['break_even_touch'] * 100:>8.1f}%"
            f" "
            f"{row['conservative_edge'] * 100:>+8.1f}%"
            f" "
            f"{row['limit_only_roi'] * 100:>+8.1f}%"
        )

    print()

    print(
        "*ROI is a gross historical proxy that gives "
        "zero credit for settlement wins after a target miss."
    )

    print(
        "Historical price touches do not prove a resting "
        "limit order would have filled."
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--db",
        required=True,
    )

    args = parser.parse_args()

    connection = connect(
        args.db
    )

    try:
        init_db(
            connection
        )

        result = (
            build_micro_atlas(
                connection
            )
        )

        print(
            "MICRO ATLAS BUILT | "
            f"markets={result['markets']} | "
            f"occurrences={result['occurrences']} | "
            f"rows={result['rows']}"
        )

        print_best_rows(
            connection
        )

    finally:
        connection.close()


if __name__ == "__main__":
    main()
