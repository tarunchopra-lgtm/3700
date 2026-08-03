from __future__ import annotations

from typing import Any

import requests
from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient


class PositionCheckRole:
    """Handle position checks for an Alpaca trading client."""

    def __init__(self, trading_client: TradingClient) -> None:
        self.trading_client = trading_client

    def list_positions(self) -> list[dict[str, Any]]:
        try:
            positions = self.trading_client.get_all_positions()
        except requests.exceptions.ProxyError as exc:
            raise RuntimeError(f"Network proxy issue: {exc}") from exc
        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(f"Network connection issue: {exc}") from exc
        except APIError as exc:
            raise RuntimeError(f"Alpaca API error: {exc}") from exc

        return [
            {
                "symbol": position.symbol,
                "qty": position.qty,
                "market_value": position.market_value,
            }
            for position in positions
        ]

    def print_positions(self) -> None:
        positions = self.list_positions()
        if not positions:
            print("No open positions found.")
            return

        print("Open positions:")
        for position in positions:
            print(
                f"- {position['symbol']}: qty={position['qty']}, market_value={position['market_value']}"
            )
