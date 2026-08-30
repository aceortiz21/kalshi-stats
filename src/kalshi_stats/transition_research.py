from __future__ import annotations

import argparse
import math
import sqlite3
from statistics import median

from .analytics import (
    _build_series_map,
    _settled_markets_with_data,
    _side_close,
    _side_low_high,
)


# Frozen boundary of the untouched final evaluation set.
LOCKED_FINAL_START = "2026-08-17T13:15:00Z"


def wilson_interval(
    successes,
    total,
    z=1.96,
):
    if total <= 0:
        return None, None

    p = (
        successes
        / total
    )

    denom = (
        1
        + z * z / total
    )

    centre = (
        p
        + z * z / (
            2 * total
        )
    )

    margin = (
        z
        * math.sqrt(
            (
                p * (1 - p)
                + z * z
                / (4 * total)
            )
            / total
        )
    )

    return (
        max(
            0.0,
            (
                centre
                - margin
            )
            / denom,
        ),

        min(
            1.0,
            (
                centre
                + margin
            )
            / denom,
        ),
    )


def research_markets(
    connection,
):
    """
    Return only markets before the frozen final
    evaluation period.
    """

    markets = (
        _settled_markets_with_data(
            connection
        )
    )

    return [
        market
        for market in markets
        if (
            market["close_time"]
            and market["close_time"]
            < LOCKED_FINAL_START
        )
    ]


def find_occurrences(
    *,
    markets,
    series_map,
    current_price,
    tolerance,
    seconds_min,
    seconds_max,
    episodes=False,
    cooldown_seconds=60,
):
    occurrences = []

    market_result = {
        str(
            market["ticker"]
        ):
        str(
            market["result"]
        )

        for market in markets
    }

    for market in markets:
        ticker = str(
            market["ticker"]
        )

        series = (
            series_map.get(
                ticker
            )
        )

        if not series:
            continue

        market_matches = []

        for side in (
            "yes",
            "no",
        ):
            in_band = False
            last_exit_ts = (
                -10**18
            )

            for (
                index,
                observation,
            ) in enumerate(
                series
            ):
                price = (
                    _side_close(
                        observation,
                        side,
                    )
                )

                time_ok = (
                    seconds_min
                    <= observation.seconds_remaining
                    <= seconds_max
                )

                price_ok = (
                    abs(
                        price
                        - current_price
                    )
                    <= (
                        tolerance
                        + 1e-12
                    )
                )

                matched = (
                    time_ok
                    and price_ok
                )

                if matched:
                    if (
                        not in_band
                        and (
                            observation.observed_ts
                            - last_exit_ts
                            >= cooldown_seconds
                        )
                    ):
                        market_matches.append(
                            (
                                observation.observed_ts,
                                index,
                                side,
                                price,
                            )
                        )

                        in_band = True

                        if not episodes:
                            break

                    continue

                if in_band:
                    in_band = False
                    last_exit_ts = (
                        observation.observed_ts
                    )

        if not market_matches:
            continue

        market_matches.sort(
            key=lambda item: (
                item[0],
                item[2],
            )
        )

        if not episodes:
            market_matches = (
                market_matches[:1]
            )

        for (
            _,
            index,
            side,
            price,
        ) in market_matches:
            observation = (
                series[index]
            )

            # Candle entry is represented by the close.
            # Do not use that candle's earlier high/low
            # as future information.
            if (
                observation.source
                == "candle"
            ):
                future = (
                    series[
                        index + 1:
                    ]
                )
            else:
                future = (
                    series[index:]
                )

            occurrences.append(
                {
                    "ticker":
                        ticker,

                    "side":
                        side,

                    "entry_ts":
                        observation.observed_ts,

                    "entry_price":
                        price,

                    "seconds_remaining":
                        observation.seconds_remaining,

                    "future":
                        future,

                    "eventual_win":
                        (
                            market_result[
                                ticker
                            ]
                            == side
                        ),
                }
            )

    return occurrences


