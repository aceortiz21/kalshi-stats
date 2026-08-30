from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import log, sqrt


@dataclass(slots=True)
class BTCTrade:
    ts_ms: int
    price: float
    size: float
    aggressor: str


@dataclass(slots=True)
class BTCSecond:
    ts_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class BTCFeatureEngine:
    """
    Rolling BTC market-state feature engine.

    Prices are sampled once per completed second.
    Individual Coinbase trades are retained for VWAP,
    volume and aggressive-flow calculations.
    """

    def __init__(
        self,
        *,
        max_window_seconds: int = 900,
    ):
        self.max_window_ms = (
            max_window_seconds
            * 1000
        )

        self.seconds = deque()
        self.trades = deque()

        self.ema_5 = None
        self.ema_9 = None
        self.ema_21 = None

        self.previous_ema_5 = None
        self.previous_ema_9 = None
        self.previous_ema_21 = None


    def add_trade(
        self,
        *,
        ts_ms: int,
        price: float,
        size: float,
        aggressor: str,
    ) -> None:
        self.trades.append(
            BTCTrade(
                ts_ms=int(ts_ms),
                price=float(price),
                size=float(size),
                aggressor=str(
                    aggressor
                ).lower(),
            )
        )

        self._trim(
            int(ts_ms)
        )


    def add_second(
        self,
        second: BTCSecond,
    ) -> None:
        self.seconds.append(
            second
        )

        self.previous_ema_5 = (
            self.ema_5
        )

        self.previous_ema_9 = (
            self.ema_9
        )

        self.previous_ema_21 = (
            self.ema_21
        )

        self.ema_5 = self._next_ema(
            self.ema_5,
            second.close,
            5,
        )

        self.ema_9 = self._next_ema(
            self.ema_9,
            second.close,
            9,
        )

        self.ema_21 = self._next_ema(
            self.ema_21,
            second.close,
            21,
        )

        self._trim(
            second.ts_ms
        )


    @staticmethod
    def _next_ema(
        previous,
        price: float,
        period: int,
    ) -> float:
        if previous is None:
            return float(price)

        alpha = (
            2.0
            / (period + 1.0)
        )

        return (
            alpha * float(price)
            + (1.0 - alpha)
            * float(previous)
        )


    def _trim(
        self,
        now_ms: int,
    ) -> None:
        cutoff = (
            now_ms
            - self.max_window_ms
        )

        while (
            self.seconds
            and self.seconds[0].ts_ms
            < cutoff
        ):
            self.seconds.popleft()

        while (
            self.trades
            and self.trades[0].ts_ms
            < cutoff
        ):
            self.trades.popleft()


    def _price_at_or_before(
        self,
        ts_ms: int,
    ):
        for second in reversed(
            self.seconds
        ):
            if second.ts_ms <= ts_ms:
                return second.close

        return None


    def _return(
        self,
        *,
        now_ms: int,
        window_seconds: int,
        spot: float,
    ):
        historical = (
            self._price_at_or_before(
                now_ms
                - window_seconds
                * 1000
            )
        )

        if (
            historical is None
            or historical <= 0
        ):
            return None

        return (
            spot / historical
            - 1.0
        )


    def _bars_since(
        self,
        cutoff_ms: int,
    ):
        return [
            second
            for second
            in self.seconds
            if second.ts_ms >= cutoff_ms
        ]


    def _trades_between(
        self,
        start_ms: int,
        end_ms: int,
    ):
        return [
            trade
            for trade
            in self.trades
            if (
                start_ms
                <= trade.ts_ms
                <= end_ms
            )
        ]


    def _vwap(
        self,
        trades,
    ):
        volume = sum(
            trade.size
            for trade
            in trades
        )

        if volume <= 0:
            return None

        notional = sum(
            trade.price
            * trade.size
            for trade
            in trades
        )

        return (
            notional / volume
        )


    def _trade_imbalance(
        self,
        trades,
    ):
        buy_volume = sum(
            trade.size
            for trade
            in trades
            if trade.aggressor
            == "buy"
        )

        sell_volume = sum(
            trade.size
            for trade
            in trades
            if trade.aggressor
            == "sell"
        )

        total = (
            buy_volume
            + sell_volume
        )

        if total <= 0:
            return None

        return (
            buy_volume
            - sell_volume
        ) / total


    @staticmethod
    def _realized_vol_bps(
        bars,
    ):
        if len(bars) < 2:
            return None

        returns = []

        previous = bars[0].close

        for bar in bars[1:]:
            if (
                previous > 0
                and bar.close > 0
            ):
                returns.append(
                    log(
                        bar.close
                        / previous
                    )
                )

            previous = bar.close

        if not returns:
            return None

        # Non-annualized realized volatility across
        # the requested short window.
        return (
            sqrt(
                sum(
                    value * value
                    for value
                    in returns
                )
            )
            * 10000.0
        )


    @staticmethod
    def _range_bps(
        bars,
        spot: float,
    ):
        if (
            not bars
            or spot <= 0
        ):
            return None

        highest = max(
            bar.high
            for bar
            in bars
        )

        lowest = min(
            bar.low
            for bar
            in bars
        )

        return (
            (highest - lowest)
            / spot
            * 10000.0
        )


    @staticmethod
    def _spread_bps(
        best_bid,
        best_ask,
    ):
        if (
            best_bid is None
            or best_ask is None
            or best_bid <= 0
            or best_ask <= 0
        ):
            return None

        midpoint = (
            best_bid + best_ask
        ) / 2.0

        if midpoint <= 0:
            return None

        return (
            (best_ask - best_bid)
            / midpoint
            * 10000.0
        )


    @staticmethod
    def _distance_bps(
        spot: float,
        reference,
    ):
        if (
            reference is None
            or reference <= 0
        ):
            return None

        return (
            (spot - reference)
            / reference
            * 10000.0
        )


    @staticmethod
    def _ema_spread_bps(
        faster,
        slower,
    ):
        if (
            faster is None
            or slower is None
            or slower == 0
        ):
            return None

        return (
            (faster - slower)
            / slower
            * 10000.0
        )


    @staticmethod
    def _ema_slope_bps(
        current,
        previous,
    ):
        if (
            current is None
            or previous is None
            or previous == 0
        ):
            return None

        return (
            (current - previous)
            / previous
            * 10000.0
        )


    def snapshot(
        self,
        *,
        ts_ms: int,
        spot: float,
        best_bid=None,
        best_ask=None,
        book_imbalance_top10=None,
    ) -> dict[str, float | int | None]:

        bars_60 = self._bars_since(
            ts_ms - 60_000
        )

        bars_300 = self._bars_since(
            ts_ms - 300_000
        )

        trades_60 = (
            self._trades_between(
                ts_ms - 60_000,
                ts_ms,
            )
        )

        trades_300 = (
            self._trades_between(
                ts_ms - 300_000,
                ts_ms,
            )
        )

        trades_previous_60 = (
            self._trades_between(
                ts_ms - 120_000,
                ts_ms - 60_001,
            )
        )

        volume_60 = sum(
            trade.size
            for trade
            in trades_60
        )

        volume_300 = sum(
            trade.size
            for trade
            in trades_300
        )

        previous_volume_60 = sum(
            trade.size
            for trade
            in trades_previous_60
        )

        relative_volume_60 = None

        if previous_volume_60 > 0:
            relative_volume_60 = (
                volume_60
                / previous_volume_60
            )

        vwap_60 = self._vwap(
            trades_60
        )

        vwap_300 = self._vwap(
            trades_300
        )

        return {
            "ts": int(ts_ms),
            "spot": float(spot),

            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread_bps": (
                self._spread_bps(
                    best_bid,
                    best_ask,
                )
            ),

            "return_30s": (
                self._return(
                    now_ms=ts_ms,
                    window_seconds=30,
                    spot=spot,
                )
            ),
            "return_60s": (
                self._return(
                    now_ms=ts_ms,
                    window_seconds=60,
                    spot=spot,
                )
            ),
            "return_180s": (
                self._return(
                    now_ms=ts_ms,
                    window_seconds=180,
                    spot=spot,
                )
            ),
            "return_300s": (
                self._return(
                    now_ms=ts_ms,
                    window_seconds=300,
                    spot=spot,
                )
            ),

            "ema_5": self.ema_5,
            "ema_9": self.ema_9,
            "ema_21": self.ema_21,

            "ema_5_9_bps": (
                self._ema_spread_bps(
                    self.ema_5,
                    self.ema_9,
                )
            ),

            "ema_9_21_bps": (
                self._ema_spread_bps(
                    self.ema_9,
                    self.ema_21,
                )
            ),

            "ema_5_slope_bps": (
                self._ema_slope_bps(
                    self.ema_5,
                    self.previous_ema_5,
                )
            ),

            "ema_9_slope_bps": (
                self._ema_slope_bps(
                    self.ema_9,
                    self.previous_ema_9,
                )
            ),

            "ema_21_slope_bps": (
                self._ema_slope_bps(
                    self.ema_21,
                    self.previous_ema_21,
                )
            ),

            "vwap_60s": vwap_60,
            "vwap_300s": vwap_300,

            "vwap_distance_60s_bps": (
                self._distance_bps(
                    spot,
                    vwap_60,
                )
            ),

            "vwap_distance_300s_bps": (
                self._distance_bps(
                    spot,
                    vwap_300,
                )
            ),

            "realized_vol_60s_bps": (
                self._realized_vol_bps(
                    bars_60
                )
            ),

            "realized_vol_300s_bps": (
                self._realized_vol_bps(
                    bars_300
                )
            ),

            "range_60s_bps": (
                self._range_bps(
                    bars_60,
                    spot,
                )
            ),

            "range_300s_bps": (
                self._range_bps(
                    bars_300,
                    spot,
                )
            ),

            "trade_volume_60s": (
                volume_60
            ),

            "trade_volume_300s": (
                volume_300
            ),

            "relative_volume_60s": (
                relative_volume_60
            ),

            "trade_imbalance_60s": (
                self._trade_imbalance(
                    trades_60
                )
            ),

            "trade_imbalance_300s": (
                self._trade_imbalance(
                    trades_300
                )
            ),

            "book_imbalance_top10": (
                book_imbalance_top10
            ),
        }
