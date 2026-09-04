#!/usr/bin/env python3
"""
Closing Bell - Close all open positions

Sells every open position:
1. First attempts to sell at the midpoint of bid/ask for each position
2. If the order doesn't fill within 1 minute, cancels and sells at market price

Usage:
    python strategies/closing_bell.py

Note: Scheduling is handled via Windows Task Scheduler.
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, CryptoLatestQuoteRequest

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth
from roles.email_notify import send_email

# ── Configuration ────────────────────────────────────────────────────────────
LIMIT_ORDER_TIMEOUT = 60  # seconds to wait for limit order to fill
LOG_FILE = os.path.join(os.path.dirname(__file__), "closing_bell_log.txt")
ORDER_TIME_IN_FORCE = TimeInForce.DAY


def _log(message: str) -> None:
    """Log message to both console and file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"
    print(log_entry)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_entry + "\n")



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
        
        # Try to get bid/ask for midpoint calculation
        bid_ask = _get_bid_ask(trading_client, symbol, stock_data_client, crypto_data_client)
        
        if bid_ask:
            bid, ask = bid_ask
            midpoint = (bid + ask) / 2.0
            close_price = midpoint
            _log(f"  Bid: ${bid:.2f}, Ask: ${ask:.2f}, Midpoint: ${midpoint:.2f}")
            
            # Try limit order at midpoint first
            if _sell_at_limit(trading_client, symbol, qty, midpoint, LIMIT_ORDER_TIMEOUT):
                closed_positions.append({"symbol": symbol, "qty": qty, "price": close_price})
                successful_closes += 1
                continue
        else:
            _log(f"  ⚠ Could not get bid/ask, proceeding to market order")
        
        # If limit order failed or bid/ask unavailable, use market order
        if _sell_at_market(trading_client, symbol, qty):
            closed_positions.append({"symbol": symbol, "qty": qty, "price": close_price})
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
