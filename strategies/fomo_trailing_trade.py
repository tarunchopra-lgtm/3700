import os
import re
import sys
import requests
from alpaca.common.exceptions import APIError
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient, OptionHistoricalDataClient
from alpaca.data.requests import CryptoLatestTradeRequest, StockLatestTradeRequest, OptionLatestTradeRequest, OptionLatestQuoteRequest
import time
from datetime import datetime

from pathlib import Path
import sys

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth

REQUEST_TIMEOUT_SECONDS = float(os.getenv("ALPACA_HTTP_TIMEOUT", "15"))


def _install_http_timeout() -> None:
    original_request = requests.sessions.Session.request

    def _request_with_timeout(self, method, url, **kwargs):
        kwargs.setdefault("timeout", REQUEST_TIMEOUT_SECONDS)
        return original_request(self, method, url, **kwargs)

    requests.sessions.Session.request = _request_with_timeout


_install_http_timeout()

DEBUG_TIMING = os.getenv("FOMO_DEBUG_TIMING", "1").strip().lower() not in ("0", "false", "no")

try:
    credentials, trading_client = bootstrap_trading_auth("fomo_trailing_trade.py")
except Exception as exc:
    print(f"Authentication failed: {exc}")
    raise SystemExit(1)

PAPER = credentials.paper
OPTION_SYMBOL_PATTERN = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")


def _is_option_symbol(symbol: str) -> bool:
    return bool(OPTION_SYMBOL_PATTERN.match(symbol.upper()))


def _normalize_symbol(symbol: str) -> str:
    return symbol.replace("/", "").replace("-", "").upper()


def _timed(label: str, fn, *args, **kwargs):
    started = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed = time.perf_counter() - started
    if DEBUG_TIMING and elapsed >= 0.5:
        print(f"[TIMING] {label}: {elapsed:.2f}s", flush=True)
    return result


def _find_position_for_symbol(symbol: str, retries: int = 3, delay_seconds: float = 1.0):
    target = _normalize_symbol(symbol)
    for attempt in range(1, retries + 1):
        try:
            # Fast path: ask Alpaca directly for this symbol instead of scanning all positions.
            pos = _timed("get_open_position", trading_client.get_open_position, symbol)
            if _normalize_symbol(getattr(pos, "symbol", "")) == target:
                return pos
        except Exception:
            # Fallback: broader scan only when direct lookup fails.
            try:
                positions = _timed("get_all_positions", trading_client.get_all_positions)
            except Exception:
                positions = []
            for pos in positions:
                if _normalize_symbol(getattr(pos, "symbol", "")) == target:
                    return pos
        if attempt < retries:
            time.sleep(delay_seconds)
    return None


def _get_open_orders_for_symbol(symbol: str):
    target = _normalize_symbol(symbol)
    orders = list(
        _timed(
            "get_orders(open)",
            trading_client.get_orders,
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=100)
        )
    )
    return [o for o in orders if _normalize_symbol(getattr(o, "symbol", "")) == target]


def _get_option_midpoint() -> tuple[float, float, float]:
    quotes = _timed(
        "get_option_latest_quote",
        data_client.get_option_latest_quote,
        OptionLatestQuoteRequest(symbol_or_symbols=SYMBOL, feed=OptionsFeed.INDICATIVE),
    )
    quote = quotes[SYMBOL]
    bid = float(quote.bid_price)
    ask = float(quote.ask_price)
    if bid <= 0 or ask <= 0 or ask < bid:
        raise RuntimeError(f"Invalid option quote for {SYMBOL}: bid={bid}, ask={ask}")
    return bid, ask, round((bid + ask) / 2.0, 2)


def _refresh_option_entry_levels() -> tuple[float, float, float]:
    global ENTRY_PRICE, STOP_PRICE, TARGET1_PRICE, current_stop_loss

    bid, ask, midpoint = _get_option_midpoint()
    ENTRY_PRICE = midpoint
    STOP_PRICE = max(round(midpoint - STOP_LOSS_DISTANCE, 2), 0.01)
    TARGET1_PRICE = round(midpoint + TARGET1_DISTANCE, 2)
    current_stop_loss = STOP_PRICE
    return bid, ask, midpoint


