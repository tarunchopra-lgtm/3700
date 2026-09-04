#!/usr/bin/env python3
"""Auto-manage call options based on current stock positions.

Behavior:
1) Login using env/credentials via roles.credentials bootstrap.
2) Check all stock positions currently open.
3) Run strategies/current_week_option.py for each stock to discover call option.
4) Buy that option at bid/ask mid-price using a limit order.
5) Use first CLI argument as option quantity (default: 2 if not specified).
6) If an underlying position crosses down to 50 shares, sell half the option position.
7) Every minute, if stock still exists otherwise do nothing.
8) If underlying stock no longer exists, sell the remaining option at market.

Usage:
    python strategies/gocall.py [SIZE]

Example:
    python strategies/gocall.py 1
    python strategies/gocall.py       # Uses default SIZE of 2
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
from alpaca.trading.requests import GetOrdersRequest, LimitOrderRequest, MarketOrderRequest

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth

CHECK_INTERVAL_SECONDS = 10
MIN_OPEN_INTEREST = 10  # Minimum open interest required to buy a call option
CALL_SYMBOL_PATTERN = re.compile(r"CALL Contract:\s+(\S+)")


@dataclass
class ManagedOption:
    underlying: str
    option_symbol: str
    previous_stock_qty: float
    initial_stock_qty: float  # Track initial position size for 50% reduction logic
    half_triggered: bool = False
    half_sold: bool = False
    half_sell_order_id: str | None = None


def _is_stock_position(position) -> bool:
    asset_class = getattr(position, "asset_class", "")
    asset_class_value = getattr(asset_class, "value", asset_class)
    asset_class_text = str(asset_class_value).lower()
    symbol = str(getattr(position, "symbol", "")).upper()
    return bool(symbol) and any(token in asset_class_text for token in ("us_equity", "stock"))


def _get_stock_positions(trading_client) -> dict[str, float]:
    positions = list(trading_client.get_all_positions())
    return {
        str(getattr(position, "symbol", "")).upper(): abs(float(getattr(position, "qty", 0)))
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
        output_preview = " | ".join(line.strip() for line in result.stdout.splitlines()[:8])
        raise RuntimeError(
            f"Could not parse CALL contract symbol for {symbol} from current_week_option.py output: {output_preview}"
        )

    return match.group(1).strip().upper()


def _get_option_quote(trading_client, option_data_client: OptionHistoricalDataClient, option_symbol: str) -> tuple[float | None, float | None, float, int]:
    quote_map = option_data_client.get_option_latest_quote(
        OptionLatestQuoteRequest(symbol_or_symbols=option_symbol, feed=OptionsFeed.INDICATIVE)
    )
    quote = quote_map.get(option_symbol) if isinstance(quote_map, dict) else quote_map

    bid = getattr(quote, "bid_price", None)
    ask = getattr(quote, "ask_price", None)
    
    # Get open_interest from the full contract object (not the quote)
    try:
        full_contract = trading_client.get_option_contract(option_symbol)
        open_interest = int(getattr(full_contract, "open_interest", 0) or 0)
    except Exception:
        open_interest = 0

    if bid is not None and ask is not None:
        bid_f = float(bid)
        ask_f = float(ask)
        return bid_f, ask_f, round((bid_f + ask_f) / 2.0, 2), open_interest
    if bid is not None:
        bid_f = float(bid)
        return bid_f, None, round(bid_f, 2), open_interest
    if ask is not None:
        ask_f = float(ask)
        return None, ask_f, round(ask_f, 2), open_interest

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


def _cancel_open_orders(trading_client, option_symbol: str) -> None:
    orders = list(
        trading_client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=200)
        )
    )
    for order in orders:
        if str(getattr(order, "symbol", "")).upper() != option_symbol.upper():
            continue
        try:
            trading_client.cancel_order_by_id(order.id)
            print(f"[CANCEL] {option_symbol} order id={order.id}")
        except Exception as exc:
            print(f"[WARN] Could not cancel order {order.id} for {option_symbol}: {exc}")


def _has_open_option_position(trading_client, option_symbol: str) -> bool:
    try:
        trading_client.get_open_position(option_symbol)
        return True
    except Exception:
        return False


def _sell_option_market(trading_client, option_symbol: str) -> None:
    response = trading_client.close_position(option_symbol)
    print(f"[SELL] {option_symbol} MARKET id={getattr(response, 'id', 'N/A')}")


def _get_option_position_qty(trading_client, option_symbol: str) -> int:
    try:
        position = trading_client.get_open_position(option_symbol)
        return max(int(round(abs(float(getattr(position, "qty", 0))))), 0)
    except Exception:
        return 0


def _sell_option_quantity_market(trading_client, option_symbol: str, qty: int) -> str:
    response = trading_client.submit_order(
        order_data=MarketOrderRequest(
            symbol=option_symbol,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
    )
    return str(response.id)


def _order_status(trading_client, order_id: str) -> str:
    order = trading_client.get_order_by_id(order_id)
    status = getattr(order, "status", "")
    return (status.value if hasattr(status, "value") else str(status)).upper()


def _ensure_managed_calls(
    trading_client,
    option_data_client: OptionHistoricalDataClient,
    size: int,
    managed: dict[str, ManagedOption],
    stock_positions: dict[str, float],
) -> None:
    for symbol in sorted(stock_positions):
        if symbol in managed:
            continue

        try:
            option_symbol = _resolve_call_symbol_from_script(symbol)
            bid_price, ask_price, mid_price, open_interest = _get_option_quote(trading_client, option_data_client, option_symbol)
            bid_text = f"{bid_price:.2f}" if bid_price is not None else "N/A"
            ask_text = f"{ask_price:.2f}" if ask_price is not None else "N/A"
            print(
                f"[PLAN] {symbol} -> BUY {option_symbol} | bid={bid_text} ask={ask_text} mid={mid_price:.2f} oi={open_interest} qty={size}"
            )

            # Check open interest threshold
            if open_interest < MIN_OPEN_INTEREST:
                print(
                    f"[SKIP] Insufficient open interest for {option_symbol}: {open_interest} < {MIN_OPEN_INTEREST} (min)"
                )
                continue

            if _has_open_buy_order(trading_client, option_symbol):
                print(f"[SKIP] Open BUY already exists for {option_symbol}")
                managed[symbol] = ManagedOption(
                    underlying=symbol,
                    option_symbol=option_symbol,
                    previous_stock_qty=stock_positions[symbol],
                    initial_stock_qty=stock_positions[symbol],
                )
                continue

            _buy_call_option(trading_client, option_symbol, size, mid_price)
            managed[symbol] = ManagedOption(
                underlying=symbol,
                option_symbol=option_symbol,
                previous_stock_qty=stock_positions[symbol],
                initial_stock_qty=stock_positions[symbol],
            )
        except Exception as exc:
            print(f"[WARN] Could not open call for {symbol}: {exc}")


def _reduce_options_at_50_shares(
    trading_client,
    managed: dict[str, ManagedOption],
    stock_positions: dict[str, float],
) -> None:
    for underlying, item in managed.items():
        current_stock_qty = stock_positions.get(underlying)
        if current_stock_qty is None:
            continue

        if item.half_sell_order_id is not None:
            try:
                status = _order_status(trading_client, item.half_sell_order_id)
                print(f"[HALF] {item.option_symbol} order={item.half_sell_order_id} status={status}")
                if status == "FILLED":
                    item.half_sold = True
                    item.half_sell_order_id = None
                elif status in {"CANCELED", "EXPIRED", "REJECTED"}:
                    item.half_sell_order_id = None
            except Exception as exc:
                print(f"[WARN] Could not check half-sell order for {item.option_symbol}: {exc}")

        # Trigger half-sale when underlying position is reduced to 50% of initial size
        half_initial_qty = item.initial_stock_qty / 2
        crossed_to_half = (
            item.previous_stock_qty > half_initial_qty
            and current_stock_qty <= half_initial_qty
        )
        if crossed_to_half:
            item.half_triggered = True

        if item.half_triggered and not item.half_sold and item.half_sell_order_id is None:
            option_qty = _get_option_position_qty(trading_client, item.option_symbol)
            sell_qty = option_qty // 2
            if sell_qty > 0:
                try:
                    item.half_sell_order_id = _sell_option_quantity_market(
                        trading_client, item.option_symbol, sell_qty
                    )
                    print(
                        f"[HALF] {underlying} reduced {item.previous_stock_qty:g} -> "
                        f"{current_stock_qty:g} shares; selling {sell_qty}/{option_qty} "
                        f"{item.option_symbol} at market id={item.half_sell_order_id}"
                    )
                except Exception as exc:
                    print(f"[WARN] Could not sell half of {item.option_symbol}: {exc}")
            elif option_qty == 1:
                item.half_sold = True
                print(
                    f"[HALF] {underlying} reached {current_stock_qty:g} shares, but "
                    f"{item.option_symbol} has only 1 contract; no partial sale possible"
                )
            else:
                print(
                    f"[HALF] {underlying} reached {current_stock_qty:g} shares; waiting for "
                    f"the {item.option_symbol} option position to fill before reducing it"
                )

        item.previous_stock_qty = current_stock_qty


def _cleanup_removed_underlyings(
    trading_client,
    managed: dict[str, ManagedOption],
    stock_symbols: set[str],
) -> None:
    removed = [underlying for underlying in managed if underlying not in stock_symbols]
    for underlying in removed:
        option_symbol = managed[underlying].option_symbol
        print(f"[EXIT] Stock {underlying} is gone; unwinding {option_symbol}")

        # An unfilled entry order would otherwise linger after the underlying is closed.
        _cancel_open_orders(trading_client, option_symbol)

        if not _has_open_option_position(trading_client, option_symbol):
            print(f"[EXIT] No open position for {option_symbol}; nothing to sell")
            managed.pop(underlying, None)
            continue

        try:
            _sell_option_market(trading_client, option_symbol)
        except Exception as exc:
            # Keep it managed so the next cycle retries the exit.
            print(f"[WARN] Could not sell option {option_symbol} for removed {underlying}: {exc}")
            continue

        managed.pop(underlying, None)


def main() -> int:
    if len(sys.argv) not in (1, 2):
        print("Usage: python strategies/gocall.py [SIZE]")
        print("       SIZE defaults to 2 if not specified")
        return 1

    try:
        size = int(sys.argv[1]) if len(sys.argv) == 2 else 2
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
            stock_positions = _get_stock_positions(trading_client)
            stock_symbols = set(stock_positions)
            position_text = ", ".join(
                f"{symbol}={stock_positions[symbol]:g}" for symbol in sorted(stock_positions)
            )
            print(f"\n[SYNC] Stock positions: {position_text or 'none'}")

            _ensure_managed_calls(trading_client, option_data_client, size, managed, stock_positions)
            _reduce_options_at_50_shares(trading_client, managed, stock_positions)
            _cleanup_removed_underlyings(trading_client, managed, stock_symbols)

            print(f"[STATE] Managed calls: {[managed[k].option_symbol for k in sorted(managed)]}")
            time.sleep(CHECK_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("Stopped by user")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())