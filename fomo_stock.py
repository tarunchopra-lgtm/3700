#!/usr/bin/env python3
"""
FOMO Stock Trading Bot - Continuous monitoring with manual entry/stop/target control

Usage: python fomo_stock.py <SYMBOL> <ENTRY_PRICE> <STOP_PRICE> <TARGET1> <TARGET2>

Arguments:
    SYMBOL: Stock ticker (e.g., AAPL, MU)
    ENTRY_PRICE: Limit buy price
    STOP_PRICE: Stop loss price
    TARGET1: First profit target (sell 1 share)
    TARGET2: Second profit target (sell 1 share)

Example: python fomo_stock.py MU 80 75 90 95

Features:
- Buys 2 shares at entry limit price
- No duplicate buys if position exists
- If position exists at start, updates stops/targets only
- Monitors every 60 seconds
- Sells 1 share at Target1, moves stop to breakeven
- Sells last share at Target2
- If both stops hit, waits to re-enter on reversal
"""

import os
import sys
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest
import time
from datetime import datetime

from roles.credentials import bootstrap_trading_auth

try:
    credentials, trading_client = bootstrap_trading_auth("fomo_stock.py")
except Exception as exc:
    print(f"Authentication failed: {exc}")
    raise SystemExit(1)

PAPER = credentials.paper
data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)


def _normalize_symbol(symbol: str) -> str:
    return symbol.replace("/", "").replace("-", "").upper()


def _find_position_for_symbol(symbol: str, retries: int = 3, delay_seconds: float = 1.0):
    target = _normalize_symbol(symbol)
    for attempt in range(1, retries + 1):
        positions = trading_client.get_all_positions()
        for pos in positions:
            if _normalize_symbol(getattr(pos, "symbol", "")) == target:
                return pos
        if attempt < retries:
            time.sleep(delay_seconds)
    return None


def _get_open_orders_for_symbol(symbol: str):
    target = _normalize_symbol(symbol)
    orders = list(
        trading_client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=100)
        )
    )
    return [o for o in orders if _normalize_symbol(getattr(o, "symbol", "")) == target]

# Parse arguments
if len(sys.argv) < 6:
    print(__doc__)
    sys.exit(1)

SYMBOL = sys.argv[1].upper()
ENTRY_PRICE = float(sys.argv[2])
STOP_PRICE = float(sys.argv[3])
TARGET1_PRICE = float(sys.argv[4])
TARGET2_PRICE = float(sys.argv[5])
IS_CRYPTO = "/" in SYMBOL

# Trading parameters
NUM_CONTRACTS = 2
QTY_PER_CONTRACT = 0.01 if IS_CRYPTO else 1
TOTAL_QTY = NUM_CONTRACTS * QTY_PER_CONTRACT
CHECK_INTERVAL = 60  # Check market every 60 seconds (1 minute)

# Track state
entry_order_id = None
first_contract_sold = False
second_contract_sold = False
breakeven_stop_set = False
current_stop_loss = STOP_PRICE
second_contract_stop_loss = None
waiting_for_reentry = False  # After stop loss, wait for price to return to entry
internal_qty = TOTAL_QTY  # Track qty internally (Alpaca position may lag)

print(f"\n╔════════════════════════════════════════╗")
print(f"║     FOMO Stock Bot for {SYMBOL:<21} ║")
print(f"╠════════════════════════════════════════╣")
print(f"║ Entry Price:      ${ENTRY_PRICE:.2f}")
print(f"║ Stop Loss:        ${STOP_PRICE:.2f}")
print(f"║ Target 1:         ${TARGET1_PRICE:.2f} (sell 1 share)")
print(f"║ Target 2:         ${TARGET2_PRICE:.2f} (sell 1 share)")
print(f"║ Contracts:        {NUM_CONTRACTS} x {QTY_PER_CONTRACT} = {TOTAL_QTY} {'BTC' if IS_CRYPTO else 'shares'}")
print(f"║ Check Interval:   Every {CHECK_INTERVAL}s (1 minute)")
print(f"║ Paper Trading:    {PAPER}")
print(f"╚════════════════════════════════════════╝\n")

