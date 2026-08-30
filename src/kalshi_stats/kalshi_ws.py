from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


WS_URL = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
WS_PATH = "/trade-api/ws/v2"


@dataclass(slots=True)
class LiveTicker:
    market_ticker: str
    yes_bid: float
    yes_ask: float
    last_price: float | None
    volume: float | None
    open_interest: float | None
    ts_ms: int


class KalshiTickerWebSocket:
    def __init__(
        self,
        api_key_id: str,
        private_key_path: str,
    ) -> None:
        self.api_key_id = api_key_id
        self.private_key_path = Path(private_key_path)

        with self.private_key_path.open("rb") as file:
            self.private_key = serialization.load_pem_private_key(
                file.read(),
                password=None,
            )

    @classmethod
    def from_env(cls) -> "KalshiTickerWebSocket":
        api_key_id = os.environ.get("KALSHI_API_KEY_ID")
        private_key_path = os.environ.get(
            "KALSHI_PRIVATE_KEY_PATH"
        )

        if not api_key_id:
            raise RuntimeError(
                "KALSHI_API_KEY_ID is not set. "
                "Run: set -a; source .env; set +a"
            )

        if not private_key_path:
            raise RuntimeError(
                "KALSHI_PRIVATE_KEY_PATH is not set. "
                "Run: set -a; source .env; set +a"
            )

        return cls(
            api_key_id=api_key_id,
            private_key_path=private_key_path,
        )

    def _signature(self, timestamp: str) -> str:
        message = (
            timestamp
            + "GET"
            + WS_PATH
        ).encode("utf-8")

        signature = self.private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )

        return base64.b64encode(signature).decode("utf-8")

    def _headers(self) -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))

        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": self._signature(
                timestamp
            ),
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
        }

    async def open(self, market_ticker: str):
        websocket = await websockets.connect(
            WS_URL,
            additional_headers=self._headers(),
            ping_interval=20,
            ping_timeout=20,
        )

        await websocket.send(
            json.dumps(
                {
                    "id": 1,
                    "cmd": "subscribe",
                    "params": {
                        "channels": ["ticker"],
                        "market_ticker": market_ticker,
                    },
                }
            )
        )

        return websocket

    def parse_ticker(
        self,
        raw_message: str,
        expected_market: str,
    ) -> LiveTicker | None:
        data = json.loads(raw_message)
        message_type = data.get("type")

        if message_type == "error":
            msg = data.get("msg", {})
            raise RuntimeError(
                "Kalshi WebSocket error "
                f"{msg.get('code')}: {msg.get('msg')}"
            )

        if message_type != "ticker":
            return None

        msg = data.get("msg", {})

        market_ticker = str(
            msg.get("market_ticker") or ""
        )

        if market_ticker != expected_market:
            return None

        yes_bid_raw = msg.get("yes_bid_dollars")
        yes_ask_raw = msg.get("yes_ask_dollars")

        if yes_bid_raw is None or yes_ask_raw is None:
            return None

        last_price_raw = msg.get("price_dollars")
        volume_raw = msg.get("volume_fp")
        open_interest_raw = msg.get("open_interest_fp")

        return LiveTicker(
            market_ticker=market_ticker,
            yes_bid=float(yes_bid_raw),
            yes_ask=float(yes_ask_raw),
            last_price=(
                None
                if last_price_raw is None
                else float(last_price_raw)
            ),
            volume=(
                None
                if volume_raw is None
                else float(volume_raw)
            ),
            open_interest=(
                None
                if open_interest_raw is None
                else float(open_interest_raw)
            ),
            ts_ms=int(
                msg.get("ts_ms")
                or int(time.time() * 1000)
            ),
        )
