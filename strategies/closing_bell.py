#!/usr/bin/env python3
"""
Closing Bell - Close all open positions

Closes every open position with support for bracket orders:
1. First cancels any bracket orders from fomo_trade.py system (stop + target orders)
2. Then attempts to sell at the midpoint of bid/ask for each position
3. If the order doesn't fill within 1 minute, cancels and sells at market price
4. Cleans up all position tracking files

Usage:
    python strategies/closing_bell.py [--help]

Note: Scheduling is handled via Windows Task Scheduler.
"""

import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    GetOrdersRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, CryptoLatestQuoteRequest

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth
from roles.email_notify import send_email

# ── Configuration ────────────────────────────────────────────────────────────
LIMIT_ORDER_TIMEOUT = 5  # seconds to wait for limit order to fill
LOG_FILE = os.path.join(os.path.dirname(__file__), "closing_bell_log.txt")
ORDER_TIME_IN_FORCE = TimeInForce.DAY
POSITIONS_DIR = WORKSPACE_ROOT / "positions"


def _log(message: str) -> None:
    """Log message to both console and file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"
    print(log_entry)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_entry + "\n")


def _load_bracket_file(symbol: str, bracket_num: int) -> Optional[dict]:
    """Load bracket tracking file if it exists."""
    import json
    
    bracket_file = POSITIONS_DIR / f"{symbol}_bracket{bracket_num}.txt"
    
    if not bracket_file.exists():
        return None
    
    try:
        with open(bracket_file, 'r') as f:
            return json.load(f)
    except Exception:
        return None


def _remove_bracket_files(symbol: str) -> None:
    """Remove bracket tracking files for a symbol."""
    for bracket_num in [1, 2]:
        bracket_file = POSITIONS_DIR / f"{symbol}_bracket{bracket_num}.txt"
        try:
            if bracket_file.exists():
                bracket_file.unlink()
                _log(f"  ✓ Cleaned up bracket {bracket_num} tracking file")
        except Exception:
            pass


def _remove_position_status_file(symbol: str) -> None:
    """Remove position status file for a symbol."""
    position_file = POSITIONS_DIR / f"{symbol}_position.txt"
    try:
        if position_file.exists():
            position_file.unlink()
            _log(f"  ✓ Cleaned up position status file")
    except Exception:
        pass


def _cancel_bracket_orders(trading_client, symbol: str) -> bool:
    """Cancel all bracket orders (stop + target) for a symbol."""
    cancelled_any = False
    
    for bracket_num in [1, 2]:
        bracket_data = _load_bracket_file(symbol, bracket_num)
        
        if not bracket_data:
            continue
        
        # Cancel stop order if it exists
        stop_order_id = bracket_data.get("stop_order_id")
        if stop_order_id and stop_order_id.startswith("alpaca_"):
            try:
                trading_client.cancel_order_by_id(stop_order_id)
                _log(f"  ✓ Cancelled bracket {bracket_num} stop order ({stop_order_id})")
                cancelled_any = True
            except Exception as e:
                _log(f"  ⚠ Could not cancel bracket {bracket_num} stop: {e}")
        
        # Cancel target order if it exists
        target_order_id = bracket_data.get("target_order_id")
        if target_order_id and target_order_id.startswith("alpaca_"):
            try:
                trading_client.cancel_order_by_id(target_order_id)
                _log(f"  ✓ Cancelled bracket {bracket_num} target order ({target_order_id})")
                cancelled_any = True
            except Exception as e:
                _log(f"  ⚠ Could not cancel bracket {bracket_num} target: {e}")
        
        # Cancel entry order if it exists and not yet filled
        entry_order_id = bracket_data.get("entry_order_id")
        if entry_order_id and entry_order_id.startswith("alpaca_"):
            try:
                trading_client.cancel_order_by_id(entry_order_id)
                _log(f"  ✓ Cancelled bracket {bracket_num} entry order ({entry_order_id})")
                cancelled_any = True
            except Exception:
                pass  # Silently skip if entry already filled
    
    return cancelled_any



def _is_crypto(symbol: str) -> bool:
    """Check if symbol is a crypto pair (contains /)."""
    return "/" in symbol


def _normalize_qty(qty: float, symbol: str) -> Union[float, int]:
    """
    Normalize quantity for order submission.
    - Stocks: Return as int
    - Crypto: Return as float with 8 decimal places
    
    Args:
        qty: Quantity as float
        symbol: Trading symbol
    
    Returns:
        qty as int (stocks) or float (crypto)
    """
    if _is_crypto(symbol):
        # Crypto: keep as float, round to 8 decimals
        return round(float(qty), 8)
    else:
        # Stocks: convert to int (remove decimals)
        return int(qty)


def _get_bid_ask(trading_client, symbol: str, stock_data_client, crypto_data_client) -> Optional[tuple[float, float]]:
    """Get the current bid and ask prices for a symbol."""
    try:
        if _is_crypto(symbol):
            quote_request = CryptoLatestQuoteRequest(symbol_or_symbols=symbol)
            latest_quote = crypto_data_client.get_crypto_latest_quote(quote_request)
        else:
            quote_request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            latest_quote = stock_data_client.get_stock_latest_quote(quote_request)
        
        quote = latest_quote.get(symbol)
        if quote:
            return float(quote.bid_price), float(quote.ask_price)
    except Exception as e:
        _log(f"  ⚠ Error getting bid/ask for {symbol}: {e}")
    
    return None


def _get_all_positions(trading_client):
    """Get all open positions."""
    try:
        positions = trading_client.get_all_positions()
        return [
            {
                "symbol": position.symbol,
                "qty": position.qty,
                "market_value": float(position.market_value),
            }
            for position in positions
        ]
    except Exception as e:
        _log(f"✗ Error fetching positions: {e}")
        return []


def _cancel_order(trading_client, order_id: str) -> bool:
    """Cancel an order by ID."""
    try:
        trading_client.cancel_order_by_id(order_id)
        return True
    except Exception as e:
        _log(f"  ⚠ Error canceling order {order_id}: {e}")
        return False


def _cancel_all_pending_sell_orders(trading_client, symbol: str) -> None:
    """Cancel every live order for a symbol so it cannot reserve closeout quantity."""
    try:
        orders = trading_client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=100)
        )
        symbol_orders = [o for o in orders if o.symbol == symbol]
        
        for order in symbol_orders:
            try:
                trading_client.cancel_order_by_id(order.id)
                _log(f"  ✓ Cancelled open {order.side} order ({order.id})")
            except Exception as e:
                _log(f"  ⚠ Could not cancel order {order.id}: {e}")
    except Exception as e:
        _log(f"  ⚠ Error fetching orders for {symbol}: {e}")


def _wait_for_order_cancellations(trading_client, symbol: str, timeout_seconds: int = 10) -> bool:
    """Wait until Alpaca confirms that no live orders reserve this position."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            orders = trading_client.get_orders(
                filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=100)
            )
            remaining = [order for order in orders if order.symbol == symbol]
            if not remaining:
                return True
            _log(f"  ⏳ Waiting for {len(remaining)} cancellation(s) to settle...")
        except Exception as e:
            _log(f"  ⚠ Could not verify cancellations for {symbol}: {e}")
        time.sleep(1)
    return False