def target_hit(
    occurrence,
    *,
    target,
    horizon_seconds=None,
):
    entry = float(
        occurrence[
            "entry_price"
        ]
    )

    if abs(
        target - entry
    ) < 1e-12:
        return True

    entry_ts = int(
        occurrence[
            "entry_ts"
        ]
    )

    for observation in occurrence[
        "future"
    ]:
        elapsed = (
            observation.observed_ts
            - entry_ts
        )

        if (
            horizon_seconds
            is not None
            and elapsed
            > horizon_seconds
        ):
            break

        low, high = (
            _side_low_high(
                observation,
                occurrence[
                    "side"
                ],
            )
        )

        if (
            target > entry
            and high >= target
        ):
            return True

        if (
            target < entry
            and low <= target
        ):
            return True

    return False


def future_extreme(
    occurrence,
    *,
    high,
):
    values = []

    for observation in occurrence[
        "future"
    ]:
        low_value, high_value = (
            _side_low_high(
                observation,
                occurrence[
                    "side"
                ],
            )
        )

        values.append(
            (
                high_value
                if high
                else low_value
            )
        )

    if not values:
        return None

    return (
        max(values)
        if high
        else min(values)
    )


def summarize_transition(
    *,
    occurrences,
    targets,
    horizons,
):
    n = len(
        occurrences
    )

    unique_markets = len(
        {
            occurrence[
                "ticker"
            ]
            for occurrence
            in occurrences
        }
    )

    settlement_wins = sum(
        occurrence[
            "eventual_win"
        ]
        for occurrence
        in occurrences
    )

    settlement_rate = (
        settlement_wins / n
        if n
        else None
    )

    settlement_ci = (
        wilson_interval(
            settlement_wins,
            n,
        )
        if n
        else (
            None,
            None,
        )
    )

    target_results = {}

    for target in targets:
        target_results[
            target
        ] = {}

        for horizon in horizons:
            hits = sum(
                target_hit(
                    occurrence,
                    target=target,
                    horizon_seconds=(
                        horizon
                    ),
                )
                for occurrence
                in occurrences
            )

            rate = (
                hits / n
                if n
                else None
            )

            ci = (
                wilson_interval(
                    hits,
                    n,
                )
                if n
                else (
                    None,
                    None,
                )
            )

            target_results[
                target
            ][horizon] = {
                "hits": hits,
                "rate": rate,
                "ci_low": ci[0],
                "ci_high": ci[1],
            }

    highs = [
        value
        for value in (
            future_extreme(
                occurrence,
                high=True,
            )
            for occurrence
            in occurrences
        )
        if value is not None
    ]

    lows = [
        value
        for value in (
            future_extreme(
                occurrence,
                high=False,
            )
            for occurrence
            in occurrences
        )
        if value is not None
    ]

    return {
        "n":
            n,

        "unique_markets":
            unique_markets,

        "settlement_wins":
            settlement_wins,

        "settlement_rate":
            settlement_rate,

        "settlement_ci_low":
            settlement_ci[0],

        "settlement_ci_high":
            settlement_ci[1],

        "median_future_high":
            (
                median(highs)
                if highs
                else None
            ),

        "median_future_low":
            (
                median(lows)
                if lows
                else None
            ),

        "targets":
            target_results,
    }


def pct(
    value,
):
    if value is None:
        return "-"

    return (
        f"{value * 100:.1f}%"
    )


def cents(
    value,
):
    if value is None:
        return "-"

    return (
        f"{value * 100:.2f}c"
    )


