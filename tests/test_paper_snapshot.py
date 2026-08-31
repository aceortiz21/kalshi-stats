from kalshi_stats.paper_snapshot import (
    diagnose_no_fill,
)


def test_no_fill_detects_price_move():
    trade = {
        "side":
            "yes",

        "entry_limit":
            .46,
    }

    book = {
        "ts_ms":
            123,

        "yes_ask":
            .47,

        "yes_ask_size":
            1000,
    }

    result = diagnose_no_fill(
        trade,
        book,
    )

    assert (
        result[
            "diagnosis"
        ]
        ==
        "PRICE_MOVED_ABOVE_IOC_LIMIT"
    )


def test_no_fill_detects_missing_book():
    trade = {
        "side":
            "yes",

        "entry_limit":
            .46,
    }

    result = diagnose_no_fill(
        trade,
        None,
    )

    assert (
        result[
            "diagnosis"
        ]
        ==
        "NO_BOOK"
    )
