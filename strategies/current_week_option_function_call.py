#!/usr/bin/env python3
"""Return only the nearest this-week call option contract symbol for a ticker.

Usage:
    python current_week_option_function_call.py <TICKER> [REFERENCE_PRICE] [--help]

Output:
    Prints only the option contract symbol, with no auth debug or extra details.
"""

# Check for --help early
def _check_help():
    import sys
    if len(sys.argv) > 1 and sys.argv[1] in ["--help", "-h", "help"]:
        print("""
CURRENT_WEEK_OPTION_FUNCTION_CALL.PY - Weekly Call Option Finder

SYNTAX:
  python strategies/current_week_option_function_call.py <TICKER> [REFERENCE_PRICE] [--help]

REQUIRED:
  TICKER              Stock symbol (e.g., AAPL, SPY, MU)

OPTIONAL:
  REFERENCE_PRICE     Strike price to find nearest call (default: current market price)
  --help, -h          Show this help message

DESCRIPTION:
  Finds the nearest weekly call option contract for a given stock
  Selects the call option with strike closest to reference price
  Returns ONLY the option contract symbol (Alpaca format)
  Useful for automated options trading and gocall.py integration

WEEKLY EXPIRATION:
  - Automatically uses this Friday's expiration date
  - Friday is defined as: (4 - today.weekday()) % 7 days forward
  - Finds all active weekly call contracts expiring that date

STRIKE SELECTION:
  - Finds strike nearest to reference price
  - If reference price not provided: uses current market price
  - Returns contract symbol of nearest ATM (at-the-money) call

EXAMPLES:
  python strategies/current_week_option_function_call.py AAPL
    - Find weekly call for AAPL at current market price
    Output: AAPL240920C00150000

  python strategies/current_week_option_function_call.py SPY 520
    - Find SPY weekly call with strike near $520
    Output: SPY240920C00520000

OUTPUT:
  - Single line: Alpaca option contract symbol (no newline, parseable)
  - Format: SYMBOL + YYMMDD + C + 8-digit strike price
  - Example: AAPL240920C00150000 (AAPL, 2024-09-20, Call, $150.00)

NOTES:
  - Requires valid Alpaca API credentials
  - Returns ONLY the contract symbol (no extra output)
  - Used by gocall.py and current_week_option.py
  - Uses IEX data feed for current price
  - Active status filter: only returns live tradeable contracts
  - Returns strike nearest to reference price, not exact match
""")
        sys.exit(0)

_check_help()

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
    # Handle help flag
    if len(sys.argv) < 2 or sys.argv[1] in ["--help", "-h", "help"]:
        print("""
CURRENT_WEEK_OPTION_FUNCTION_CALL.PY - Weekly Call Option Finder

SYNTAX:
  python current_week_option_function_call.py <TICKER> [REFERENCE_PRICE] [--help]

REQUIRED:
  <TICKER>              Stock symbol (e.g., AAPL, SPY, TSLA)

OPTIONAL:
  REFERENCE_PRICE       Strike price reference (default: current stock price)
  --help, -h            Show this help message

DESCRIPTION:
  Finds the nearest weekly call option contract for a given stock
  Useful for options trading strategies
  Returns tradeable option contract symbol
  Uses current price or provided reference for strike selection

CONTRACT SELECTION:
  - Finds options expiring this week (Friday typically)
  - Selects call options near reference price
  - Returns Alpaca-compatible contract symbol
  - Used in automated options strategies

EXAMPLES:
  python strategies/current_week_option_function_call.py AAPL
    - Find weekly call for AAPL near current market price

  python strategies/current_week_option_function_call.py SPY 450
    - Find weekly call for SPY near $450 strike

  python strategies/current_week_option_function_call.py TSLA 250
    - Find weekly call for TSLA near $250 strike

OUTPUT:
  - Alpaca contract symbol (e.g., AAPL 240920C00150000)
  - Contract format: SYMBOL DATE{YYMMDD} C/P STRIKE
  - Usable directly in options trading orders

NOTES:
  - Requires valid Alpaca API credentials with options approval
  - Works with stocks that have weekly options
  - Reference price helps find nearest strike
  - Call = bullish position (right to buy at strike)
  - Use with options trading strategies for automation
""")
        return 0
    
    if len(sys.argv) not in (2, 3):
        print("Usage: python current_week_option_function_call.py <TICKER> [REFERENCE_PRICE] [--help]", file=sys.stderr)
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