def _place_entry_order() -> tuple[str, float]:
    limit_price = ENTRY_PRICE
    if REFRESH_OPTION_MIDPOINT:
        bid, ask, limit_price = _refresh_option_entry_levels()
        print(
            f"[QUOTE] Fresh {SYMBOL} bid=${bid:.2f} ask=${ask:.2f} midpoint=${limit_price:.2f}; "
            f"stop=${STOP_PRICE:.2f} target1=${TARGET1_PRICE:.2f} trailing_stop=${TRAILING_STOP_AMOUNT:.2f}"
        )
    entry_order = LimitOrderRequest(
        symbol=SYMBOL,
        qty=TOTAL_QTY,
        side=OrderSide.BUY,
        time_in_force=ORDER_TIME_IN_FORCE,
        limit_price=limit_price,
    )
    entry_response = _timed("submit_order(entry)", trading_client.submit_order, order_data=entry_order)
    return str(entry_response.id), limit_price


def _get_current_price(symbol: str) -> float:
    if IS_CRYPTO:
        price_request = CryptoLatestTradeRequest(symbol_or_symbols=symbol)
        latest_trade = _timed("get_crypto_latest_trade", data_client.get_crypto_latest_trade, price_request)
    elif IS_OPTION:
        price_request = OptionLatestTradeRequest(symbol_or_symbols=symbol, feed=OptionsFeed.INDICATIVE)
        latest_trade = _timed("get_option_latest_trade", data_client.get_option_latest_trade, price_request)
    else:
        price_request = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
        latest_trade = _timed("get_stock_latest_trade", data_client.get_stock_latest_trade, price_request)
    return float(latest_trade[symbol].price)


def _normalize_position_qty(raw_qty: float) -> float | int:
    if IS_CRYPTO:
        return abs(float(raw_qty))
    # Stock orders should use whole-share integer quantities.
    return max(int(round(abs(float(raw_qty)))), 0)

def _print_usage() -> None:
    print("Usage: python fomo_trailing_trade.py <TICKER> <NUM_STOCKS> <ENTRY_PRICE> <STOP_PRICE> <TARGET1_PRICE> <TRAILING_STOP> [--refresh-option-midpoint] [--single-entry]")
    print("Example: python fomo_trailing_trade.py INTC 10 90 89 91 1")
    print("  After TARGET1 is hit, trailing stop activates and closes when price drops TRAILING_STOP from the high")


# Parse arguments: ticker, number of stocks, entry, stop, target1, trailing_stop
if len(sys.argv) < 7:
    print(f"Error: expected 6 arguments, got {len(sys.argv) - 1}")
    _print_usage()
    sys.exit(1)

SYMBOL = sys.argv[1].upper()
try:
    NUM_STOCKS = int(sys.argv[2])
    ENTRY_PRICE = float(sys.argv[3])
    STOP_PRICE = float(sys.argv[4])
    TARGET1_PRICE = float(sys.argv[5])
    TRAILING_STOP_AMOUNT = float(sys.argv[6])
except ValueError:
    print("Error: NUM_STOCKS must be an integer and price arguments must be numeric values")
    _print_usage()
    sys.exit(1)

extra_flags = set(sys.argv[7:])
unknown_flags = extra_flags - {"--refresh-option-midpoint", "--single-entry"}
if unknown_flags:
    print(f"Error: unknown option(s): {', '.join(sorted(unknown_flags))}")
    _print_usage()
    sys.exit(1)
REFRESH_OPTION_MIDPOINT = "--refresh-option-midpoint" in extra_flags
SINGLE_ENTRY_MODE = "--single-entry" in extra_flags

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

if TARGET1_PRICE <= ENTRY_PRICE:
    print(
        "Error: expected long-trade target with ENTRY_PRICE < TARGET1_PRICE "
        f"(entry={ENTRY_PRICE}, target1={TARGET1_PRICE})"
    )
    _print_usage()
    sys.exit(1)

if TRAILING_STOP_AMOUNT <= 0:
    print(f"Error: TRAILING_STOP_AMOUNT must be positive, got {TRAILING_STOP_AMOUNT}")
    _print_usage()
    sys.exit(1)

IS_CRYPTO = "/" in SYMBOL
IS_OPTION = _is_option_symbol(SYMBOL)
if REFRESH_OPTION_MIDPOINT and not IS_OPTION:
    print("Error: --refresh-option-midpoint can only be used with an option symbol")
    sys.exit(1)
STOP_LOSS_DISTANCE = ENTRY_PRICE - STOP_PRICE
TARGET1_DISTANCE = TARGET1_PRICE - ENTRY_PRICE
ORDER_TIME_IN_FORCE = TimeInForce.DAY if IS_OPTION else TimeInForce.GTC
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
trailing_stop_active = False
highest_price_since_target1 = None

