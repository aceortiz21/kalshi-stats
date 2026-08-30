from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
import json
import time

import websockets

from .btc_features import (
    BTCFeatureEngine,
    BTCSecond,
)
from .database import (
    connect,
    init_db,
)


COINBASE_WS_URL = (
    "wss://advanced-trade-ws.coinbase.com"
)

PRODUCT_ID = "BTC-USD"


def _parse_time_ms(
    value: str,
) -> int:
    """
    Parse Coinbase RFC3339 timestamps, including nanosecond
    timestamps by truncating excess precision to microseconds.
    """

    text = str(value).strip()

    if not text:
        return int(
            time.time() * 1000
        )

    if text.endswith("Z"):
        text = (
            text[:-1]
            + "+00:00"
        )

    if "." in text:
        prefix, suffix = (
            text.split(
                ".",
                1,
            )
        )

        timezone_index = None

        for marker in (
            "+",
            "-",
        ):
            index = suffix.find(
                marker
            )

            if index >= 0:
                timezone_index = index
                break

        if timezone_index is None:
            fractional = suffix
            timezone_text = ""
        else:
            fractional = suffix[
                :timezone_index
            ]

            timezone_text = suffix[
                timezone_index:
            ]

        fractional = (
            fractional[:6]
            .ljust(
                6,
                "0",
            )
        )

        text = (
            prefix
            + "."
            + fractional
            + timezone_text
        )

    return int(
        datetime.fromisoformat(
            text
        ).timestamp()
        * 1000
    )


