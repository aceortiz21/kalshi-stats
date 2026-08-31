from kalshi_stats.historical_adaptive_v2 import (
    SettlementEvidence,
    beta_summary,
    candidate_break_even_probability,
    is_settlement_strategy,
)


def test_beta_lcb_below_mean():
    result = beta_summary(
        wins=90,
        losses=10,
        z=2.326,
    )

    assert (
        result[
            "lower"
        ]
        <
        result[
            "mean"
        ]
    )


def test_settlement_strategy_detection():
    assert (
        is_settlement_strategy(
            "grid:v1:p85-94:t10-12m:settle"
        )
    )

    assert not (
        is_settlement_strategy(
            "grid:v1:p85-94:t10-12m:tp10_sl05"
        )
    )


def test_break_even_includes_fee():
    trade = {
        "count":
            2.0,

        "entry_notional":
            1.0,

        "entry_fee":
            .04,
    }

    result = (
        candidate_break_even_probability(
            trade
        )
    )

    assert round(
        result,
        8,
    ) == .52


def test_settlement_evidence_tracks_recent():
    evidence = SettlementEvidence(
        20
    )

    for index in range(
        30
    ):
        evidence.add(
            {
                "exit_price":
                    (
                        1.0
                        if index % 2 == 0
                        else 0.0
                    ),

                "market_ticker":
                    f"M{index}",
            }
        )

    assert evidence.n == 30

    assert (
        len(
            evidence.recent
        )
        == 20
    )

    assert (
        len(
            evidence.markets
        )
        == 30
    )