def print_summary(
    *,
    summary,
    current_price,
    tolerance,
    seconds_min,
    seconds_max,
    horizons,
):
    print()
    print(
        "=" * 100
    )

    print(
        "HISTORICAL STATE TRANSITION RESEARCH"
    )

    print(
        "=" * 100
    )

    print(
        "Sample: PRE-LOCKED research data only"
    )

    print(
        "State:",
        (
            f"{current_price * 100:.2f}c "
            f"±{tolerance * 100:.2f}c"
        ),
    )

    print(
        "Time remaining:",
        f"{seconds_min}-{seconds_max}s",
    )

    print(
        "Occurrences:",
        summary["n"],
    )

    print(
        "Unique markets:",
        summary[
            "unique_markets"
        ],
    )

    print()

    print(
        "P(settles on this side):",
        pct(
            summary[
                "settlement_rate"
            ]
        ),
        (
            "["
            + pct(
                summary[
                    "settlement_ci_low"
                ]
            )
            + ", "
            + pct(
                summary[
                    "settlement_ci_high"
                ]
            )
            + "]"
        ),
    )

    print(
        "Median future high:",
        cents(
            summary[
                "median_future_high"
            ]
        ),
    )

    print(
        "Median future low:",
        cents(
            summary[
                "median_future_low"
            ]
        ),
    )

    print()
    print(
        "TARGET TRANSITIONS"
    )

    print(
        "-" * 100
    )

    header = (
        f"{'TARGET':>10}"
    )

    for horizon in horizons:
        label = (
            "SETTLE"
            if horizon is None
            else f"{horizon}s"
        )

        header += (
            f" | {label:^24}"
        )

    print(
        header
    )

    print(
        "-" * 100
    )

    for (
        target,
        horizon_results,
    ) in summary[
        "targets"
    ].items():

        row = (
            f"{target * 100:>9.2f}c"
        )

        for horizon in horizons:
            result = (
                horizon_results[
                    horizon
                ]
            )

            rate = pct(
                result[
                    "rate"
                ]
            )

            ci_low = pct(
                result[
                    "ci_low"
                ]
            )

            ci_high = pct(
                result[
                    "ci_high"
                ]
            )

            text = (
                f"{rate} "
                f"[{ci_low},{ci_high}]"
            )

            row += (
                f" | {text:^24}"
            )

        print(
            row
        )

    print()



def competing_barrier_result(
    occurrence,
    *,
    up_delta,
    down_delta,
    horizon_seconds=None,
):
    """
    Determine which relative price barrier is reached
    first.

    Example:
        entry 35c
        up_delta 15c
        down_delta 5c

    asks whether 50c occurs before 30c.

    If both barriers appear inside the same historical
    OHLC candle, ordering is unknowable and the result is
    AMBIGUOUS rather than guessed.
    """

    entry = float(
        occurrence[
            "entry_price"
        ]
    )

    upper = (
        entry
        + float(
            up_delta
        )
    )

    lower = (
        entry
        - float(
            down_delta
        )
    )

    entry_ts = int(
        occurrence[
            "entry_ts"
        ]
    )

    for observation in occurrence[
        "future"
    ]:
        elapsed = (
            observation.observed_ts
            - entry_ts
        )

        if (
            horizon_seconds
            is not None
            and elapsed
            > horizon_seconds
        ):
            break

        low, high = (
            _side_low_high(
                observation,
                occurrence[
                    "side"
                ],
            )
        )

        upper_hit = (
            upper <= 1.0
            and high >= upper
        )

        lower_hit = (
            lower >= 0.0
            and low <= lower
        )

        if (
            upper_hit
            and lower_hit
        ):
            return "AMBIGUOUS"

        if upper_hit:
            return "UPPER"

        if lower_hit:
            return "LOWER"

    # Settlement is a real terminal binary price: 1 or 0.
    # Include it only if the requested horizon reaches
    # settlement.
    reaches_settlement = (
        horizon_seconds is None
        or float(
            occurrence[
                "seconds_remaining"
            ]
        )
        <= horizon_seconds
    )

    if reaches_settlement:
        settlement = (
            1.0
            if occurrence[
                "eventual_win"
            ]
            else 0.0
        )

        upper_hit = (
            upper <= 1.0
            and settlement
            >= upper
        )

        lower_hit = (
            lower >= 0.0
            and settlement
            <= lower
        )

        if upper_hit:
            return "UPPER"

        if lower_hit:
            return "LOWER"

    return "NONE"