class CoinbaseBTCState:
    def __init__(
        self,
        *,
        product_id: str = PRODUCT_ID,
    ):
        self.product_id = (
            product_id
        )

        self.feature_engine = (
            BTCFeatureEngine()
        )

        self.bids = {}
        self.asks = {}

        self.ticker_best_bid = None
        self.ticker_best_ask = None

        self.spot = None

        # completed/active trade bars keyed by UTC second
        self.trade_bars = {}

        self.last_emitted_second = None


    def handle_message(
        self,
        message: dict,
    ) -> None:
        channel = message.get(
            "channel"
        )

        events = (
            message.get(
                "events"
            )
            or []
        )

        if channel == "ticker":
            for event in events:
                for ticker in (
                    event.get(
                        "tickers"
                    )
                    or []
                ):
                    if (
                        ticker.get(
                            "product_id"
                        )
                        != self.product_id
                    ):
                        continue

                    try:
                        self.spot = float(
                            ticker["price"]
                        )
                    except (
                        KeyError,
                        TypeError,
                        ValueError,
                    ):
                        pass

                    try:
                        self.ticker_best_bid = (
                            float(
                                ticker[
                                    "best_bid"
                                ]
                            )
                        )
                    except (
                        KeyError,
                        TypeError,
                        ValueError,
                    ):
                        pass

                    try:
                        self.ticker_best_ask = (
                            float(
                                ticker[
                                    "best_ask"
                                ]
                            )
                        )
                    except (
                        KeyError,
                        TypeError,
                        ValueError,
                    ):
                        pass

        elif channel == "market_trades":
            for event in events:
                for trade in (
                    event.get(
                        "trades"
                    )
                    or []
                ):
                    if (
                        trade.get(
                            "product_id"
                        )
                        != self.product_id
                    ):
                        continue

                    try:
                        price = float(
                            trade["price"]
                        )

                        size = float(
                            trade["size"]
                        )

                        ts_ms = (
                            _parse_time_ms(
                                trade["time"]
                            )
                        )
                    except (
                        KeyError,
                        TypeError,
                        ValueError,
                    ):
                        continue

                    maker_side = str(
                        trade.get(
                            "side",
                            "",
                        )
                    ).upper()

                    # Coinbase market_trades side is the MAKER side.
                    # Aggressive flow is therefore the opposite side.
                    if maker_side == "BUY":
                        aggressor = "sell"
                    elif maker_side == "SELL":
                        aggressor = "buy"
                    else:
                        aggressor = "unknown"

                    self.spot = price

                    self.feature_engine.add_trade(
                        ts_ms=ts_ms,
                        price=price,
                        size=size,
                        aggressor=(
                            aggressor
                        ),
                    )

                    second = (
                        ts_ms // 1000
                    )

                    bar = (
                        self.trade_bars.get(
                            second
                        )
                    )

                    if bar is None:
                        self.trade_bars[
                            second
                        ] = [
                            price,
                            price,
                            price,
                            price,
                            size,
                        ]
                    else:
                        bar[1] = max(
                            bar[1],
                            price,
                        )

                        bar[2] = min(
                            bar[2],
                            price,
                        )

                        bar[3] = price
                        bar[4] += size

        elif channel == "l2_data":
            for event in events:
                if (
                    event.get(
                        "product_id"
                    )
                    != self.product_id
                ):
                    continue

                if (
                    event.get(
                        "type"
                    )
                    == "snapshot"
                ):
                    self.bids.clear()
                    self.asks.clear()

                for update in (
                    event.get(
                        "updates"
                    )
                    or []
                ):
                    try:
                        price = float(
                            update[
                                "price_level"
                            ]
                        )

                        quantity = float(
                            update[
                                "new_quantity"
                            ]
                        )
                    except (
                        KeyError,
                        TypeError,
                        ValueError,
                    ):
                        continue

                    side = str(
                        update.get(
                            "side",
                            "",
                        )
                    ).lower()

                    if side in {
                        "bid",
                        "buy",
                    }:
                        book = self.bids

                    elif side in {
                        "ask",
                        "offer",
                        "sell",
                    }:
                        book = self.asks

                    else:
                        continue

                    if quantity <= 0:
                        book.pop(
                            price,
                            None,
                        )
                    else:
                        book[
                            price
                        ] = quantity


    def best_bid(
        self,
    ):
        if self.bids:
            return max(
                self.bids
            )

        return (
            self.ticker_best_bid
        )


    def best_ask(
        self,
    ):
        if self.asks:
            return min(
                self.asks
            )

        return (
            self.ticker_best_ask
        )


    def book_imbalance_top10(
        self,
    ):
        if (
            not self.bids
            or not self.asks
        ):
            return None

        top_bids = sorted(
            self.bids.items(),
            key=lambda item: (
                item[0]
            ),
            reverse=True,
        )[:10]

        top_asks = sorted(
            self.asks.items(),
            key=lambda item: (
                item[0]
            ),
        )[:10]

        bid_quantity = sum(
            quantity
            for _, quantity
            in top_bids
        )

        ask_quantity = sum(
            quantity
            for _, quantity
            in top_asks
        )

        total = (
            bid_quantity
            + ask_quantity
        )

        if total <= 0:
            return None

        return (
            bid_quantity
            - ask_quantity
        ) / total


    def completed_seconds(
        self,
        now_ms: int,
    ):
        """
        Emit through the previous UTC second.

        The one-second delay gives Coinbase's ~250 ms trade batches
        time to arrive before a bar is finalized.
        """

        target_second = (
            now_ms // 1000
            - 1
        )

        if self.last_emitted_second is None:
            if self.trade_bars:
                self.last_emitted_second = (
                    min(
                        self.trade_bars
                    )
                    - 1
                )
            else:
                return []

        completed = []

        while (
            self.last_emitted_second
            < target_second
        ):
            second = (
                self.last_emitted_second
                + 1
            )

            raw = (
                self.trade_bars.pop(
                    second,
                    None,
                )
            )

            if raw is not None:
                (
                    open_price,
                    high,
                    low,
                    close,
                    volume,
                ) = raw

                self.spot = close

            elif self.spot is not None:
                open_price = (
                    float(self.spot)
                )

                high = open_price
                low = open_price
                close = open_price
                volume = 0.0

            else:
                self.last_emitted_second = (
                    second
                )

                continue

            bar = BTCSecond(
                ts_ms=(
                    second * 1000
                ),
                open=float(
                    open_price
                ),
                high=float(high),
                low=float(low),
                close=float(close),
                volume=float(volume),
            )

            self.feature_engine.add_second(
                bar
            )

            features = (
                self.feature_engine.snapshot(
                    ts_ms=bar.ts_ms,
                    spot=bar.close,
                    best_bid=(
                        self.best_bid()
                    ),
                    best_ask=(
                        self.best_ask()
                    ),
                    book_imbalance_top10=(
                        self.book_imbalance_top10()
                    ),
                )
            )

            completed.append(
                (
                    bar,
                    features,
                )
            )

            self.last_emitted_second = (
                second
            )

        return completed


def _insert_second(
    connection,
    bar: BTCSecond,
) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO btc_1s (
            ts,
            source,
            open,
            high,
            low,
            close,
            volume
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            bar.ts_ms,
            "coinbase_ws_1s",
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            bar.volume,
        ),
    )


FEATURE_COLUMNS = [
    "spot",
    "best_bid",
    "best_ask",
    "spread_bps",
    "return_30s",
    "return_60s",
    "return_180s",
    "return_300s",
    "ema_5",
    "ema_9",
    "ema_21",
    "ema_5_9_bps",
    "ema_9_21_bps",
    "ema_5_slope_bps",
    "ema_9_slope_bps",
    "ema_21_slope_bps",
    "vwap_60s",
    "vwap_300s",
    "vwap_distance_60s_bps",
    "vwap_distance_300s_bps",
    "realized_vol_60s_bps",
    "realized_vol_300s_bps",
    "range_60s_bps",
    "range_300s_bps",
    "trade_volume_60s",
    "trade_volume_300s",
    "relative_volume_60s",
    "trade_imbalance_60s",
    "trade_imbalance_300s",
    "book_imbalance_top10",
]