print(f"╔════════════════════════════════════════╗")
print(f"║  FOMO TRAILING TRADE Bot for {SYMBOL:<20} ║")
print(f"╠════════════════════════════════════════╣")
print(f"║ Entry Price:      ${ENTRY_PRICE:.2f}")
print(f"║ Shares:           {NUM_STOCKS} (Target1: {FIRST_TARGET_QTY}, Trail: {SECOND_TARGET_QTY})")
print(f"║ Stop Price:       ${STOP_PRICE:.2f}")
print(f"║ Profit Target 1:  ${TARGET1_PRICE:.2f}")
print(f"║ Trailing Stop:    ${TRAILING_STOP_AMOUNT:.2f} (activates at Target1)")
print(f"║ Paper Trading:    {PAPER}")
print(f"╚════════════════════════════════════════╝\n")
print("[INFO] Strategy is long-running by design; prompt will not return unless you press Ctrl+C.", flush=True)
print(f"[INFO] HTTP timeout set to {REQUEST_TIMEOUT_SECONDS:.0f}s per API call.", flush=True)

# Check if position already exists
print(f"[{datetime.now().strftime('%H:%M:%S')}] Checking for existing position...", flush=True)
existing_position = _find_position_for_symbol(SYMBOL, retries=1, delay_seconds=0.0)

if existing_position:
    print(f"✓ Existing position found: {existing_position.qty} {SYMBOL}")
    print(f"  Monitoring existing position...")
    # Don't place new order
else:
    # Check current price before placing initial order
    print(f"[{datetime.now().strftime('%H:%M:%S')}] No existing position.")
    try:
        current_price = _get_current_price(SYMBOL)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Current price: ${current_price:.2f} | Entry price: ${ENTRY_PRICE:.2f}")
        
        # Only place initial order if current price is at or above entry price
        # This ensures we don't immediately fill at worse prices
        if current_price >= ENTRY_PRICE:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Placing LIMIT BUY order for {TOTAL_QTY} {SYMBOL} at ${ENTRY_PRICE:.2f}...")
            try:
                entry_order_id, submitted_price = _place_entry_order()
                print(f"✓ LIMIT BUY order placed successfully at ${submitted_price:.2f}. Order ID: {entry_order_id}")
                print(f"  Order will fill when price comes down to ${ENTRY_PRICE:.2f}")
            except Exception as e:
                print(f"✗ Error placing initial BUY order: {e}")
                entry_order_id = None
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Current price (${current_price:.2f}) is below entry (${ENTRY_PRICE:.2f})")
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Waiting for price to cross above ${ENTRY_PRICE:.2f} before placing order...")
            entry_order_id = None
    except Exception as e:
        print(f"✗ Error checking current price: {e}")
        entry_order_id = None

print(f"[{datetime.now().strftime('%H:%M:%S')}] Stop loss set at ${STOP_PRICE:.2f}")
print(f"(Stop loss will be monitored and executed automatically)")

print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Monitoring position... Press Ctrl+C to exit\n")

