import os
import re
import sys
from alpaca.common.exceptions import APIError
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient, OptionHistoricalDataClient
from alpaca.data.requests import CryptoLatestTradeRequest, StockLatestTradeRequest, OptionLatestTradeRequest
import time
from datetime import datetime

from roles.credentials import bootstrap_trading_auth

try:
    credentials, trading_client = bootstrap_trading_auth("fomo_trade.py")
except Exception as exc:
    print(f"Authentication failed: {exc}")
    raise SystemExit(1)

PAPER = credentials.paper
OPTION_SYMBOL_PATTERN = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")


def _is_option_symbol(symbol: str) -> bool:
    return bool(OPTION_SYMBOL_PATTERN.match(symbol.upper()))


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


def _place_entry_order() -> str:
    entry_order = LimitOrderRequest(
        symbol=SYMBOL,
        qty=TOTAL_QTY,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.GTC,
        limit_price=ENTRY_PRICE,
    )
    entry_response = trading_client.submit_order(order_data=entry_order)
    return str(entry_response.id)


def _get_current_price(symbol: str) -> float:
    if IS_CRYPTO:
        price_request = CryptoLatestTradeRequest(symbol_or_symbols=symbol)
        latest_trade = data_client.get_crypto_latest_trade(price_request)
    elif IS_OPTION:
        price_request = OptionLatestTradeRequest(symbol_or_symbols=symbol, feed=OptionsFeed.INDICATIVE)
        latest_trade = data_client.get_option_latest_trade(price_request)
    else:
        price_request = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
        latest_trade = data_client.get_stock_latest_trade(price_request)
    return float(latest_trade[symbol].price)


def _normalize_position_qty(raw_qty: float) -> float | int:
    if IS_CRYPTO:
        return abs(float(raw_qty))
    # Stock orders should use whole-share integer quantities.
    return max(int(round(abs(float(raw_qty)))), 0)

def _print_usage() -> None:
    print("Usage: python fomo_trade.py <TICKER> <NUM_STOCKS> <ENTRY_PRICE> <STOP_PRICE> <TARGET1_PRICE> <TARGET2_PRICE>")
    print("Example: python fomo_trade.py MU 2 780 770 800 900")


# Parse arguments: ticker, number of stocks, entry, stop, target1, target2
if len(sys.argv) != 7:
    print(f"Error: expected 6 arguments, got {len(sys.argv) - 1}")
    _print_usage()
    sys.exit(1)

SYMBOL = sys.argv[1].upper()
try:
    NUM_STOCKS = int(sys.argv[2])
    ENTRY_PRICE = float(sys.argv[3])
    STOP_PRICE = float(sys.argv[4])
    TARGET1_PRICE = float(sys.argv[5])
    TARGET2_PRICE = float(sys.argv[6])
except ValueError:
    print("Error: NUM_STOCKS must be an integer and price arguments must be numeric values")
    _print_usage()
    sys.exit(1)

if NUM_STOCKS <= 0:
    print("Error: NUM_STOCKS must be a positive integer")
    _print_usage()
    sys.exit(1)

if NUM_STOCKS % 2 != 0:
    print(f"Error: NUM_STOCKS must be an even number, got {NUM_STOCKS}")
    _print_usage()
    sys.exit(1)

if STOP_PRICE >= ENTRY_PRICE:
    print(f"Error: STOP_PRICE must be lower than ENTRY_PRICE for long trades (stop={STOP_PRICE}, entry={ENTRY_PRICE})")
    _print_usage()
    sys.exit(1)

if TARGET1_PRICE <= ENTRY_PRICE or TARGET2_PRICE <= TARGET1_PRICE:
    print(
        "Error: expected long-trade targets with ENTRY_PRICE < TARGET1_PRICE < TARGET2_PRICE "
        f"(entry={ENTRY_PRICE}, target1={TARGET1_PRICE}, target2={TARGET2_PRICE})"
    )
    _print_usage()
    sys.exit(1)

IS_CRYPTO = "/" in SYMBOL
IS_OPTION = _is_option_symbol(SYMBOL)
data_client = (
    CryptoHistoricalDataClient(credentials.api_key, credentials.secret_key)
    if IS_CRYPTO
    else OptionHistoricalDataClient(credentials.api_key, credentials.secret_key)
    if IS_OPTION
    else StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
)

# Trading parameters
NUM_CONTRACTS = NUM_STOCKS
FIRST_TARGET_QTY = NUM_STOCKS // 2
SECOND_TARGET_QTY = NUM_STOCKS - FIRST_TARGET_QTY
TOTAL_QTY = float(NUM_STOCKS)
CHECK_INTERVAL = 5  # Check market every 5 seconds
FLAT_CHECK_INTERVAL = 1  # Faster checks while waiting for a re-entry trigger

# Track state
entry_order_id = None
first_contract_sold = False
breakeven_stop_set = False
current_stop_loss = STOP_PRICE
second_contract_stop_loss = None
awaiting_reentry_after_stop = False
last_observed_price = None

