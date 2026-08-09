#!/usr/bin/env python3
"""Return only the nearest this-week call option contract symbol for a ticker.

Usage:
    python current_week_option_function_call.py <TICKER> [REFERENCE_PRICE]

Output:
    Prints only the option contract symbol, with no auth debug or extra details.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetStatus, ContractType
from alpaca.trading.requests import GetOptionContractsRequest
from dotenv import load_dotenv


def _load_credentials() -> tuple[str, str, bool]:
    base_dir = Path(__file__).resolve().parent
    env_path = base_dir / "env" / "credentials"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv(dotenv_path=base_dir / ".env")

    api_key = (os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID") or "").strip()
    secret_key = (
        os.getenv("ALPACA_API_SECRET")
        or os.getenv("ALPACA_SECRET_KEY")
        or os.getenv("APCA_API_SECRET_KEY")
        or ""
    ).strip()
    paper = os.getenv("ALPACA_PAPER", "true").strip().lower() == "true"

    if not api_key or not secret_key:
        raise ValueError("Set Alpaca credentials in env/credentials or .env")

    return api_key, secret_key, paper


def _get_weekly_expiration(today: date | None = None) -> date:
    if today is None:
        today = date.today()
    days_until_friday = (4 - today.weekday()) % 7
    return today + timedelta(days=days_until_friday)


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def _get_current_price(symbol: str, stock_client: StockHistoricalDataClient) -> float:
    request = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
    trade_map = stock_client.get_stock_latest_trade(request)
    trade = trade_map[symbol]
    return float(trade.price)


def _get_nearest_contract_symbol(
    trading_client: TradingClient,
    symbol: str,
    reference_price: float,
    contract_type: ContractType,
) -> str:
    expiration = _get_weekly_expiration()
    request = GetOptionContractsRequest(
        underlying_symbols=[symbol],
        expiration_date=expiration,
        type=contract_type,
        status=AssetStatus.ACTIVE,
        limit=1000,
    )
    response = trading_client.get_option_contracts(request)
    contracts = getattr(response, "option_contracts", None)
    if contracts is None and isinstance(response, dict):
        contracts = response.get("option_contracts", [])

    contracts_list = list(contracts or [])
    if not contracts_list:
        raise RuntimeError(f"No active {contract_type.value.upper()} contracts found for {symbol} in this week's expiration")

    selected_contract = min(
        contracts_list,
        key=lambda contract: (
            abs(float(getattr(contract, "strike_price", 0.0)) - reference_price),
            float(getattr(contract, "strike_price", 0.0)),
        ),
    )
    contract_symbol = getattr(selected_contract, "symbol", None)
    if not contract_symbol:
        raise RuntimeError(f"Unable to resolve option contract symbol for {symbol}")

    return str(contract_symbol)


def get_current_week_call_option_symbol(ticker: str, reference_price: float | None = None) -> str:
    symbol = _normalize_symbol(ticker)
    api_key, secret_key, paper = _load_credentials()
    trading_client = TradingClient(api_key=api_key, secret_key=secret_key, paper=paper)
    if reference_price is None:
        stock_client = StockHistoricalDataClient(api_key, secret_key)
        reference_price = _get_current_price(symbol, stock_client)
    return _get_nearest_contract_symbol(trading_client, symbol, reference_price, ContractType.CALL)


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print("Usage: python current_week_option_function_call.py <TICKER> [REFERENCE_PRICE]", file=sys.stderr)
        return 1

    try:
        ref_price = float(sys.argv[2]) if len(sys.argv) == 3 else None
        contract_symbol = get_current_week_call_option_symbol(sys.argv[1], ref_price)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(contract_symbol)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())