# 3. Continuous monitoring loop
try:
    while True:
        try:
            # Get current price
            current_price = _get_current_price(SYMBOL)
            if DEBUG_TIMING:
                print(f"[HEARTBEAT] {SYMBOL} current=${current_price:.2f} entry=${ENTRY_PRICE:.2f}", flush=True)

            crossed_above_entry = (
                (REFRESH_OPTION_MIDPOINT and last_observed_price is None)
                or (last_observed_price is None and current_price >= ENTRY_PRICE)
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
                    # Only place limit order if current price is at or above the limit price
                    # This ensures the order only fills when price reaches the limit
                    if awaiting_reentry_after_stop and crossed_above_entry and current_price >= ENTRY_PRICE:
                        prev_price_text = "startup" if last_observed_price is None else f"${last_observed_price:.2f}"
                        print(
                            f"[{datetime.now().strftime('%H:%M:%S')}] Re-entry trigger: "
                            f"price crossed above entry ({prev_price_text} -> ${current_price:.2f}). "
                            f"Submitting LIMIT BUY for {TOTAL_QTY} {SYMBOL} at ${ENTRY_PRICE:.2f}..."
                        )
                        try:
                            entry_order_id, submitted_price = _place_entry_order()
                            first_contract_sold = False
                            breakeven_stop_set = False
                            second_contract_stop_loss = None
                            awaiting_reentry_after_stop = False
                            trailing_stop_active = False
                            highest_price_since_target1 = None
                            print(f"✓ Re-entry BUY submitted at midpoint ${submitted_price:.2f}. Order ID: {entry_order_id}")
                        except Exception as e:
                            print(f"✗ Error submitting re-entry BUY: {e}")
                    elif not awaiting_reentry_after_stop and crossed_above_entry and current_price >= ENTRY_PRICE:
                        prev_price_text = "startup" if last_observed_price is None else f"${last_observed_price:.2f}"
                        print(
                            f"[{datetime.now().strftime('%H:%M:%S')}] Initial entry trigger: "
                            f"price crossed entry ({prev_price_text} -> ${current_price:.2f}). "
                            f"Submitting LIMIT BUY for {TOTAL_QTY} {SYMBOL} at ${ENTRY_PRICE:.2f}..."
                        )
                        try:
                            entry_order_id, submitted_price = _place_entry_order()
                            first_contract_sold = False
                            breakeven_stop_set = False
                            second_contract_stop_loss = None
                            trailing_stop_active = False
                            highest_price_since_target1 = None
                            print(f"✓ Entry BUY submitted at midpoint ${submitted_price:.2f}. Order ID: {entry_order_id}")
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
            
            # Determine the active stop (initial stop loss or breakeven)
            active_stop = second_contract_stop_loss if (first_contract_sold and breakeven_stop_set) else current_stop_loss
            
            # Track highest price since target1 for trailing stop
            if first_contract_sold and trailing_stop_active:
                if highest_price_since_target1 is None or current_price > highest_price_since_target1:
                    highest_price_since_target1 = current_price
                trailing_stop_level = highest_price_since_target1 - TRAILING_STOP_AMOUNT
            else:
                trailing_stop_level = None
            
            # Print status
            if trailing_stop_active and trailing_stop_level is not None:
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] Price: ${current_price:.2f} | "
                    f"Position: {current_qty} | High: ${highest_price_since_target1:.2f} | "
                    f"Trail Stop: ${trailing_stop_level:.2f} | P&L: ${unrealized_pnl:.2f}",
                    end=""
                )
            else:
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
                        time_in_force=ORDER_TIME_IN_FORCE
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
                    trailing_stop_active = False
                    highest_price_since_target1 = None
                    last_observed_price = current_price
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Ready for next trade...\n")
                    if SINGLE_ENTRY_MODE:
                        print("[INFO] Single-entry lifecycle complete after stop; returning control to parent.")
                        break
                except Exception as e:
                    print(f"✗ Error executing stop loss: {e}")
            
            # Stop loss for remaining contract after first sale (at breakeven)
            elif first_contract_sold and breakeven_stop_set and not trailing_stop_active and current_price <= second_contract_stop_loss:
                print(f"\n✗ BREAKEVEN STOP HIT at ${current_price:.2f}! Closing remaining {current_qty} {SYMBOL}...")
                try:
                    close_order = MarketOrderRequest(
                        symbol=SYMBOL,
                        qty=current_qty,
                        side=OrderSide.SELL,
                        time_in_force=ORDER_TIME_IN_FORCE
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
                    trailing_stop_active = False
                    highest_price_since_target1 = None
                    last_observed_price = current_price
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Ready for next trade...\n")
                    if SINGLE_ENTRY_MODE:
                        print("[INFO] Single-entry lifecycle complete after breakeven stop; returning control to parent.")
                        break
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
                        time_in_force=ORDER_TIME_IN_FORCE
                    )
                    sell_response = trading_client.submit_order(order_data=sell_order)
                    print(f"✓ Sold first contract. Order ID: {sell_response.id}")
                    first_contract_sold = True
                    
                    # Set breakeven stop loss for remaining contract
                    second_contract_stop_loss = ENTRY_PRICE
                    breakeven_stop_set = True
                    print(f"✓ Breakeven stop loss set at ${ENTRY_PRICE:.2f} for remaining contract")
                    
                    # Activate trailing stop
                    trailing_stop_active = True
                    highest_price_since_target1 = current_price
                    print(f"✓ Trailing stop activated at ${current_price:.2f} (will exit if price drops ${TRAILING_STOP_AMOUNT:.2f})")
                
                except Exception as e:
                    print(f"✗ Error selling first contract: {e}")
            
            # Check if trailing stop is hit
            elif first_contract_sold and trailing_stop_active and current_price <= (highest_price_since_target1 - TRAILING_STOP_AMOUNT):
                print(f"\n✓ TRAILING STOP HIT at ${current_price:.2f} (high: ${highest_price_since_target1:.2f})!")
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Closing remaining position ({current_qty} {SYMBOL})...")
                
                try:
                    close_order = MarketOrderRequest(
                        symbol=SYMBOL,
                        qty=current_qty,
                        side=OrderSide.SELL,
                        time_in_force=ORDER_TIME_IN_FORCE
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
                    trailing_stop_active = False
                    highest_price_since_target1 = None
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Ready for next trade...\n")
                    if SINGLE_ENTRY_MODE:
                        print("[INFO] Single-entry lifecycle complete after trailing stop; returning control to parent.")
                        break
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
