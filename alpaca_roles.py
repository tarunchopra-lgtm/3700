from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from dotenv import load_dotenv

from roles.credentials import bootstrap_trading_auth


class CredentialsRole:
    """Load Alpaca credentials from a .env file."""

    def __init__(self, env_path: str = ".env") -> None:
        env_file = Path(env_path)
        if not env_file.is_absolute():
            env_file = Path(__file__).resolve().parent / env_file

        load_dotenv(dotenv_path=env_file)
        self.api_key = (
            os.getenv("ALPACA_API_KEY")
            or os.getenv("APCA_API_KEY_ID")
            or ""
        ).strip()
        self.secret_key = (
            os.getenv("ALPACA_SECRET_KEY")
            or os.getenv("APCA_API_SECRET_KEY")
            or ""
        ).strip()
        self.paper = os.getenv("ALPACA_PAPER", "true").strip().lower() == "true"

        if not self.api_key or not self.secret_key:
            raise ValueError(
                "Set ALPACA_API_KEY/ALPACA_SECRET_KEY or APCA_API_KEY_ID/APCA_API_SECRET_KEY in your .env file"
            )

    def build_trading_client(self) -> TradingClient:
        return TradingClient(
            api_key=self.api_key,
            secret_key=self.secret_key,
            paper=self.paper,
        )


class PositionCheckRole:
    """Handle position checks for an Alpaca trading client."""

    def __init__(self, trading_client: TradingClient) -> None:
        self.trading_client = trading_client

    def list_positions(self) -> list[dict[str, Any]]:
        try:
            positions = self.trading_client.get_all_positions()
        except APIError as exc:
            raise RuntimeError(
                "Alpaca rejected the credentials. Check that the API key/secret match your account and that ALPACA_PAPER matches the account type."
            ) from exc

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


def main() -> None:
    try:
        _, trading_client = bootstrap_trading_auth("alpaca_roles.py")
        position_checker = PositionCheckRole(trading_client)
        position_checker.print_positions()
    except Exception as exc:
        print(f"Error: {exc}")


if __name__ == "__main__":
    main()