def _remove_position_tracking(symbol: str) -> None:
    """Remove the current JSON tracker after a closeout order is accepted."""
    position_file = POSITIONS_DIR / f"{symbol}.json"
    try:
        if position_file.exists():
            position_file.unlink()
            _log(f"  ✓ Cleaned up position tracker ({position_file.name})")
    except Exception as e:
        _log(f"  ⚠ Could not remove position tracker for {symbol}: {e}")


def _sell_at_limit(
    trading_client,
    symbol: str,
    qty: float,
    limit_price: float,
    timeout_seconds: int = LIMIT_ORDER_TIMEOUT,
) -> Optional[str]:
    """
    Try to sell at limit price. Returns order ID if submitted, None otherwise.
    Waits for timeout_seconds for the order to fill, then returns.
    """
    try:
        # Debug: show what we're normalizing
        normalized_qty = _normalize_qty(qty, symbol)
        _log(f"  → Normalizing qty: {qty} ({type(qty).__name__}) for {symbol} → {normalized_qty} ({type(normalized_qty).__name__})")
        
        order = LimitOrderRequest(
            symbol=symbol,
            qty=normalized_qty,
            side=OrderSide.SELL,
            time_in_force=ORDER_TIME_IN_FORCE,
            limit_price=round(limit_price, 2),
        )
        response = trading_client.submit_order(order_data=order)
        order_id = response.id
        _log(f"  → Submitted limit order {order_id} for {qty} {symbol} @ ${limit_price:.2f}")
        
        # Wait for the order to fill or timeout
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            try:
                order_status = trading_client.get_order_by_id(order_id)
                filled_qty = float(order_status.filled_qty) if order_status.filled_qty else 0
                if filled_qty > 0:
                    filled_avg_price = float(order_status.filled_avg_price) if order_status.filled_avg_price else 0
                    _log(f"  ✓ Limit order filled! {filled_qty} {symbol} @ avg ${filled_avg_price:.2f}")
                    return order_id  # Filled, return success
                
                if order_status.status in ("canceled", "expired", "rejected"):
                    _log(f"  ⚠ Limit order {order_id} {order_status.status}")
                    return None
            except Exception as e:
                _log(f"  ⚠ Error checking order status: {e}")
            
            time.sleep(5)  # Check every 5 seconds
        
        # Timeout reached, order did not fill
        _log(f"  ✗ Limit order did not fill in {timeout_seconds} seconds, canceling...")
        _cancel_order(trading_client, order_id)
        return None
        
    except Exception as e:
        _log(f"  ✗ Error submitting limit order for {symbol}: {e}")
        return None