def summarize_competing_barriers(
    *,
    occurrences,
    barrier_pairs,
    horizons,
):
    output = {}

    for (
        up_delta,
        down_delta,
    ) in barrier_pairs:

        pair_key = (
            up_delta,
            down_delta,
        )

        output[
            pair_key
        ] = {}

        for horizon in horizons:
            counts = {
                "UPPER": 0,
                "LOWER": 0,
                "AMBIGUOUS": 0,
                "NONE": 0,
            }

            for occurrence in occurrences:
                result = (
                    competing_barrier_result(
                        occurrence,
                        up_delta=(
                            up_delta
                        ),
                        down_delta=(
                            down_delta
                        ),
                        horizon_seconds=(
                            horizon
                        ),
                    )
                )

                counts[
                    result
                ] += 1

            ordered_n = (
                counts[
                    "UPPER"
                ]
                + counts[
                    "LOWER"
                ]
            )

            resolved_n = (
                ordered_n
                + counts[
                    "AMBIGUOUS"
                ]
            )

            upper_given_order = (
                counts[
                    "UPPER"
                ]
                / ordered_n
                if ordered_n
                else None
            )

            conservative_upper = (
                counts[
                    "UPPER"
                ]
                / resolved_n
                if resolved_n
                else None
            )

            optimistic_upper = (
                (
                    counts[
                        "UPPER"
                    ]
                    + counts[
                        "AMBIGUOUS"
                    ]
                )
                / resolved_n
                if resolved_n
                else None
            )

            output[
                pair_key
            ][horizon] = {
                **counts,

                "ordered_n":
                    ordered_n,

                "upper_given_order":
                    upper_given_order,

                "conservative_upper":
                    conservative_upper,

                "optimistic_upper":
                    optimistic_upper,
            }

    return output


def parse_barrier_pairs(
    text,
):
    pairs = []

    if not text:
        return pairs

    for raw_pair in text.split(","):
        raw_pair = (
            raw_pair.strip()
        )

        if not raw_pair:
            continue

        if ":" not in raw_pair:
            raise ValueError(
                "Barrier pairs must use "
                "UP_CENTS:DOWN_CENTS"
            )

        up_text, down_text = (
            raw_pair.split(
                ":",
                1,
            )
        )

        up = (
            float(
                up_text.strip()
            )
            / 100.0
        )

        down = (
            float(
                down_text.strip()
            )
            / 100.0
        )

        if (
            up <= 0
            or down <= 0
        ):
            raise ValueError(
                "Barrier distances must be positive"
            )

        pairs.append(
            (
                up,
                down,
            )
        )

    return pairs


def print_competing_barriers(
    *,
    summary,
    horizons,
):
    if not summary:
        return

    print()
    print(
        "COMPETING BARRIERS / FIRST PASSAGE"
    )

    print(
        "-" * 118
    )

    print(
        "Question: does the upside barrier occur "
        "before the downside barrier?"
    )

    print(
        "AMBIG = both appeared inside one OHLC candle; "
        "historical ordering is unknowable."
    )

    print()

    header = (
        f"{'UP/DOWN':>13}"
        f" | {'HORIZON':>8}"
        f" | {'UP FIRST':>9}"
        f" | {'DOWN FIRST':>10}"
        f" | {'AMBIG':>7}"
        f" | {'NONE':>7}"
        f" | {'P(UP|ORDERED)':>14}"
        f" | {'AMBIG RANGE':>19}"
    )

    print(
        header
    )

    print(
        "-" * 118
    )

    for (
        up_delta,
        down_delta,
    ), horizon_rows in summary.items():

        pair_label = (
            f"+{up_delta * 100:.1f}c/"
            f"-{down_delta * 100:.1f}c"
        )

        for horizon in horizons:
            row = (
                horizon_rows[
                    horizon
                ]
            )

            horizon_label = (
                "SETTLE"
                if horizon is None
                else f"{horizon}s"
            )

            probability = (
                "-"
                if row[
                    "upper_given_order"
                ]
                is None
                else (
                    f"{row['upper_given_order'] * 100:.1f}%"
                )
            )

            if (
                row[
                    "conservative_upper"
                ]
                is None
            ):
                ambiguity_range = "-"
            else:
                ambiguity_range = (
                    f"{row['conservative_upper'] * 100:.1f}%"
                    "-"
                    f"{row['optimistic_upper'] * 100:.1f}%"
                )

            print(
                f"{pair_label:>13}"
                f" | {horizon_label:>8}"
                f" | {row['UPPER']:>9}"
                f" | {row['LOWER']:>10}"
                f" | {row['AMBIGUOUS']:>7}"
                f" | {row['NONE']:>7}"
                f" | {probability:>14}"
                f" | {ambiguity_range:>19}"
            )

    print()



