#!/usr/bin/env python3
"""Run current_week_option.py for every stock position in the account.

Usage:
    python position_options.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

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