#!/usr/bin/env python3
"""Auto-manage call options based on current stock positions.

Behavior:
1) Login using env/credentials via roles.credentials bootstrap.
2) Check all stock positions currently open.
3) Run strategies/current_week_option.py for each stock to discover call option.
4) Buy that option at bid/ask mid-price using a limit order.
5) Use first CLI argument as option quantity (e.g., 1, 5).
6) Every minute, if stock still exists do nothing.
7) If underlying stock no longer exists, sell the option at market.

Usage:
    python strategies/gocall.py <SIZE>

Example:
    python strategies/gocall.py 1
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from alpaca.data.enums import OptionsFeed
from alpaca.data.historical import OptionHistoricalDataClient
from alpaca.data.requests import OptionLatestQuoteRequest
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, LimitOrderRequest

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth

CHECK_INTERVAL_SECONDS = 60
CALL_SYMBOL_PATTERN = re.compile(r"CALL Contract:\s+(\S+)")


@dataclass
class ManagedOption:
    underlying: str
    option_symbol: str


def _is_stock_position(position) -> bool:
    asset_class = getattr(position, "asset_class", "")
    asset_class_value = getattr(asset_class, "value", asset_class)
    asset_class_text = str(asset_class_value).lower()
    symbol = str(getattr(position, "symbol", "")).upper()
    return bool(symbol) and any(token in asset_class_text for token in ("us_equity", "stock"))


def _get_stock_position_symbols(trading_client) -> set[str]:
    positions = list(trading_client.get_all_positions())
    return {
        str(getattr(position, "symbol", "")).upper()
        for position in positions
        if _is_stock_position(position)
    }


def _resolve_call_symbol_from_script(symbol: str) -> str:
    script_path = WORKSPACE_ROOT / "strategies" / "current_week_option.py"
    result = subprocess.run(
        [sys.executable, str(script_path), symbol],
        check=False,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        detail = stderr if stderr else stdout if stdout else "unknown error"
        raise RuntimeError(f"current_week_option.py failed for {symbol}: {detail}")

    match = CALL_SYMBOL_PATTERN.search(result.stdout)
    if not match:
        raise RuntimeError(f"Could not parse CALL contract symbol for {symbol} from current_week_option.py output")

    return match.group(1).strip().upper()


def _get_option_mid_price(option_data_client: OptionHistoricalDataClient, option_symbol: str) -> float:
    quote_map = option_data_client.get_option_latest_quote(
        OptionLatestQuoteRequest(symbol_or_symbols=option_symbol, feed=OptionsFeed.INDICATIVE)
    )
    quote = quote_map.get(option_symbol) if isinstance(quote_map, dict) else quote_map

    bid = getattr(quote, "bid_price", None)
    ask = getattr(quote, "ask_price", None)

    if bid is not None and ask is not None:
        return round((float(bid) + float(ask)) / 2.0, 2)
    if bid is not None:
        return round(float(bid), 2)
    if ask is not None:
        return round(float(ask), 2)

    raise RuntimeError(f"No bid/ask quote available for {option_symbol}")


def _has_open_buy_order(trading_client, option_symbol: str) -> bool:
    orders = list(
        trading_client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=200)
        )
    )
    for order in orders:
        symbol = str(getattr(order, "symbol", "")).upper()
        side = getattr(order, "side", None)
        side_text = (side.value if hasattr(side, "value") else str(side)).upper()
        if symbol == option_symbol.upper() and side_text == "BUY":
            return True
    return False


def _buy_call_option(trading_client, option_symbol: str, qty: int, mid_price: float) -> None:
    order = LimitOrderRequest(
        symbol=option_symbol,
        qty=qty,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.GTC,
        limit_price=mid_price,
    )
    response = trading_client.submit_order(order_data=order)
    print(f"[BUY] {option_symbol} qty={qty} @ {mid_price:.2f} id={getattr(response, 'id', 'N/A')}")


def _sell_option_market(trading_client, option_symbol: str) -> None:
    response = trading_client.close_position(symbol=option_symbol)
    print(f"[SELL] {option_symbol} MARKET id={getattr(response, 'id', 'N/A')}")


def _ensure_managed_calls(
    trading_client,
    option_data_client: OptionHistoricalDataClient,
    size: int,
    managed: dict[str, ManagedOption],
    stock_symbols: set[str],
) -> None:
    for symbol in sorted(stock_symbols):
        if symbol in managed:
            continue

        try:
            option_symbol = _resolve_call_symbol_from_script(symbol)
            mid_price = _get_option_mid_price(option_data_client, option_symbol)

            if _has_open_buy_order(trading_client, option_symbol):
                print(f"[SKIP] Open BUY already exists for {option_symbol}")
                managed[symbol] = ManagedOption(underlying=symbol, option_symbol=option_symbol)
                continue

            _buy_call_option(trading_client, option_symbol, size, mid_price)
            managed[symbol] = ManagedOption(underlying=symbol, option_symbol=option_symbol)
        except Exception as exc:
            print(f"[WARN] Could not open call for {symbol}: {exc}")


def _cleanup_removed_underlyings(
    trading_client,
    managed: dict[str, ManagedOption],
    stock_symbols: set[str],
) -> None:
    removed = [underlying for underlying in managed if underlying not in stock_symbols]
    for underlying in removed:
        option_symbol = managed[underlying].option_symbol
        try:
            _sell_option_market(trading_client, option_symbol)
        except Exception as exc:
            print(f"[WARN] Could not sell option {option_symbol} for removed {underlying}: {exc}")
        finally:
            managed.pop(underlying, None)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python strategies/gocall.py <SIZE>")
        return 1

    try:
        size = int(sys.argv[1])
    except ValueError:
        print("SIZE must be an integer")
        return 1

    if size <= 0:
        print("SIZE must be greater than 0")
        return 1

    try:
        credentials, trading_client = bootstrap_trading_auth("gocall.py")
    except Exception as exc:
        print(f"Authentication failed: {exc}")
        return 1

    option_data_client = OptionHistoricalDataClient(credentials.api_key, credentials.secret_key)
    managed: dict[str, ManagedOption] = {}

    print(f"Starting gocall.py with size={size}; checking every {CHECK_INTERVAL_SECONDS}s")

    try:
        while True:
            stock_symbols = _get_stock_position_symbols(trading_client)
            print(f"\n[SYNC] Stock positions: {sorted(stock_symbols) if stock_symbols else 'none'}")

            _ensure_managed_calls(trading_client, option_data_client, size, managed, stock_symbols)
            _cleanup_removed_underlyings(trading_client, managed, stock_symbols)

            print(f"[STATE] Managed calls: {[managed[k].option_symbol for k in sorted(managed)]}")
            time.sleep(CHECK_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("Stopped by user")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())