# Check if position already exists
print(f"[{datetime.now().strftime('%H:%M:%S')}] Checking for existing position...")
existing_position = _find_position_for_symbol(SYMBOL, retries=5, delay_seconds=1.0)

if existing_position:
    print(f"✓ Existing position found: {existing_position.qty} {SYMBOL}")
    
    # Cancel any lingering open orders for this symbol so shares are available to sell
    try:
        symbol_orders = _get_open_orders_for_symbol(SYMBOL)
        if symbol_orders:
            print(f"  ⚠ Found {len(symbol_orders)} open order(s) for {SYMBOL} — cancelling to free shares...")
            for o in symbol_orders:
                try:
                    trading_client.cancel_order_by_id(o.id)
                    side = o.side.value if hasattr(o.side, 'value') else str(o.side)
                    print(f"  ✓ Cancelled {side.upper()} {o.qty} @ {o.limit_price or 'Market'} (ID: {o.id})")
                except Exception as ce:
                    print(f"  ✗ Could not cancel order {o.id}: {ce}")
        else:
            print(f"  ✓ No open orders for {SYMBOL}")
    except Exception as e:
        print(f"  ⚠ Could not check open orders: {e}")
    
    print(f"  Entry Price (assumed): ${ENTRY_PRICE:.2f}")
    print(f"  Stop Loss updated to: ${STOP_PRICE:.2f}")
    print(f"  Target 1 updated to: ${TARGET1_PRICE:.2f}")
    print(f"  Target 2 updated to: ${TARGET2_PRICE:.2f}")
    print(f"  Monitoring position only (no new buy orders)...\n")
    current_stop_loss = STOP_PRICE
else:
    # Check current price before placing buy — only buy if price is AT or BELOW entry (valid limit)
    try:
        _check = data_client.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=SYMBOL))
        _current = _check[SYMBOL].price
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Current price: ${_current:.2f} | Entry limit: ${ENTRY_PRICE:.2f}")
        if _current > ENTRY_PRICE:
            print(f"⚠ Current price ${_current:.2f} is ABOVE entry ${ENTRY_PRICE:.2f}")
            print(f"  Limit buy order will only fill when price drops to ${ENTRY_PRICE:.2f}")
    except:
        pass

    print(f"[{datetime.now().strftime('%H:%M:%S')}] No existing position. Placing entry order for {TOTAL_QTY} {SYMBOL} at limit price ${ENTRY_PRICE:.2f}...")
    try:
        entry_order = LimitOrderRequest(
            symbol=SYMBOL,
            qty=TOTAL_QTY,
            side=OrderSide.BUY,
            limit_price=ENTRY_PRICE,
            time_in_force=TimeInForce.GTC
        )
        entry_response = trading_client.submit_order(order_data=entry_order)
        entry_order_id = entry_response.id
        print(f"✓ Limit buy order placed at ${ENTRY_PRICE:.2f}")
        print(f"  Order ID: {entry_order_id}")
        time.sleep(2)
    except Exception as e:
        print(f"✗ Error placing entry order: {e}")
        sys.exit(1)

print(f"[{datetime.now().strftime('%H:%M:%S')}] Stop loss set at ${STOP_PRICE:.2f}")
print(f"[{datetime.now().strftime('%H:%M:%S')}] Target 1: ${TARGET1_PRICE:.2f} (sell 1 share, move stop to breakeven)")
print(f"[{datetime.now().strftime('%H:%M:%S')}] Target 2: ${TARGET2_PRICE:.2f} (sell remaining share)")
print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Monitoring position... Press Ctrl+C to exit\n")

