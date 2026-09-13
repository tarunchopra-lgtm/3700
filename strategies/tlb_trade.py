#!/usr/bin/env python3
"""
TLB Trade - Trade all symbols from lists/today-breakout
Buys at today's open price with configurable stop loss and targets

Configuration (edit below):
    POSITION_SIZE = $1000          # Target dollar amount per stock
    STOP_LOSS_PCT = 1.0            # Stop loss percentage below entry
    TARGET1_PCT = 1.0              # Target 1 profit percentage
    TARGET2_PCT = 2.0              # Target 2 profit percentage
    CHECK_INTERVAL = 5             # Seconds between price checks
"""

import os
import re
import sys
import time
import math
import requests
from datetime import datetime, date
from alpaca.common.exceptions import APIError
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth

# ── CONFIGURATION (Edit these values) ────────────────────────────────────────
POSITION_SIZE = 1000.0          # Target dollar amount per stock (~$1000)
STOP_LOSS_PCT = 1.0             # Stop loss as percentage (1.0 = 1%)
TARGET1_PCT = 1.0               # Target 1 as percentage
TARGET2_PCT = 2.0               # Target 2 as percentage
CHECK_INTERVAL = 5              # Seconds between price checks
BREAKOUT_FILE = WORKSPACE_ROOT / "lists" / "today-breakout"

REQUEST_TIMEOUT_SECONDS = float(os.getenv("ALPACA_HTTP_TIMEOUT", "15"))


def _install_http_timeout() -> None:
    original_request = requests.sessions.Session.request

    def _request_with_timeout(self, method, url, **kwargs):
        kwargs.setdefault("timeout", REQUEST_TIMEOUT_SECONDS)
        return original_request(self, method, url, **kwargs)

    requests.sessions.Session.request = _request_with_timeout


_install_http_timeout()

try:
    credentials, trading_client = bootstrap_trading_auth("tlb_trade.py")
except Exception as exc:
    print(f"Authentication failed: {exc}")
    raise SystemExit(1)

PAPER = credentials.paper
data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)


def _normalize_symbol(symbol: str) -> str:
    return symbol.replace("/", "").replace("-", "").upper()


def _get_current_price(symbol: str) -> float:
    """Fetch current price for a stock."""
    try:
        price_request = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
        latest_trade = data_client.get_stock_latest_trade(price_request)
        return float(latest_trade[symbol].price)
    except Exception as e:
        print(f"  ✗ Error fetching price for {symbol}: {e}")
        return None


def _find_position_for_symbol(symbol: str, retries: int = 3, delay_seconds: float = 1.0):
    """Find an open position for a symbol."""
    target = _normalize_symbol(symbol)
    for attempt in range(1, retries + 1):
        try:
            pos = trading_client.get_open_position(symbol)
            if _normalize_symbol(getattr(pos, "symbol", "")) == target:
                return pos
        except Exception:
            try:
                positions = trading_client.get_all_positions()
            except Exception:
                positions = []
            for pos in positions:
                if _normalize_symbol(getattr(pos, "symbol", "")) == target:
                    return pos
        if attempt < retries:
            time.sleep(delay_seconds)
    return None


def _get_open_orders_for_symbol(symbol: str):
    """Get all open orders for a symbol."""
    target = _normalize_symbol(symbol)
    try:
        orders = list(trading_client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=100)))
        return [o for o in orders if _normalize_symbol(getattr(o, "symbol", "")) == target]
    except Exception:
        return []


def _load_breakout_symbols() -> list[str]:
    """Load symbols from lists/today-breakout"""
    if not BREAKOUT_FILE.exists():
        print(f"Error: {BREAKOUT_FILE} not found")
        return []
    
    symbols = []
    try:
        with open(BREAKOUT_FILE, 'r') as f:
            for line in f:
                symbol = line.strip().upper()
                if symbol and not symbol.startswith('#'):
                    symbols.append(symbol)
    except Exception as e:
        print(f"Error reading {BREAKOUT_FILE}: {e}")
    
    return symbols


def _calculate_quantity(current_price: float, target_size: float) -> int:
    """Calculate quantity to buy based on target position size."""
    if current_price <= 0:
        return 0
    qty = int(target_size / current_price)
    return max(qty, 1)  # At least 1 share


def _place_limit_order(symbol: str, qty: int, price: float, side: OrderSide) -> str:
    """Place a limit order. Returns order ID or None."""
    try:
        order = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.GTC,
            limit_price=round(price, 2),
        )
        response = trading_client.submit_order(order_data=order)
        return str(response.id)
    except Exception as e:
        print(f"  ✗ Error placing {side} order for {symbol}: {e}")
        return None


def _place_market_order(symbol: str, qty: int, side: OrderSide) -> str:
    """Place a market order. Returns order ID or None."""
    try:
        order = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
        )
        response = trading_client.submit_order(order_data=order)
        return str(response.id)
    except Exception as e:
        print(f"  ✗ Error placing market {side} order for {symbol}: {e}")
        return None


