#!/usr/bin/env python3
"""
Closing Bell - Automated position closer at market close (12:55 PM MST)

Sells every open position at market close:
1. First attempts to sell at the midpoint of bid/ask for each position
2. If the order doesn't fill within 1 minute, cancels and sells at market price

Usage:
    python strategies/closing_bell.py

This script runs continuously and triggers at 12:55 PM MST every trading day.
Press Ctrl+C to stop.
"""

import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from alpaca.common.exceptions import APIError
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

# ── Configuration ────────────────────────────────────────────────────────────
CLOSING_HOUR = 12
CLOSING_MINUTE = 55
CHECK_INTERVAL = 30  # seconds between time checks
LIMIT_ORDER_TIMEOUT = 60  # seconds to wait for limit order to fill
LOG_FILE = os.path.join(os.path.dirname(__file__), "closing_bell_log.txt")

# ── Constants ────────────────────────────────────────────────────────────────
ORDER_TIME_IN_FORCE = TimeInForce.DAY


def _log(message: str) -> None:
    """Log message to both console and file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"
    print(log_entry)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_entry + "\n")


def _is_trading_hours() -> bool:
    """Check if we're within trading hours (9:30 AM - 4:00 PM MST)."""
    now = datetime.now()
    hour = now.hour
    minute = now.minute
    weekday = now.weekday()  # 0 = Monday, 4 = Friday, 5 = Saturday, 6 = Sunday
    
    # Skip weekends
    if weekday >= 5:
        return False
    
    # 9:30 AM to 4:00 PM (16:00)
    if hour < 9 or hour > 16:
        return False
    
    if hour == 9 and minute < 30:
        return False
    
    return True


def _is_closing_time() -> bool:
    """Check if it's 12:55 PM MST."""
    now = datetime.now()
    return now.hour == CLOSING_HOUR and now.minute == CLOSING_MINUTE


def _is_crypto(symbol: str) -> bool:
    """Check if symbol is a crypto pair (contains /)."""
    return "/" in symbol


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
                "market_value": position.market_value,
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
        order = LimitOrderRequest(
            symbol=symbol,
            qty=int(qty) if qty == int(qty) else qty,
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
                if order_status.filled_qty and order_status.filled_qty > 0:
                    _log(f"  ✓ Limit order filled! {order_status.filled_qty} {symbol} @ avg ${order_status.filled_avg_price:.2f}")
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
        order = MarketOrderRequest(
            symbol=symbol,
            qty=int(qty) if qty == int(qty) else qty,
            side=OrderSide.SELL,
            time_in_force=ORDER_TIME_IN_FORCE,
        )
        response = trading_client.submit_order(order_data=order)
        _log(f"  ✓ Market sell order submitted for {qty} {symbol} (Order ID: {response.id})")
        return True
    except Exception as e:
        _log(f"  ✗ Error submitting market order for {symbol}: {e}")
        return False


def close_all_positions(trading_client, stock_data_client, crypto_data_client) -> None:
    """Close all open positions."""
    _log("\n" + "=" * 60)
    _log("🔔 CLOSING BELL - Closing all positions...")
    _log("=" * 60)
    
    positions = _get_all_positions(trading_client)
    
    if not positions:
        _log("✓ No open positions to close.")
        _log("=" * 60 + "\n")
        return
    
    _log(f"Found {len(positions)} open position(s). Starting closeout...")
    
    successful_closes = 0
    failed_closes = 0
    
    for position in positions:
        symbol = position["symbol"]
        qty = position["qty"]
        market_value = position["market_value"]
        
        _log(f"\n[{symbol}] Qty: {qty}, Value: ${market_value:.2f}")
        
        # Try to get bid/ask for midpoint calculation
        bid_ask = _get_bid_ask(trading_client, symbol, stock_data_client, crypto_data_client)
        
        if bid_ask:
            bid, ask = bid_ask
            midpoint = (bid + ask) / 2.0
            _log(f"  Bid: ${bid:.2f}, Ask: ${ask:.2f}, Midpoint: ${midpoint:.2f}")
            
            # Try limit order at midpoint first
            if _sell_at_limit(trading_client, symbol, qty, midpoint, LIMIT_ORDER_TIMEOUT):
                successful_closes += 1
                continue
        else:
            _log(f"  ⚠ Could not get bid/ask, proceeding to market order")
        
        # If limit order failed or bid/ask unavailable, use market order
        if _sell_at_market(trading_client, symbol, qty):
            successful_closes += 1
        else:
            failed_closes += 1
    
    _log("\n" + "=" * 60)
    _log(f"Closeout Summary: {successful_closes} successful, {failed_closes} failed")
    _log("=" * 60 + "\n")


def main():
    """Main loop."""
    try:
        credentials, trading_client = bootstrap_trading_auth("closing_bell.py")
    except Exception as exc:
        print(f"Authentication failed: {exc}")
        sys.exit(1)
    
    stock_data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
    crypto_data_client = CryptoHistoricalDataClient(credentials.api_key, credentials.secret_key)
    
    _log("\n🔔 Closing Bell initialized. Waiting for 12:55 PM MST...")
    _log(f"Current time: {datetime.now().strftime('%H:%M:%S %Z')}")
    
    last_run_date = None  # Track the date we last ran to avoid duplicate runs
    
    try:
        while True:
            now = datetime.now()
            
            # Check if it's trading hours
            if not _is_trading_hours():
                # Only log this once per day at market open time
                if now.hour == 9 and now.minute == 30:
                    _log(f"[{now.strftime('%H:%M:%S')}] Market closed or weekend, waiting...")
                time.sleep(CHECK_INTERVAL)
                continue
            
            # Check if it's closing time
            if _is_closing_time():
                today = now.date()
                
                # Run only once per day
                if last_run_date != today:
                    close_all_positions(trading_client, stock_data_client, crypto_data_client)
                    last_run_date = today
                else:
                    _log(f"[{now.strftime('%H:%M:%S')}] Already closed positions today, waiting...")
            
            time.sleep(CHECK_INTERVAL)
    
    except KeyboardInterrupt:
        _log("\n\n🛑 Closing Bell stopped by user.")
    except Exception as e:
        _log(f"\n✗ Unexpected error: {e}")
        raise


if __name__ == "__main__":
    main()