# Continuous monitoring loop
try:
    while True:
        try:
            # Get current price
            price_request = StockLatestTradeRequest(symbol_or_symbols=SYMBOL)
            latest_trade = data_client.get_stock_latest_trade(price_request)
            current_price = latest_trade[SYMBOL].price
            
            # Get current position (with retries for fill/position propagation lag)
            current_position = _find_position_for_symbol(SYMBOL, retries=3, delay_seconds=1.0)
            
            if current_position is None:
                if waiting_for_reentry:
                    # Wait for price to recover BACK UP to entry level, then re-enter
                    if current_price >= ENTRY_PRICE:
                        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Price ${current_price:.2f} recovered to entry ${ENTRY_PRICE:.2f}. Re-entering...")
                        try:
                            reentry_order = LimitOrderRequest(
                                symbol=SYMBOL,
                                qty=TOTAL_QTY,
                                side=OrderSide.BUY,
                                limit_price=ENTRY_PRICE,
                                time_in_force=TimeInForce.GTC
                            )
                            reentry_response = trading_client.submit_order(order_data=reentry_order)
                            print(f"✓ Re-entry limit buy placed at ${ENTRY_PRICE:.2f}. Order ID: {reentry_response.id}")
                            current_stop_loss = STOP_PRICE
                            waiting_for_reentry = False
                            internal_qty = TOTAL_QTY  # Reset for fresh position
                        except Exception as e:
                            print(f"✗ Error placing re-entry order: {e}")
                    else:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] Waiting for reversal to ${ENTRY_PRICE:.2f} | Current: ${current_price:.2f}")
                else:
                    symbol_orders = _get_open_orders_for_symbol(SYMBOL)
                    buy_orders = [
                        o for o in symbol_orders
                        if (o.side.value if hasattr(o.side, 'value') else str(o.side)).upper() == "BUY"
                    ]
                    if buy_orders:
                        print(
                            f"[{datetime.now().strftime('%H:%M:%S')}] No active position yet; "
                            f"{len(buy_orders)} BUY order(s) still open for {SYMBOL}. "
                            f"Waiting for fill at ${ENTRY_PRICE:.2f} | Current: ${current_price:.2f}"
                        )
                    else:
                        print(
                            f"[{datetime.now().strftime('%H:%M:%S')}] No position and no open BUY orders "
                            f"for {SYMBOL}. Current: ${current_price:.2f}"
                        )
                time.sleep(CHECK_INTERVAL)
                continue
            
            # Calculate P&L
            unrealized_pnl = float(current_position.unrealized_pl)
            entry_price_from_position = float(current_position.avg_entry_price) if hasattr(current_position, 'avg_entry_price') else ENTRY_PRICE
            current_qty = max(int(round(abs(float(current_position.qty)))), 0)
            if current_qty == 0:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Position qty reported as 0. Waiting for next refresh...")
                time.sleep(CHECK_INTERVAL)
                continue

            # Always re-sync risk levels from active position and command-line values.
            internal_qty = current_qty
            if not first_contract_sold:
                current_stop_loss = STOP_PRICE
            elif breakeven_stop_set:
                second_contract_stop_loss = entry_price_from_position
            
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Price: ${current_price:.2f} | Position: {int(current_qty)} | P&L: ${unrealized_pnl:.2f}", end="")
            
            # CHECK STOP LOSS FIRST
            # Stop loss triggers exit ONLY if not both orders already sold and not already waiting for re-entry
            if not second_contract_sold and not waiting_for_reentry and current_price <= current_stop_loss:
                print(f"\n✗ STOP LOSS HIT at ${current_price:.2f}! Closing all {int(current_qty)} shares...")
                try:
                    close_order = MarketOrderRequest(
                        symbol=SYMBOL,
                        qty=int(current_qty),
                        side=OrderSide.SELL,
                        time_in_force=TimeInForce.GTC
                    )
                    close_response = trading_client.submit_order(order_data=close_order)
                    print(f"✓ Position closed. Order ID: {close_response.id}")
                    print(f"\n╔════════════════════════════════════════╗")
                    print(f"║       STOPPED OUT                      ║")
                    print(f"║ Loss: ${unrealized_pnl:.2f}")
                    print(f"╚════════════════════════════════════════╝")
                    # Reset state - ready to re-enter on reversal
                    first_contract_sold = False
                    second_contract_sold = False
                    breakeven_stop_set = False
                    second_contract_stop_loss = None
                    waiting_for_reentry = True
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Waiting for price to return to ${ENTRY_PRICE:.2f} to re-enter...\n")
                except Exception as e:
                    print(f"✗ Error executing stop loss: {e}")
            
            # Stop loss for remaining contract after first sale (at breakeven)
            elif first_contract_sold and not second_contract_sold and breakeven_stop_set and not waiting_for_reentry and current_price <= second_contract_stop_loss:
                print(f"\n✗ BREAKEVEN STOP HIT at ${current_price:.2f}! Closing remaining {int(current_qty)} shares...")
                try:
                    close_order = MarketOrderRequest(
                        symbol=SYMBOL,
                        qty=int(current_qty),
                        side=OrderSide.SELL,
                        time_in_force=TimeInForce.GTC
                    )
                    close_response = trading_client.submit_order(order_data=close_order)
                    print(f"✓ Position closed. Order ID: {close_response.id}")
                    print(f"\n╔════════════════════════════════════════╗")
                    print(f"║     BREAKEVEN STOP HIT                 ║")
                    print(f"║ Final P&L: ${unrealized_pnl:.2f}")
                    print(f"╚════════════════════════════════════════╝")
                    # Reset state - ready to re-enter on reversal
                    first_contract_sold = False
                    second_contract_sold = False
                    breakeven_stop_set = False
                    second_contract_stop_loss = None
                    waiting_for_reentry = True
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Waiting for price to return to ${ENTRY_PRICE:.2f} to re-enter...\n")
                except Exception as e:
                    print(f"✗ Error executing breakeven stop: {e}")
            
            # Check if first profit target reached
            elif not first_contract_sold and current_price >= TARGET1_PRICE:
                print(f"\n✓ FIRST TARGET HIT at ${current_price:.2f}!")
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Selling first share ({QTY_PER_CONTRACT} {SYMBOL})...")
                
                try:
                    sell_qty = min(QTY_PER_CONTRACT, int(current_qty))
                    # Sell first contract
                    sell_order = MarketOrderRequest(
                        symbol=SYMBOL,
                        qty=sell_qty,
                        side=OrderSide.SELL,
                        time_in_force=TimeInForce.GTC
                    )
                    sell_response = trading_client.submit_order(order_data=sell_order)
                    print(f"✓ Sold first share. Order ID: {sell_response.id}")
                    first_contract_sold = True
                    internal_qty = max(int(current_qty) - sell_qty, 0)
                    
                    # Set breakeven stop loss for remaining contract
                    second_contract_stop_loss = entry_price_from_position
                    breakeven_stop_set = True
                    print(f"✓ Breakeven stop loss set at ${entry_price_from_position:.2f} for remaining share")
                
                except Exception as e:
                    print(f"✗ Error selling first share: {e}")
            
            # Check if second profit target reached
            elif first_contract_sold and not second_contract_sold and current_price >= TARGET2_PRICE:
                print(f"\n✓ SECOND TARGET HIT at ${current_price:.2f}!")
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Closing remaining position ({int(current_qty)} {SYMBOL})...")
                
                try:
                    close_order = MarketOrderRequest(
                        symbol=SYMBOL,
                        qty=int(current_qty),
                        side=OrderSide.SELL,
                        time_in_force=TimeInForce.GTC
                    )
                    close_response = trading_client.submit_order(order_data=close_order)
                    print(f"✓ Position closed. Order ID: {close_response.id}")
                    print(f"\n╔════════════════════════════════════════╗")
                    print(f"║         TRADE COMPLETE                 ║")
                    print(f"║ Final P&L: ${unrealized_pnl:.2f}")
                    print(f"╚════════════════════════════════════════╝")
                    # Reset state for potential re-entry
                    first_contract_sold = False
                    second_contract_sold = False
                    breakeven_stop_set = False
                    second_contract_stop_loss = None
                    internal_qty = TOTAL_QTY
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Ready for next trade...\n")
                except Exception as e:
                    print(f"✗ Error closing position: {e}")
            
            else:
                print()
            
            time.sleep(CHECK_INTERVAL)
        
        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"\n✗ Error: {e}")
            time.sleep(CHECK_INTERVAL)

except KeyboardInterrupt:
    print("\n\nBot stopped by user")