def _sell_at_market(
    trading_client,
    symbol: str,
    qty: float,
) -> bool:
    """Sell at market price. Returns True if successful."""
    try:
        # Debug: show what we're normalizing
        normalized_qty = _normalize_qty(qty, symbol)
        _log(f"  → Normalizing qty: {qty} ({type(qty).__name__}) for {symbol} → {normalized_qty} ({type(normalized_qty).__name__})")
        
        order = MarketOrderRequest(
            symbol=symbol,
            qty=normalized_qty,
            side=OrderSide.SELL,
            time_in_force=ORDER_TIME_IN_FORCE,
        )
        response = trading_client.submit_order(order_data=order)
        _log(f"  ✓ Market sell order submitted for {qty} {symbol} (Order ID: {response.id})")
        return True
    except Exception as e:
        _log(f"  ✗ Error submitting market order for {symbol}: {e}")
        import traceback
        _log(f"  DEBUG: {traceback.format_exc()}")
        return False


def _send_close_report(closed_positions: list) -> None:
    """Send email report of closed positions."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    if closed_positions:
        subject = f"🔔 Closing Bell Report - {len(closed_positions)} position(s) closed"
        body = f"Closing Bell executed at {timestamp}\n\n"
        body += f"Positions closed: {len(closed_positions)}\n"
        body += "="*60 + "\n\n"
        
        for pos in closed_positions:
            symbol = pos["symbol"]
            qty = pos["qty"]
            price = pos["price"]
            price_str = f"${price:.2f}" if price is not None else "Market Price"
            body += f"{symbol}: {qty} shares closed at {price_str}\n"
        
        body += "\n" + "="*60
    else:
        subject = f"🔔 Closing Bell Report - No positions to close"
        body = f"Closing Bell executed at {timestamp}\n\n"
        body += "No open positions existed at closing time.\n"
        body += "Program ran successfully with no action required."
    
    try:
        recipient = send_email(subject, body)
        _log(f"✓ Email report sent to {recipient}")
    except Exception as e:
        _log(f"✗ Failed to send email report: {e}")


def close_all_positions(trading_client, stock_data_client, crypto_data_client) -> None:
    """Close all open positions and send email report."""
    _log("\n" + "=" * 60)
    _log("🔔 CLOSING BELL - Closing all positions...")
    _log("=" * 60)
    
    positions = _get_all_positions(trading_client)
    closed_positions = []  # Track closed positions with prices
    
    if not positions:
        _log("✓ No open positions to close.")
        _log("=" * 60 + "\n")
        # Email even if no positions
        _send_close_report(closed_positions)
        return
    
    _log(f"Found {len(positions)} open position(s). Starting closeout...")
    
    successful_closes = 0
    failed_closes = 0
    
    for position in positions:
        symbol = position["symbol"]
        qty = position["qty"]
        market_value = position["market_value"]
        close_price = None
        
        _log(f"\n[{symbol}] Qty: {qty}, Value: ${market_value:.2f}")
        
        # Cancel every live order, including OCO parent/leg orders from fomo_trade.py.
        # An open exit order reserves position quantity and blocks liquidation.
        _cancel_all_pending_sell_orders(trading_client, symbol)
        if not _wait_for_order_cancellations(trading_client, symbol):
            _log(f"  ✗ Skipping {symbol}: open orders still reserve its quantity")
            failed_closes += 1
            continue

        # Closing Bell must flatten the position; do not leave a midpoint limit order open.
        if _sell_at_market(trading_client, symbol, qty):
            _remove_bracket_files(symbol)
            _remove_position_status_file(symbol)
            _remove_position_tracking(symbol)
            closed_positions.append({"symbol": symbol, "qty": qty, "price": None})
            successful_closes += 1
        else:
            failed_closes += 1
    
    _log("\n" + "=" * 60)
    _log(f"Closeout Summary: {successful_closes} successful, {failed_closes} failed")
    _log("=" * 60 + "\n")
    
    # Send email report
    _send_close_report(closed_positions)


def main():
    """Close all positions immediately."""
    # Handle help flag
    if len(sys.argv) > 1 and sys.argv[1] in ["--help", "-h", "help"]:
        print("""
