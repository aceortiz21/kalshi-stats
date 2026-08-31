import inspect

from kalshi_stats.database import connect, init_db
from kalshi_stats.ml_dataset import (
    EXCLUDED_LEAKAGE_COLUMNS,
    FEATURE_CLASSIFICATION,
    FEATURE_COLUMNS,
    UNAVAILABLE_CANDIDATES,
    build_ml_dataset,
)


def _insert_feature_row(connection, *, ticker, timestamp, result, missing=None):
    missing = set(missing or ())
    values = {
        "market_ticker": ticker,
        "observed_ts": timestamp,
        "feature_version": 2,
        "result": result,
        "candle_source": "test",
        "btc_source": "test",
        "btc_ts": timestamp * 1000 - 1000,
    }
    for index, column in enumerate(FEATURE_COLUMNS, start=1):
        values[column] = None if column in missing else float(index) / 100.0
    values.update(
        {
            "kalshi_price_close": 0.5,
            "kalshi_price_low": 0.49,
            "kalshi_price_high": 0.51,
            "yes_bid_close": 0.48,
            "yes_ask_close": 0.52,
            "seconds_remaining": 300,
            "threshold": 100.0,
            "btc_age_ms": 1000,
            "spot": 101.0,
            "threshold_distance_dollars": 1.0,
            "threshold_distance_pct": 0.01,
            "threshold_distance_bps": 100.0,
        }
    )
    columns = tuple(values)
    connection.execute(
        f"INSERT INTO historical_market_features ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})",
        tuple(values[column] for column in columns),
    )


def test_dataset_uses_explicit_whitelist_and_correct_separate_labels():
    connection = connect(":memory:")
    try:
        init_db(connection)
        _insert_feature_row(
            connection, ticker="YES-MARKET", timestamp=100, result="yes"
        )
        _insert_feature_row(
            connection, ticker="NO-MARKET", timestamp=200, result="no"
        )

        dataset = build_ml_dataset(connection)

        assert dataset.feature_names == FEATURE_COLUMNS
        assert "result" not in dataset.feature_names
        assert EXCLUDED_LEAKAGE_COLUMNS == ("result",)
        assert dataset.targets == (1, 0)
        assert dataset.market_probabilities == (0.5, 0.5)
        assert "SELECT *" not in inspect.getsource(build_ml_dataset).upper()
    finally:
        connection.close()


def test_every_historical_column_is_explicitly_classified():
    connection = connect(":memory:")
    try:
        init_db(connection)
        schema_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(historical_market_features)"
            )
        }
        assert schema_columns == set(FEATURE_CLASSIFICATION)
        assert {
            column
            for column, classification in FEATURE_CLASSIFICATION.items()
            if classification == "SAFE_CONTEMPORANEOUS"
        } == set(FEATURE_COLUMNS)
        assert set(UNAVAILABLE_CANDIDATES.values()) == {"UNAVAILABLE"}
        assert not set(UNAVAILABLE_CANDIDATES) & schema_columns
    finally:
        connection.close()


def test_missing_feature_value_is_loaded_deterministically():
    connection = connect(":memory:")
    try:
        init_db(connection)
        _insert_feature_row(
            connection,
            ticker="YES-MARKET",
            timestamp=100,
            result="yes",
            missing={"return_30s"},
        )
        first = build_ml_dataset(connection)
        second = build_ml_dataset(connection)
        feature_index = FEATURE_COLUMNS.index("return_30s")
        assert first.features[0][feature_index] is None
        assert first == second
    finally:
        connection.close()
