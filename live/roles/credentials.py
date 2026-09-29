from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from dotenv import load_dotenv


class CredentialsRole:
    """Load Alpaca credentials from a local env file."""

    def __init__(self, env_path: Optional[str | Path] = None) -> None:
        base_dir = Path(__file__).resolve().parent.parent
        if env_path is None:
            env_path = base_dir / "env" / "credentials"

        self.env_path = Path(env_path)
        if not self.env_path.is_absolute():
            self.env_path = (base_dir / self.env_path).resolve()

        if self.env_path.exists():
            load_dotenv(dotenv_path=self.env_path)
        else:
            load_dotenv(dotenv_path=base_dir / ".env")

        self.api_key = (
            os.getenv("ALPACA_API_KEY")
            or os.getenv("APCA_API_KEY_ID")
            or ""
        ).strip()
        self.secret_key = (
            os.getenv("ALPACA_API_SECRET")
            or os.getenv("ALPACA_SECRET_KEY")
            or os.getenv("APCA_API_SECRET_KEY")
            or ""
        ).strip()
        self.paper = os.getenv("ALPACA_PAPER", "true").strip().lower() == "true"

        if not self.api_key or not self.secret_key:
            raise ValueError(
                "Set Alpaca credentials in env/credentials or .env"
            )

    def build_trading_client(self) -> TradingClient:
        return TradingClient(
            api_key=self.api_key,
            secret_key=self.secret_key,
            paper=self.paper,
        )

    @staticmethod
    def mask(value: str) -> str:
        if not value:
            return "<missing>"
        if len(value) <= 8:
            return "*" * len(value)
        return f"{value[:4]}...{value[-4:]}"

    def print_runtime_and_credential_debug(self, script_name: str) -> None:
        print(f"[AUTH] Script: {script_name}")
        print(f"[AUTH] Python executable: {sys.executable}")
        print(f"[AUTH] Credentials file: {self.env_path}")
        print(f"[AUTH] API key: {self.mask(self.api_key)} (len={len(self.api_key)})")
        print(f"[AUTH] Secret key: {self.mask(self.secret_key)} (len={len(self.secret_key)})")
        print(f"[AUTH] Paper trading: {self.paper}")

    def authenticate(self, trading_client: TradingClient) -> None:
        try:
            account = trading_client.get_account()
            status = getattr(account, "status", "unknown")
            account_id = str(getattr(account, "id", ""))
            print(
                "[AUTH] Authentication OK: "
                f"account={self.mask(account_id)} status={status}"
            )
        except APIError as exc:
            raise RuntimeError(f"Alpaca API authentication failed: {exc}") from exc


def bootstrap_trading_auth(script_name: str) -> tuple[CredentialsRole, TradingClient]:
    credentials = CredentialsRole()
    credentials.print_runtime_and_credential_debug(script_name)
    trading_client = credentials.build_trading_client()
    credentials.authenticate(trading_client)
    return credentials, trading_client
