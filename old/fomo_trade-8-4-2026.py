import os
import sys
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import CryptoLatestTradeRequest, StockLatestTradeRequest
import time
from datetime import datetime

from roles.credentials import bootstrap_trading_auth

try:
    credentials, trading_client = bootstrap_trading_auth("fomo_trade.py")
except Exception as exc:
    print(f"Authentication failed: {exc}")
    raise SystemExit(1)

PAPER = credentials.paper


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

def _print_usage() -> None:
    print("Usage: python fomo_trade.py <TICKER> <ENTRY_PRICE> <STOP_PRICE> <TARGET1_PRICE> <TARGET2_PRICE>")
    print("Example: python fomo_trade.py BTC/USD 63500 63490 63510 63530")


# Parse arguments: ticker, entry, stop, target1, target2
if len(sys.argv) != 6:
    print(f"Error: expected 5 arguments, got {len(sys.argv) - 1}")
    _print_usage()
    sys.exit(1)

SYMBOL = sys.argv[1].upper()
try:
    ENTRY_PRICE = float(sys.argv[2])
    STOP_PRICE = float(sys.argv[3])
    TARGET1_PRICE = float(sys.argv[4])
    TARGET2_PRICE = float(sys.argv[5])
except ValueError:
    print("Error: ENTRY_PRICE, STOP_PRICE, TARGET1_PRICE and TARGET2_PRICE must be numeric values")
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
data_client = (
    CryptoHistoricalDataClient(credentials.api_key, credentials.secret_key)
    if IS_CRYPTO
    else StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
)

# Trading parameters
QTY_PER_CONTRACT = 0.1 if IS_CRYPTO else 1
NUM_CONTRACTS = 2
TOTAL_QTY = QTY_PER_CONTRACT * NUM_CONTRACTS
CHECK_INTERVAL = 5  # Check market every 5 seconds

# Track state
entry_order_id = None
first_contract_sold = False
breakeven_stop_set = False
current_stop_loss = STOP_PRICE
second_contract_stop_loss = None

print(f"╔════════════════════════════════════════╗")
print(f"║     FOMO Trade Bot for {SYMBOL:<22} ║")
print(f"╠════════════════════════════════════════╣")
print(f"║ Entry Price:      ${ENTRY_PRICE:.2f}")
print(f"║ Contracts:        {NUM_CONTRACTS} x {QTY_PER_CONTRACT} {'BTC' if IS_CRYPTO else 'shares'} = {TOTAL_QTY} {'BTC' if IS_CRYPTO else 'shares'}")
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
        print(f"✓ Limit buy order placed at ${ENTRY_PRICE:.2f}. Order ID: {entry_order_id}")
        time.sleep(2)  # Wait a moment
    except Exception as e:
        print(f"✗ Error placing entry order: {e}")
        sys.exit(1)

print(f"[{datetime.now().strftime('%H:%M:%S')}] Stop loss set at ${STOP_PRICE:.2f}")
print(f"(Stop loss will be monitored and executed automatically)")

print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Monitoring position... Press Ctrl+C to exit\n")

# 3. Continuous monitoring loop
try:
    while True:
        try:
            # Get current price
            if IS_CRYPTO:
                price_request = CryptoLatestTradeRequest(symbol_or_symbols=SYMBOL)
                latest_trade = data_client.get_crypto_latest_trade(price_request)
            else:
                price_request = StockLatestTradeRequest(symbol_or_symbols=SYMBOL)
                latest_trade = data_client.get_stock_latest_trade(price_request)
            current_price = latest_trade[SYMBOL].price
            
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
                    print(
                        f"[{datetime.now().strftime('%H:%M:%S')}] No position and no open BUY orders "
                        f"for {SYMBOL}."
                    )
                time.sleep(CHECK_INTERVAL)
                continue
            
            # Calculate P&L
            current_qty = float(current_position.qty)
            unrealized_pnl = float(current_position.unrealized_pl)
            
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Price: ${current_price:.2f} | Position: {current_qty} | P&L: ${unrealized_pnl:.2f}", end="")
            
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
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Ready for next trade...\n")
                except Exception as e:
                    print(f"✗ Error executing breakeven stop: {e}")
            
            # Check if first profit target reached
            elif not first_contract_sold and current_price >= TARGET1_PRICE:
                print(f"\n✓ FIRST PROFIT TARGET HIT at ${current_price:.2f}!")
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Selling first contract ({QTY_PER_CONTRACT} {SYMBOL})...")
                
                try:
                    # Sell first contract
                    sell_order = MarketOrderRequest(
                        symbol=SYMBOL,
                        qty=QTY_PER_CONTRACT,
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