def _insert_features(
    connection,
    features,
) -> None:
    columns = [
        "ts",
        "source",
        *FEATURE_COLUMNS,
    ]

    placeholders = ", ".join(
        "?"
        for _ in columns
    )

    values = [
        int(
            features["ts"]
        ),
        "coinbase_ws",
        *[
            features.get(
                column
            )
            for column
            in FEATURE_COLUMNS
        ],
    ]

    connection.execute(
        f"""
        INSERT OR REPLACE INTO btc_feature_snapshots (
            {", ".join(columns)}
        )
        VALUES ({placeholders})
        """,
        values,
    )


async def run_coinbase_btc_live(
    *,
    db_path: str,
    product_id: str = PRODUCT_ID,
) -> None:
    connection = connect(
        db_path
    )

    init_db(connection)

    state = CoinbaseBTCState(
        product_id=product_id
    )

    reconnect_delay = 0.5

    saved = 0

    try:
        while True:
            websocket = None

            try:
                websocket = await (
                    websockets.connect(
                        COINBASE_WS_URL,
                        ping_interval=20,
                        ping_timeout=20,
                        close_timeout=5,
                        # Coinbase BTC level2 snapshots can exceed the
                        # websockets library's default 1 MiB message limit.
                        max_size=8 * 1024 * 1024,
                        max_queue=64,
                    )
                )

                # Coinbase requires one subscription message
                # per channel.
                for channel in (
                    "ticker",
                    "level2",
                    "market_trades",
                ):
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "subscribe",
                                "product_ids": [
                                    product_id
                                ],
                                "channel": (
                                    channel
                                ),
                            }
                        )
                    )

                await websocket.send(
                    json.dumps(
                        {
                            "type": "subscribe",
                            "channel": (
                                "heartbeats"
                            ),
                        }
                    )
                )

                print(
                    "Coinbase WebSocket subscribed: "
                    f"{product_id}"
                )

                reconnect_delay = 0.5

                last_log = 0.0

                while True:
                    try:
                        raw = await asyncio.wait_for(
                            websocket.recv(),
                            timeout=0.25,
                        )

                        message = json.loads(
                            raw
                        )

                        state.handle_message(
                            message
                        )

                    except asyncio.TimeoutError:
                        pass

                    now_ms = int(
                        time.time()
                        * 1000
                    )

                    completed = (
                        state.completed_seconds(
                            now_ms
                        )
                    )

                    if completed:
                        for bar, features in (
                            completed
                        ):
                            _insert_second(
                                connection,
                                bar,
                            )

                            _insert_features(
                                connection,
                                features,
                            )

                            saved += 1

                        connection.commit()

                    now = time.monotonic()

                    if (
                        now - last_log
                        >= 5.0
                        and state.spot
                        is not None
                    ):
                        latest = (
                            completed[-1][1]
                            if completed
                            else None
                        )

                        if latest is None:
                            latest = (
                                state.feature_engine
                                .snapshot(
                                    ts_ms=now_ms,
                                    spot=float(
                                        state.spot
                                    ),
                                    best_bid=(
                                        state.best_bid()
                                    ),
                                    best_ask=(
                                        state.best_ask()
                                    ),
                                    book_imbalance_top10=(
                                        state
                                        .book_imbalance_top10()
                                    ),
                                )
                            )

                        print(
                            "BTC live | "
                            f"${state.spot:,.2f} | "
                            f"spread="
                            f"{latest.get('spread_bps') or 0:.2f}bps | "
                            f"imb60="
                            f"{latest.get('trade_imbalance_60s')} | "
                            f"book="
                            f"{latest.get('book_imbalance_top10')} | "
                            f"saved={saved}"
                        )

                        last_log = now

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                print(
                    "Coinbase WebSocket disconnected: "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

                print(
                    "Reconnecting in "
                    f"{reconnect_delay:.1f}s..."
                )

                await asyncio.sleep(
                    reconnect_delay
                )

                reconnect_delay = min(
                    reconnect_delay
                    * 2.0,
                    10.0,
                )

            finally:
                if websocket is not None:
                    try:
                        await websocket.close()
                    except Exception:
                        pass

    finally:
        connection.close()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--db",
        required=True,
    )

    parser.add_argument(
        "--product",
        default=PRODUCT_ID,
    )

    args = parser.parse_args()

    try:
        asyncio.run(
            run_coinbase_btc_live(
                db_path=args.db,
                product_id=(
                    args.product
                ),
            )
        )

    except KeyboardInterrupt:
        print(
            "\nCoinbase BTC collector stopped."
        )


if __name__ == "__main__":
    main()