def _cancel_order(order_id: str, symbol: str) -> bool:
    """Cancel an order by ID."""
    try:
        trading_client.cancel_order_by_id(order_id)
        return True
    except Exception as e:
        print(f"  ✗ Error cancelling order {order_id} for {symbol}: {e}")
        return False


def _wait_for_position(symbol: str, timeout_seconds: int = 60) -> bool:
    """Wait for a position to exist (entry order filled). Returns True if filled."""
    elapsed = 0
    while elapsed < timeout_seconds:
        pos = _find_position_for_symbol(symbol)
        if pos:
            return True
        elapsed += 3
        time.sleep(3)
    return False


def _place_stop_and_targets(symbol: str, qty: int, entry_price: float):
    """Place stop loss and target orders once position exists."""
    stop_price = round(entry_price * (1 - STOP_LOSS_PCT / 100.0), 2)
    target1_price = round(entry_price * (1 + TARGET1_PCT / 100.0), 2)
    target2_price = round(entry_price * (1 + TARGET2_PCT / 100.0), 2)
    
    print(f"\n  [ACTION] Position filled! Placing stop loss and targets...")
    
    # Place stop loss
    print(f"    Placing stop loss at ${stop_price:.2f}...")
    stop_order_id = _place_limit_order(symbol, qty, stop_price, OrderSide.SELL)
    if stop_order_id:
        print(f"    ✓ Stop loss order placed: {stop_order_id}")
    else:
        print(f"    ✗ Failed to place stop loss")
    
    # Place target 1 (half position)
    target1_qty = qty // 2 if qty > 1 else 0
    if target1_qty > 0:
        print(f"    Placing target 1 ({target1_qty} shares) at ${target1_price:.2f}...")
        target1_order_id = _place_limit_order(symbol, target1_qty, target1_price, OrderSide.SELL)
        if target1_order_id:
            print(f"    ✓ Target 1 order placed: {target1_order_id}")
        else:
            print(f"    ✗ Failed to place target 1")
    
    # Place target 2 (remaining position)
    target2_qty = qty - target1_qty
    if target2_qty > 0:
        print(f"    Placing target 2 ({target2_qty} shares) at ${target2_price:.2f}...")
        target2_order_id = _place_limit_order(symbol, target2_qty, target2_price, OrderSide.SELL)
        if target2_order_id:
            print(f"    ✓ Target 2 order placed: {target2_order_id}")
        else:
            print(f"    ✗ Failed to place target 2")


def _place_entry_order(symbol: str) -> dict:
    """Place entry order for a symbol. Returns dict with symbol, qty, entry_price, or None if failed."""
    print(f"\n{'─'*64}")
    print(f"  {symbol}")
    print(f"{'─'*64}")
    
    # Get current price
    current_price = _get_current_price(symbol)
    if current_price is None or current_price <= 0:
        print(f"  ✗ Could not get price for {symbol}")
        return None
    
    # Calculate position size and levels
    qty = _calculate_quantity(current_price, POSITION_SIZE)
    entry_price = current_price
    stop_price = round(entry_price * (1 - STOP_LOSS_PCT / 100.0), 2)
    target1_price = round(entry_price * (1 + TARGET1_PCT / 100.0), 2)
    target2_price = round(entry_price * (1 + TARGET2_PCT / 100.0), 2)
    
    print(f"  Current Price:  ${current_price:.2f}")
    print(f"  Qty to Buy:     {qty} shares")
    print(f"  Entry:          ${entry_price:.2f}")
    print(f"  Stop Loss:      ${stop_price:.2f} ({STOP_LOSS_PCT}%)")
    print(f"  Target 1:       ${target1_price:.2f} ({TARGET1_PCT}%)")
    print(f"  Target 2:       ${target2_price:.2f} ({TARGET2_PCT}%)")
    print(f"  Position Value: ${entry_price * qty:.2f}")
    
    # Check for existing position
    pos = _find_position_for_symbol(symbol)
    if pos:
        print(f"  ⚠ Position already exists for {symbol}: {float(pos.qty)} shares @ ${float(pos.avg_entry_price):.2f}")
        return None
    
    # Check for existing orders
    open_orders = _get_open_orders_for_symbol(symbol)
    if open_orders:
        print(f"  ⚠ Existing open orders for {symbol}: {len(open_orders)}")
        for o in open_orders:
            side = (o.side.value if hasattr(o.side, 'value') else str(o.side)).upper()
            limit = getattr(o, 'limit_price', 'market')
            print(f"    [{side}] {float(o.qty)} @ ${limit}")
        return None
    
    # Place entry order at current price
    print(f"\n  [ACTION] Placing BUY order for {qty} shares at ${entry_price:.2f}...")
    entry_order_id = _place_limit_order(symbol, qty, entry_price, OrderSide.BUY)
    if not entry_order_id:
        print(f"  ✗ Failed to place entry order")
        return None
    
    print(f"  ✓ Entry order placed: {entry_order_id}")
    
    return {
        "symbol": symbol,
        "qty": qty,
        "entry_price": entry_price,
        "order_id": entry_order_id
    }


