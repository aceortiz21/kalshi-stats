from kalshi_stats.challenger_generator import (
    main_discovery_candidates,
    micro_discovery_candidates,
)


def test_main_generator_finds_positive_context():
    rows = []

    for index in range(6):
        rows.append(
            {
                "profile_id":
                    "RAW",

                "entry_ask":
                    .61,

                "seconds_remaining":
                    330,

                "entry_notional":
                    .01,

                "bid_proxy_gross_pnl":
                    .001,
            }
        )

    candidates = (
        main_discovery_candidates(
            rows
        )
    )

    assert candidates

    best = candidates[0]

    assert (
        best[
            "profile_id"
        ]
        == "RAW"
    )

    assert (
        best[
            "price_name"
        ]
        == "60-62"
    )

    assert (
        best[
            "time_name"
        ]
        == "5-6m"
    )

    assert (
        best[
            "discovery_n"
        ]
        == 6
    )


def test_micro_generator_ignores_negative_cells():
    rows = []

    for index in range(10):
        rows.append(
            {
                "entry_price_key":
                    1,

                "entry_ask":
                    .001,

                "time_bucket":
                    "3-4m",

                "target_price":
                    .002,

                "status":
                    (
                        "HIT"
                        if index < 8
                        else "MISS"
                    ),
            }
        )

    for index in range(10):
        rows.append(
            {
                "entry_price_key":
                    2,

                "entry_ask":
                    .002,

                "time_bucket":
                    "3-4m",

                "target_price":
                    .004,

                "status":
                    "MISS",
            }
        )

    candidates = (
        micro_discovery_candidates(
            rows
        )
    )

    assert len(
        candidates
    ) == 1

    assert (
        candidates[0][
            "entry_price_key"
        ]
        == 1
    )