print(f"╔════════════════════════════════════════╗")
print(f"║     FOMO Trade Bot for {SYMBOL:<22} ║")
print(f"╠════════════════════════════════════════╣")
print(f"║ Entry Price:      ${ENTRY_PRICE:.2f}")
print(f"║ Shares:           {NUM_STOCKS} (Target1: {FIRST_TARGET_QTY}, Target2: {SECOND_TARGET_QTY})")
print(f"║ Stop Price:       ${STOP_PRICE:.2f}")
print(f"║ Profit Target 1:  ${TARGET1_PRICE:.2f}")
print(f"║ Profit Target 2:  ${TARGET2_PRICE:.2f}")
print(f"║ Paper Trading:    {PAPER}")
print(f"╚════════════════════════════════════════╝\n")

# Check if position already exists
print(f"[{datetime.now().strftime('%H:%M:%S')}] Checking for existing position...")
existing_position = _find_position_for_symbol(SYMBOL, retries=5, delay_seconds=1.0)

if existing_position:
    print(f"✓ Existing position found: {existing_position.qty} {SYMBOL}")
    print(f"  Monitoring existing position...")
    # Don't place new order
else:
    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] No existing position. "
        f"Waiting for price to reach/reclaim entry ${ENTRY_PRICE:.2f} for {SYMBOL}."
    )

print(f"[{datetime.now().strftime('%H:%M:%S')}] Stop loss set at ${STOP_PRICE:.2f}")
print(f"(Stop loss will be monitored and executed automatically)")
print(f"[{datetime.now().strftime('%H:%M:%S')}] Entry trigger line set at ${ENTRY_PRICE:.2f} (limit order on cross)")

print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Monitoring position... Press Ctrl+C to exit\n")