def _manage_symbol(symbol: str):
    """Manage trading for one symbol (for re-entries during monitoring)."""
    _place_entry_order(symbol)


def main():
    print(f"\n{'═'*64}")
    print(f"  TLB TRADE - Today's Breakout Trader")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═'*64}")
    print(f"\n  Configuration:")
    print(f"    Position Size:   ${POSITION_SIZE:.2f}")
    print(f"    Stop Loss:       {STOP_LOSS_PCT}%")
    print(f"    Target 1:        {TARGET1_PCT}%")
    print(f"    Target 2:        {TARGET2_PCT}%")
    print(f"    Paper Trading:   {PAPER}")
    
    # Load symbols from today-breakout
    symbols = _load_breakout_symbols()
    if not symbols:
        print(f"\n  ✗ No symbols found in {BREAKOUT_FILE}")
        return 1
    
    print(f"\n  Symbols to trade: {len(symbols)}")
    print(f"    {', '.join(symbols[:5])}" + (f" ... ({len(symbols)-5} more)" if len(symbols) > 5 else ""))
    
    print(f"\n{'─'*64}")
    print(f"  SETUP PHASE - Placing initial entry orders")
    print(f"{'─'*64}")
    
    # Place all entry orders without waiting for fills
    pending_orders = {}  # Track pending entries: {symbol: {qty, entry_price, order_id}}
    for symbol in symbols:
        result = _place_entry_order(symbol)
        if result:
            pending_orders[symbol] = result
    
    print(f"\n{'─'*64}")
    print(f"  ✓ Entry orders placed for {len(pending_orders)}/{len(symbols)} symbols")
    print(f"  Monitoring for fills and managing stop loss/targets...")
    print(f"{'─'*64}\n")
    
    # Monitor mode: check for fills, place stop/targets, and allow re-entry
    print(f"  [MONITOR] Monitoring positions (Press Ctrl+C to stop)...\n")
    
    stops_placed = set()  # Track symbols where we've placed stop/targets
    re_entry_attempts = {}  # Track re-entry attempts per symbol
    max_re_entries = 2  # Allow up to 2 re-entries per symbol
    
    try:
        while True:
            current_time = datetime.now()
            
            # First, check pending orders for fills and place stop/targets
            for symbol in list(pending_orders.keys()):
                if symbol in stops_placed:
                    # Already placed stops for this symbol
                    del pending_orders[symbol]
                    continue
                
                pos = _find_position_for_symbol(symbol)
                if pos:
                    # Position filled! Place stop/targets
                    entry_data = pending_orders[symbol]
                    qty = entry_data["qty"]
                    entry_price = entry_data["entry_price"]
                    
                    print(f"[{current_time.strftime('%H:%M:%S')}] {symbol}: Position filled! Placing stop/targets...")
                    _place_stop_and_targets(symbol, qty, entry_price)
                    stops_placed.add(symbol)
                    del pending_orders[symbol]
            
            # Second, check for closed positions (stopped out) and allow re-entry
            for symbol in symbols:
                # Skip if we've already re-entered too many times
                if re_entry_attempts.get(symbol, 0) >= max_re_entries:
                    continue
                
                # Skip if we still have a pending order for this symbol
                if symbol in pending_orders:
                    continue
                
                # Skip if we haven't placed stop/targets yet
                if symbol not in stops_placed:
                    continue
                
                # Check if position exists
                pos = _find_position_for_symbol(symbol)
                if pos:
                    # Position still open - no action needed
                    continue
                
                # Check if we have open orders for this symbol
                open_orders = _get_open_orders_for_symbol(symbol)
                if open_orders:
                    # Still have pending orders, wait for them
                    continue
                
                # No position and no orders - stopped out. Allow re-entry
                re_entry_count = re_entry_attempts.get(symbol, 0)
                re_entry_attempts[symbol] = re_entry_count + 1
                
                print(f"[{current_time.strftime('%H:%M:%S')}] {symbol}: Re-entry opportunity (attempt {re_entry_count + 1}/{max_re_entries})")
                result = _place_entry_order(symbol)
                if result:
                    pending_orders[symbol] = result
                    stops_placed.discard(symbol)  # Reset so we can place stops again
            
            time.sleep(CHECK_INTERVAL)
    
    except KeyboardInterrupt:
        print(f"\n\n{'─'*64}")
        print(f"  Monitoring stopped by user")
        print(f"{'─'*64}\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
