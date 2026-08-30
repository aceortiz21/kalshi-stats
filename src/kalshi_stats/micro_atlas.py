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