# 3. Continuous monitoring loop
try:
    while True:
        try:
            # Get current price
            current_price = _get_current_price(SYMBOL)

            crossed_above_entry = (
                (last_observed_price is None and current_price >= ENTRY_PRICE)
                or (
                    last_observed_price is not None
                    and last_observed_price < ENTRY_PRICE
                    and current_price >= ENTRY_PRICE
                )
            )
            
            # Get current position
            current_position = _find_position_for_symbol(SYMBOL, retries=3, delay_seconds=1.0)
            
            if current_position is None:
                symbol_orders = _get_open_orders_for_symbol(SYMBOL)
                buy_orders = [
                    o for o in symbol_orders
                    if (o.side.value if hasattr(o.side, "value") else str(o.side)).upper() == "BUY"
                ]
                if buy_orders:
                    print(
                        f"[{datetime.now().strftime('%H:%M:%S')}] No active position yet; "
                        f"{len(buy_orders)} BUY order(s) still open for {SYMBOL}."
                    )
                else:
                    if awaiting_reentry_after_stop and crossed_above_entry:
                        prev_price_text = "startup" if last_observed_price is None else f"${last_observed_price:.2f}"
                        print(
                            f"[{datetime.now().strftime('%H:%M:%S')}] Re-entry trigger: "
                            f"price crossed above entry ({prev_price_text} -> ${current_price:.2f}). "
                            f"Submitting LIMIT BUY for {TOTAL_QTY} {SYMBOL} at ${ENTRY_PRICE:.2f}..."
                        )
                        try:
                            entry_order_id = _place_entry_order()
                            first_contract_sold = False
                            breakeven_stop_set = False
                            second_contract_stop_loss = None
                            awaiting_reentry_after_stop = False
                            print(f"✓ Re-entry BUY submitted. Order ID: {entry_order_id}")
                        except Exception as e:
                            print(f"✗ Error submitting re-entry BUY: {e}")
                    elif not awaiting_reentry_after_stop and crossed_above_entry:
                        prev_price_text = "startup" if last_observed_price is None else f"${last_observed_price:.2f}"
                        print(
                            f"[{datetime.now().strftime('%H:%M:%S')}] Initial entry trigger: "
                            f"price crossed entry ({prev_price_text} -> ${current_price:.2f}). "
                            f"Submitting LIMIT BUY for {TOTAL_QTY} {SYMBOL} at ${ENTRY_PRICE:.2f}..."
                        )
                        try:
                            entry_order_id = _place_entry_order()
                            first_contract_sold = False
                            breakeven_stop_set = False
                            second_contract_stop_loss = None
                            print(f"✓ Entry BUY submitted. Order ID: {entry_order_id}")
                        except Exception as e:
                            print(f"✗ Error submitting entry BUY: {e}")
                    else:
                        print(
                            f"[{datetime.now().strftime('%H:%M:%S')}] No position and no open BUY orders "
                            f"for {SYMBOL}. Waiting for re-entry at ${ENTRY_PRICE:.2f} "
                            f"(current ${current_price:.2f})."
                        )
                last_observed_price = current_price
                time.sleep(FLAT_CHECK_INTERVAL)
                continue
            
            # Calculate P&L
            current_qty = _normalize_position_qty(float(current_position.qty))
            unrealized_pnl = float(current_position.unrealized_pl)

            if current_qty == 0:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Position qty resolved to 0. Waiting for next refresh...")
                time.sleep(CHECK_INTERVAL)
                continue
            
            active_stop = second_contract_stop_loss if (first_contract_sold and breakeven_stop_set) else current_stop_loss
            print(
                f"[{datetime.now().strftime('%H:%M:%S')}] Price: ${current_price:.2f} | "
                f"Position: {current_qty} | Stop: ${active_stop:.2f} | P&L: ${unrealized_pnl:.2f}",
                end=""
            )
            
            # CHECK STOP LOSS FIRST
            # Stop loss for all contracts initially
            if not first_contract_sold and current_price <= current_stop_loss:
                print(f"\n✗ STOP LOSS HIT at ${current_price:.2f}! Closing all {current_qty} {SYMBOL}...")
                try:
                    close_order = MarketOrderRequest(
                        symbol=SYMBOL,
                        qty=current_qty,
                        side=OrderSide.SELL,
                        time_in_force=TimeInForce.GTC
                    )
                    close_response = trading_client.submit_order(order_data=close_order)
                    print(f"✓ Position closed. Order ID: {close_response.id}")
                    print(f"\n╔════════════════════════════════════════╗")
                    print(f"║       STOPPED OUT                      ║")
                    print(f"║ Loss: ${unrealized_pnl:.2f}")
                    print(f"╚════════════════════════════════════════╝")
                    # Reset state for potential re-entry
                    first_contract_sold = False
                    breakeven_stop_set = False
                    second_contract_stop_loss = None
                    awaiting_reentry_after_stop = True
                    last_observed_price = current_price
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Ready for next trade...\n")
                except Exception as e:
                    print(f"✗ Error executing stop loss: {e}")
            
            # Stop loss for remaining contract after first sale (at breakeven)
            elif first_contract_sold and breakeven_stop_set and current_price <= second_contract_stop_loss:
                print(f"\n✗ BREAKEVEN STOP HIT at ${current_price:.2f}! Closing remaining {current_qty} {SYMBOL}...")
                try:
                    close_order = MarketOrderRequest(
                        symbol=SYMBOL,
                        qty=current_qty,
                        side=OrderSide.SELL,
                        time_in_force=TimeInForce.GTC
                    )
                    close_response = trading_client.submit_order(order_data=close_order)
                    print(f"✓ Position closed. Order ID: {close_response.id}")
                    print(f"\n╔════════════════════════════════════════╗")
                    print(f"║     BREAKEVEN STOP HIT                 ║")
                    print(f"║ Final P&L: ${unrealized_pnl:.2f}")
                    print(f"╚════════════════════════════════════════╝")
                    # Reset state for potential re-entry
                    first_contract_sold = False
                    breakeven_stop_set = False
                    second_contract_stop_loss = None
                    awaiting_reentry_after_stop = True
                    last_observed_price = current_price
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Ready for next trade...\n")
                except Exception as e:
                    print(f"✗ Error executing breakeven stop: {e}")
            
            # Check if first profit target reached
            elif not first_contract_sold and current_price >= TARGET1_PRICE:
                print(f"\n✓ FIRST PROFIT TARGET HIT at ${current_price:.2f}!")
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Selling first half ({FIRST_TARGET_QTY} {SYMBOL})...")
                
                try:
                    # Sell first contract
                    sell_order = MarketOrderRequest(
                        symbol=SYMBOL,
                        qty=FIRST_TARGET_QTY,
                        side=OrderSide.SELL,
                        time_in_force=TimeInForce.GTC
                    )
                    sell_response = trading_client.submit_order(order_data=sell_order)
                    print(f"✓ Sold first contract. Order ID: {sell_response.id}")
                    first_contract_sold = True
                    
                    # Set breakeven stop loss for remaining contract
                    second_contract_stop_loss = ENTRY_PRICE
                    breakeven_stop_set = True
                    print(f"✓ Breakeven stop loss set at ${ENTRY_PRICE:.2f} for remaining contract")
                
                except Exception as e:
                    print(f"✗ Error selling first contract: {e}")
            
            # Check if second profit target reached
            elif first_contract_sold and current_price >= TARGET2_PRICE:
                print(f"\n✓ SECOND PROFIT TARGET HIT at ${current_price:.2f}!")
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Closing remaining position ({current_qty} {SYMBOL})...")
                
                try:
                    close_order = MarketOrderRequest(
                        symbol=SYMBOL,
                        qty=current_qty,
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
                    breakeven_stop_set = False
                    second_contract_stop_loss = None
                    awaiting_reentry_after_stop = False
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Ready for next trade...\n")
                except Exception as e:
                    print(f"✗ Error closing position: {e}")
            
            else:
                print()

            last_observed_price = current_price
            
            time.sleep(CHECK_INTERVAL)
        
        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"\n✗ Error: {e}")
            time.sleep(CHECK_INTERVAL)

except KeyboardInterrupt:
    print("\n\nBot stopped by user")