CLOSING_BELL.PY - Force Close All Open Positions

SYNTAX:
  python strategies/closing_bell.py [--help]

OPTIONS:
  --help, -h    Show this help message

DESCRIPTION:
  Emergency program to close all open trading positions
  Executes at market close or on-demand to flatten account
  Uses intelligent exit strategy: limit order → market order
  
  NOW WITH BRACKET ORDER SUPPORT:
  - Automatically cancels bracket orders from fomo_trade.py system
  - Cleans up bracket1/bracket2 tracking files
  - Clears position territory tracking
  - Then closes any remaining positions

EXECUTION SEQUENCE:
  1. Identify all open positions
  2. For each position:
     a. Cancel all bracket orders (stop + target from fomo_trade.py)
     b. Clean up bracket tracking files
     c. Cancel any pending sell orders
     d. Place limit order at midpoint (60-second timeout)
     e. If limit fails, sell at market price
  3. Send email report
  4. Clean up all position tracking files

EXIT STRATEGY:
  1. Get bid/ask quotes for each position
  2. Calculate midpoint price (bid + ask) / 2
  3. Place limit order at midpoint (60-second timeout)
  4. If limit doesn't fill, auto-cancel and sell at market price
  5. Log all trades to closing_bell_log.txt

POSITIONS CLOSED:
  - All stock holdings
  - All cryptocurrency holdings
  - All long and short positions

EXAMPLES:
  python strategies/closing_bell.py
    - Close all open positions immediately
    - Limit order for 60 seconds at midpoint
    - Falls back to market order if needed

  python strategies/closing_bell.py --help
    - Show detailed usage

OUTPUT:
  Console output:
  - Symbol, quantity, and exit price for each position
  - Order type (limit or market)
  - Fill confirmations
  - Total P&L on close
  
  Log file: strategies/closing_bell_log.txt
  - Timestamp for each trade
  - Order IDs and status
  - Fill prices and quantities

IMPORTANT NOTES:
  - Closes ALL positions (both stocks and crypto)
  - No confirmation prompts (use with caution!)
  - Ideal to run as Windows Task Scheduler job at market close
  - Limit order timeout: 60 seconds
  - Best for end-of-day cleanup (4:00 PM ET)
  - Requires active trading account and Alpaca API credentials
  - Order time in force: DAY (expires if not filled by close)
""")
        return 0
    
    try:
        credentials, trading_client = bootstrap_trading_auth("closing_bell.py")
    except Exception as exc:
        print(f"Authentication failed: {exc}")
        sys.exit(1)
    
    stock_data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
    crypto_data_client = CryptoHistoricalDataClient(credentials.api_key, credentials.secret_key)
    
    close_all_positions(trading_client, stock_data_client, crypto_data_client)


if __name__ == "__main__":
    main()
