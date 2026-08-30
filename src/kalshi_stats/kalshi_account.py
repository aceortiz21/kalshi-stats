from __future__ import annotations

import base64
import json
import os
import time

from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import (
    Request,
    urlopen,
)

from cryptography.hazmat.primitives import (
    hashes,
    serialization,
)
from cryptography.hazmat.primitives.asymmetric import (
    padding,
)


API_ROOT = (
    "https://external-api.kalshi.com"
)

API_PREFIX = (
    "/trade-api/v2"
)


class KalshiAccountClient:
    """
    Read-only authenticated access to the user's
    own Kalshi account information.

    This client does not place, modify, or cancel orders.
    """

    def __init__(
        self,
        *,
        key_id: str | None = None,
        private_key_path: str | None = None,
    ):
        self.key_id = (
            key_id
            or os.environ[
                "KALSHI_API_KEY_ID"
            ]
        )

        key_path = (
            private_key_path
            or os.environ[
                "KALSHI_PRIVATE_KEY_PATH"
            ]
        )

        with open(
            key_path,
            "rb",
        ) as handle:
            self.private_key = (
                serialization.load_pem_private_key(
                    handle.read(),
                    password=None,
                )
            )

    def _sign(
        self,
        text: str,
    ) -> str:
        signature = (
            self.private_key.sign(
                text.encode(
                    "utf-8"
                ),
                padding.PSS(
                    mgf=padding.MGF1(
                        hashes.SHA256()
                    ),
                    salt_length=(
                        padding.PSS.DIGEST_LENGTH
                    ),
                ),
                hashes.SHA256(),
            )
        )

        return (
            base64.b64encode(
                signature
            ).decode(
                "utf-8"
            )
        )

    def _headers(
        self,
        *,
        method: str,
        path: str,
    ):
        timestamp = str(
            int(
                time.time()
                * 1000
            )
        )

        message = (
            timestamp
            + method.upper()
            + path
        )

        return {
            "KALSHI-ACCESS-KEY": (
                self.key_id
            ),
            "KALSHI-ACCESS-TIMESTAMP": (
                timestamp
            ),
            "KALSHI-ACCESS-SIGNATURE": (
                self._sign(
                    message
                )
            ),
            "Accept": (
                "application/json"
            ),
            "User-Agent": (
                "kalshi-stats/0.1"
            ),
        }

    def get_json(
        self,
        endpoint: str,
        params=None,
    ):
        path = (
            API_PREFIX
            + endpoint
        )

        query = (
            ""
            if not params
            else "?"
            + urlencode(
                params
            )
        )

        request = Request(
            API_ROOT
            + path
            + query,
            headers=self._headers(
                method="GET",
                path=path,
            ),
            method="GET",
        )

        with urlopen(
            request,
            timeout=30,
        ) as response:
            return json.load(
                response
            )

    def _paginate(
        self,
        endpoint: str,
        key: str,
        *,
        params=None,
        allow_404=False,
    ):
        output = []
        cursor = None

        while True:
            current = dict(
                params or {}
            )

            current.setdefault(
                "limit",
                1000,
            )

            if cursor:
                current[
                    "cursor"
                ] = cursor

            try:
                data = self.get_json(
                    endpoint,
                    current,
                )

            except HTTPError as error:
                if (
                    allow_404
                    and error.code
                    == 404
                ):
                    return output

                raise

            output.extend(
                data.get(
                    key,
                    [],
                )
            )

            cursor = data.get(
                "cursor"
            )

            if not cursor:
                break

        return output

    def get_fills(
        self,
    ):
        return self._paginate(
            "/portfolio/fills",
            "fills",
        )

    def get_historical_fills(
        self,
    ):
        return self._paginate(
            "/historical/fills",
            "fills",
            allow_404=True,
        )

    def get_settlements(
        self,
    ):
        return self._paginate(
            "/portfolio/settlements",
            "settlements",
        )

    def get_positions(
        self,
        *,
        subaccount=0,
    ):
        return self._paginate(
            "/portfolio/positions",
            "market_positions",
            params={
                "subaccount": (
                    subaccount
                ),
                "count_filter": (
                    "position,total_traded"
                ),
            },
        )

    def get_balance(
        self,
        *,
        subaccount=0,
    ):
        return self.get_json(
            "/portfolio/balance",
            {
                "subaccount": (
                    subaccount
                ),
            },
        )
