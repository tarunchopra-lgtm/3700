#!/usr/bin/env python3
"""Run current_week_option.py for every stock position in the account.

Usage:
    python position_options.py [--help]
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Check for --help early
if len(sys.argv) > 1 and sys.argv[1] in ["--help", "-h", "help"]:
    print("""
POSITION_OPTIONS.PY - Generate Options for All Stock Positions

SYNTAX:
  python strategies/position_options.py [--help]

OPTIONS:
  --help, -h    Show this help message

DESCRIPTION:
  Iterates through all current stock positions in trading account
  Runs current_week_option.py for each position
  Generates weekly option chain analysis and trading recommendations
  Useful for managing options across entire portfolio

WORKFLOW:
  1. Authenticate with Alpaca API
  2. Fetch all open positions in account
  3. Filter for stock positions only (US equity assets)
  4. For each stock position, run current_week_option.py
  5. Display analysis and option recommendations per stock

EXAMPLES:
  python strategies/position_options.py
    - Analyze weekly options for all current stock positions
    - Outputs analysis for AAPL, then MSFT, then GOOGL, etc.
    - Shows option recommendations for each position

OUTPUT:
  Per position:
  - Stock symbol and position details
  - Weekly call option analysis
  - Weekly put option analysis
  - Option bid/ask prices and premiums
  - Trading opportunity indicators

DEPENDENCIES:
  - current_week_option.py (must be in same strategies/ directory)
  - Valid Alpaca API credentials
  - Active trading account with positions

NOTES:
  - Requires valid Alpaca API credentials
  - Works with both paper and live trading accounts
  - Best run during market hours for current prices
  - Generates output for EACH position sequentially
  - Exit with error if no positions found
  - Use with gocall.py for options selling strategies
""")
    sys.exit(0)

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth


def is_stock_position(position) -> bool:
    asset_class = getattr(position, "asset_class", "")
    asset_class_value = getattr(asset_class, "value", asset_class)
    asset_class_text = str(asset_class_value).lower()
    symbol = str(getattr(position, "symbol", "")).upper()
    return bool(symbol) and any(token in asset_class_text for token in ("us_equity", "stock"))


def main() -> None:
    try:
        _, trading_client = bootstrap_trading_auth("position_options.py")
    except Exception as exc:
        print(f"Authentication failed: {exc}")
        raise SystemExit(1)

    positions = [position for position in trading_client.get_all_positions() if is_stock_position(position)]
    if not positions:
        print("No current stock positions found.")
        return

    script_path = Path(__file__).resolve().parent / "current_week_option.py"
    if not script_path.exists():
        print(f"Missing script: {script_path}")
        raise SystemExit(1)

    for position in positions:
        symbol = str(getattr(position, "symbol", "")).upper()
        if not symbol:
            continue

        print(f"\n{'=' * 72}")
        print(f"Running current_week_option.py for {symbol}")
        print(f"{'=' * 72}")

        result = subprocess.run(
            [sys.executable, str(script_path), symbol],
            check=False,
        )
        if result.returncode != 0:
            print(f"{symbol}: current_week_option.py exited with code {result.returncode}")


if __name__ == "__main__":
    main()