def parse_csv_floats(
    text,
):
    return [
        float(part.strip())
        for part in text.split(",")
        if part.strip()
    ]


def parse_horizons(
    text,
):
    output = []

    for part in text.split(","):
        value = (
            part.strip().lower()
        )

        if not value:
            continue

        if value in {
            "settle",
            "rest",
            "all",
        }:
            output.append(
                None
            )
        else:
            output.append(
                int(value)
            )

    return output


def main():
    parser = (
        argparse.ArgumentParser()
    )

    parser.add_argument(
        "--db",
        required=True,
    )

    parser.add_argument(
        "--price-cents",
        type=float,
        required=True,
    )

    parser.add_argument(
        "--tolerance-cents",
        type=float,
        default=0.0,
    )

    parser.add_argument(
        "--targets-cents",
        required=True,
    )

    parser.add_argument(
        "--seconds-min",
        type=int,
        default=0,
    )

    parser.add_argument(
        "--seconds-max",
        type=int,
        default=900,
    )

    parser.add_argument(
        "--horizons",
        default="60,120,300,settle",
    )

    parser.add_argument(
        "--barriers-cents",
        default="",
        help=(
            "Relative competing barriers, e.g. "
            "'15:5,5:5' means +15c/-5c and +5c/-5c"
        ),
    )

    parser.add_argument(
        "--episodes",
        action="store_true",
    )

    parser.add_argument(
        "--cooldown-seconds",
        type=int,
        default=60,
    )

    args = (
        parser.parse_args()
    )

    connection = sqlite3.connect(
        args.db
    )

    connection.row_factory = (
        sqlite3.Row
    )

    try:
        markets = (
            research_markets(
                connection
            )
        )

        series_map = (
            _build_series_map(
                connection,
                markets,
            )
        )

        current_price = (
            args.price_cents
            / 100.0
        )

        tolerance = (
            args.tolerance_cents
            / 100.0
        )

        targets = [
            value / 100.0
            for value
            in parse_csv_floats(
                args.targets_cents
            )
        ]

        horizons = (
            parse_horizons(
                args.horizons
            )
        )

        barrier_pairs = (
            parse_barrier_pairs(
                args.barriers_cents
            )
        )

        occurrences = (
            find_occurrences(
                markets=markets,
                series_map=series_map,
                current_price=(
                    current_price
                ),
                tolerance=(
                    tolerance
                ),
                seconds_min=(
                    args.seconds_min
                ),
                seconds_max=(
                    args.seconds_max
                ),
                episodes=(
                    args.episodes
                ),
                cooldown_seconds=(
                    args.cooldown_seconds
                ),
            )
        )

        summary = (
            summarize_transition(
                occurrences=(
                    occurrences
                ),
                targets=targets,
                horizons=horizons,
            )
        )

        print_summary(
            summary=summary,
            current_price=(
                current_price
            ),
            tolerance=(
                tolerance
            ),
            seconds_min=(
                args.seconds_min
            ),
            seconds_max=(
                args.seconds_max
            ),
            horizons=horizons,
        )

        if barrier_pairs:
            barrier_summary = (
                summarize_competing_barriers(
                    occurrences=(
                        occurrences
                    ),
                    barrier_pairs=(
                        barrier_pairs
                    ),
                    horizons=horizons,
                )
            )

            print_competing_barriers(
                summary=(
                    barrier_summary
                ),
                horizons=horizons,
            )

    finally:
        connection.close()


if __name__ == "__main__":
    main()
