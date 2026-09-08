#!/usr/bin/env python3
"""Spray Trading Strategy: Trade 4 zones around today's open price.

Works with both stocks and crypto symbols.

Uses today's open as a reference and creates 4 buy zones:
- Zone 1: 2% below open
- Zone 2: 1% below open
- Zone 3: 1% above open
- Zone 4: 2% above open

Each zone has:
- Stop loss: 0.25% below entry
- Target 1: 0.25% above entry (half position)
- Target 2: 0.75% above entry (other half)

Only ONE zone is active at a time. When price approaches a zone,
that zone becomes active. Once the trade completes, the next zone activates.

Usage:
    python spray.py SYMBOL QUANTITY
    
Examples (Stocks):
    python spray.py MU 10
    python spray.py SPY 5
    
Examples (Crypto):
    python spray.py BTC/USD 0.1
    python spray.py ETH/USD 1
"""

import os
import re
import sys
import requests
from alpaca.common.exceptions import APIError
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest, CryptoLatestTradeRequest, StockBarsRequest, CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame
from datetime import datetime, timedelta, timezone
import time
from pathlib import Path

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

DEBUG_TIMING = os.getenv("SPRAY_DEBUG_TIMING", "1").strip().lower() not in ("0", "false", "no")

try:
    credentials, trading_client = bootstrap_trading_auth("spray.py")
except Exception as exc:
    print(f"Authentication failed: {exc}")
    raise SystemExit(1)

PAPER = credentials.paper


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
            pos = _timed("get_open_position", trading_client.get_open_position, symbol)
            if pos and _normalize_symbol(getattr(pos, "symbol", "")) == target:
                return pos
        except Exception as e:
            # Fallback: broader scan only when direct lookup fails.
            try:
                positions = _timed("get_all_positions", trading_client.get_all_positions)
                if positions:
                    for pos in positions:
                        if pos and _normalize_symbol(getattr(pos, "symbol", "")) == target:
                            return pos
            except Exception:
                pass
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


def _get_current_price(symbol: str) -> float:
    if IS_CRYPTO:
        price_request = CryptoLatestTradeRequest(symbol_or_symbols=symbol)
        latest_trade = _timed("get_crypto_latest_trade", data_client.get_crypto_latest_trade, price_request)
    else:
        price_request = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
        latest_trade = _timed("get_stock_latest_trade", data_client.get_stock_latest_trade, price_request)
    return float(latest_trade[symbol].price)


def _get_today_open(symbol: str) -> float:
    """Get today's open price from daily bars. Falls back to most recent bar if today's not available."""
    try:
        now = datetime.now(timezone.utc)
        
        if IS_CRYPTO:
            from alpaca.data.requests import CryptoBarsRequest
            request = CryptoBarsRequest(
                symbol_or_symbols=[symbol],
                timeframe=TimeFrame.Day,
                start=now - timedelta(days=10),  # Fetch 10 days to ensure data availability
                end=now,
            )
            response = data_client.get_crypto_bars(request)
        else:
            # Try SIP feed first (more comprehensive), fall back to IEX
            try:
                request = StockBarsRequest(
                    symbol_or_symbols=[symbol],
                    timeframe=TimeFrame.Day,
                    start=now - timedelta(days=10),  # Fetch 10 days to ensure data availability
                    end=now,
                    feed=DataFeed.SIP,
                )
                response = data_client.get_stock_bars(request)
            except Exception:
                # Fall back to IEX feed
                request = StockBarsRequest(
                    symbol_or_symbols=[symbol],
                    timeframe=TimeFrame.Day,
                    start=now - timedelta(days=10),
                    end=now,
                    feed=DataFeed.IEX,
                )
                response = data_client.get_stock_bars(request)
        
        if hasattr(response, 'data') and symbol in response.data:
            bars = response.data[symbol]
            if bars:
                # Get the most recent bar (today's if available, otherwise yesterday's or earlier)
                return float(bars[-1].open)
        
        print(f"Debug: No bars found for {symbol}. Response type: {type(response)}")
    except Exception as e:
        print(f"Debug: Error fetching open price: {e}")
    return None


def _normalize_position_qty(raw_qty: float) -> float | int:
    if IS_CRYPTO:
        return abs(float(raw_qty))
    return max(int(round(abs(float(raw_qty)))), 0)


def _find_call_option_for_stock(symbol: str) -> str | None:
    """Find any open call option position for the given stock symbol."""
    try:
        positions = _timed("get_all_positions", trading_client.get_all_positions)
        if not positions:
            return None
        
        for pos in positions:
            pos_symbol = str(getattr(pos, "symbol", "")).upper()
            if not pos_symbol:
                continue
            
            # Look for call options matching the pattern: STOCK + date + C + strike
            # Example: MU260917C140000
            if symbol in pos_symbol and "C" in pos_symbol and pos_symbol.endswith("0"):
                # Verify it's a call option (has C before the strike)
                if re.search(rf"{re.escape(symbol)}\d{{6}}C\d+", pos_symbol):
                    qty = abs(float(getattr(pos, "qty", 0)))
                    if qty > 0:
                        return pos_symbol
    except Exception as e:
        if DEBUG_TIMING:
            print(f"[DEBUG] Error finding call option: {e}", flush=True)
    
    return None


def _sell_one_call_option(option_symbol: str) -> bool:
    """Sell 1 call option at market price."""
    try:
        sell_order = MarketOrderRequest(
            symbol=option_symbol,
            qty=1,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        response = trading_client.submit_order(order_data=sell_order)
        print(f"  ✓ Sold 1 call option {option_symbol}. Order ID: {response.id}")
        return True
    except Exception as e:
        print(f"  ✗ Error selling call option: {e}")
        return False


def _cancel_all_orders_for_symbol(symbol: str) -> int:
    """Cancel all open orders for a symbol. Returns count of cancelled orders."""
    try:
        orders = _get_open_orders_for_symbol(symbol)
        cancelled_count = 0
        for order in orders:
            try:
                trading_client.cancel_order_by_id(order.id)
                cancelled_count += 1
            except Exception as e:
                if DEBUG_TIMING:
                    print(f"[DEBUG] Could not cancel order {order.id}: {e}", flush=True)
        return cancelled_count
    except Exception as e:
        if DEBUG_TIMING:
            print(f"[DEBUG] Error cancelling orders: {e}", flush=True)
        return 0


def _round_price(price: float) -> float:
    """Round price to 2 decimal places."""
    return round(price, 2)


def _print_usage() -> None:
    print("Usage: python spray.py")
    print("Reads configuration from lists/spray.txt")
    print("File format: SYMBOL NUM_STOCKS")
    print("Example line: NVDA 4")


def _read_config_from_file() -> tuple:
    """Read the first active (non-comment) line from lists/spray.txt"""
    config_path = WORKSPACE_ROOT / "lists" / "spray.txt"
    
    if not config_path.exists():
        print(f"Error: Configuration file not found at {config_path}")
        print("Please create lists/spray.txt with content like:")
        print("NVDA 4")
        sys.exit(1)
    
    try:
        with open(config_path, 'r') as f:
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if not line or line.startswith('#'):
                    continue
                
                parts = line.split()
                if len(parts) < 2:
                    print(f"Error: Invalid configuration line: {line}")
                    print("Expected format: SYMBOL NUM_STOCKS")
                    sys.exit(1)
                
                symbol = parts[0].upper()
                try:
                    num_stocks = int(parts[1])
                except ValueError as e:
                    print(f"Error: Invalid value in configuration line: {line}")
                    print(f"Details: {e}")
                    sys.exit(1)
                
                return symbol, num_stocks
        
        print("Error: No valid configuration lines found in lists/spray.txt")
        sys.exit(1)
    except Exception as e:
        print(f"Error reading configuration file: {e}")
        sys.exit(1)


# Parse configuration from file
SYMBOL, NUM_STOCKS = _read_config_from_file()

if NUM_STOCKS <= 0:
    print("Error: NUM_STOCKS must be positive")
    _print_usage()
    sys.exit(1)

if NUM_STOCKS % 2 != 0:
    print(f"Error: NUM_STOCKS must be even (got {NUM_STOCKS})")
    _print_usage()
    sys.exit(1)

IS_CRYPTO = "/" in SYMBOL
ORDER_TIME_IN_FORCE = TimeInForce.GTC
data_client = (
    CryptoHistoricalDataClient(credentials.api_key, credentials.secret_key)
    if IS_CRYPTO
    else StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
)

# Trading parameters
TOTAL_QTY = float(NUM_STOCKS)
FIRST_TARGET_QTY = NUM_STOCKS // 2
SECOND_TARGET_QTY = NUM_STOCKS - FIRST_TARGET_QTY
CHECK_INTERVAL = 10
FLAT_CHECK_INTERVAL = 10

# Fetch today's open
print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching today's open for {SYMBOL}...")
TODAY_OPEN = _get_today_open(SYMBOL)
if TODAY_OPEN is None:
    print(f"✗ Could not fetch today's open price for {SYMBOL}")
    sys.exit(1)

print(f"✓ Today's open: ${TODAY_OPEN:.2f}")

# Define the 4 zones
# Each zone: (name, percentage_from_open, buy_price)
ZONES = [
    ("Zone 1 (1% Below)", -0.01, _round_price(TODAY_OPEN * 0.99)),
    ("Zone 2 (2% Below)", -0.02, _round_price(TODAY_OPEN * 0.98)),
    ("Zone 3 (1% Above)", 0.01, _round_price(TODAY_OPEN * 1.01)),
    ("Zone 4 (2% Above)", 0.02, _round_price(TODAY_OPEN * 1.02)),
]

# Calculate stops and targets for each zone
# All stops and targets are now relative to TODAY_OPEN (not to buy price)
ZONE_PARAMS = []

# Zone 1: 1% Below
zone1_buy = _round_price(TODAY_OPEN * 0.99)
zone1_stop = _round_price(TODAY_OPEN * 0.9875)   # 1.25% below open
zone1_t1 = _round_price(TODAY_OPEN * 0.9925)     # 0.75% below open
zone1_t2 = _round_price(TODAY_OPEN * 0.9975)     # 0.25% below open
ZONE_PARAMS.append({'name': "Zone 1 (1% Below)", 'buy_price': zone1_buy, 'stop_loss': zone1_stop, 'target1': zone1_t1, 'target2': zone1_t2})

# Zone 2: 2% Below
zone2_buy = _round_price(TODAY_OPEN * 0.98)
zone2_stop = _round_price(TODAY_OPEN * 0.9775)   # 2.25% below open
zone2_t1 = _round_price(TODAY_OPEN * 0.9825)     # 1.75% below open
zone2_t2 = _round_price(TODAY_OPEN * 0.9875)     # 1.25% below open
ZONE_PARAMS.append({'name': "Zone 2 (2% Below)", 'buy_price': zone2_buy, 'stop_loss': zone2_stop, 'target1': zone2_t1, 'target2': zone2_t2})

# Zone 3: 1% Above
zone3_buy = _round_price(TODAY_OPEN * 1.01)
zone3_stop = _round_price(TODAY_OPEN * 1.0075)   # 0.75% above open
zone3_t1 = _round_price(TODAY_OPEN * 1.0125)     # 1.25% above open
zone3_t2 = _round_price(TODAY_OPEN * 1.0175)     # 1.75% above open
ZONE_PARAMS.append({'name': "Zone 3 (1% Above)", 'buy_price': zone3_buy, 'stop_loss': zone3_stop, 'target1': zone3_t1, 'target2': zone3_t2})

# Zone 4: 2% Above
zone4_buy = _round_price(TODAY_OPEN * 1.02)
zone4_stop = _round_price(TODAY_OPEN * 1.0175)   # 1.75% above open
zone4_t1 = _round_price(TODAY_OPEN * 1.0225)     # 2.25% above open
zone4_t2 = _round_price(TODAY_OPEN * 1.0275)     # 2.75% above open
ZONE_PARAMS.append({'name': "Zone 4 (2% Above)", 'buy_price': zone4_buy, 'stop_loss': zone4_stop, 'target1': zone4_t1, 'target2': zone4_t2})

print(f"\n╔════════════════════════════════════════╗")
print(f"║  SPRAY TRADING - {SYMBOL:<25} ║")
print(f"╠════════════════════════════════════════╣")
print(f"║ Today's Open:      ${TODAY_OPEN:.2f}")
print(f"║ Shares per Zone:   {NUM_STOCKS}")
print(f"║ Paper Trading:     {PAPER}")
print(f"╠════════════════════════════════════════╣")

for params in ZONE_PARAMS:
    print(f"║ {params['name']:<28} Buy: ${params['buy_price']:<6.2f}")
    print(f"║   Stop: ${params['stop_loss']:.2f} | T1: ${params['target1']:.2f} | T2: ${params['target2']:.2f}")

print(f"╚════════════════════════════════════════╝\n")

# State tracking
active_zone_idx = None  # Which zone is currently active
entry_order_id = None
first_target_sold = False
current_stop_loss = None
second_target_stop_loss = None
highest_price = None
last_observed_price = None
awaiting_zone_completion = False
waiting_for_price_level = False  # Waiting for price to reach buy level

print(f"[{datetime.now().strftime('%H:%M:%S')}] Monitoring zones... Press Ctrl+C to exit\n")

# Main loop
try:
    while True:
        try:
            current_price = _get_current_price(SYMBOL)
            if DEBUG_TIMING:
                print(f"[HEARTBEAT] {SYMBOL} current=${current_price:.2f}", flush=True)

            # Get current position
            current_position = _find_position_for_symbol(SYMBOL, retries=2, delay_seconds=0.5)

            if current_position is None:
                # No position - check for active buy orders
                symbol_orders = _get_open_orders_for_symbol(SYMBOL)
                buy_orders = [
                    o for o in symbol_orders
                    if (o.side.value if hasattr(o.side, "value") else str(o.side)).upper() == "BUY"
                ]

                if buy_orders:
                    # Determine which zone these orders belong to
                    if active_zone_idx is None:
                        # Find zone by matching order limit price with zone buy price
                        order_price = float(buy_orders[0].limit_price) if hasattr(buy_orders[0], 'limit_price') and buy_orders[0].limit_price else None
                        if order_price:
                            # Find exact matching zone
                            for idx, params in enumerate(ZONE_PARAMS):
                                if abs(params['buy_price'] - order_price) < 0.01:
                                    active_zone_idx = idx
                                    break
                        
                        # If no exact match found, use closest zone
                        if active_zone_idx is None:
                            zone_distances = [(idx, abs(current_price - params['buy_price'])) for idx, params in enumerate(ZONE_PARAMS)]
                            active_zone_idx, _ = min(zone_distances, key=lambda x: x[1])
                    
                    # Re-evaluate if we should switch zones based on current price
                    zone_distances = [
                        (idx, abs(current_price - params['buy_price']))
                        for idx, params in enumerate(ZONE_PARAMS)
                    ]
                    closest_zone_idx, closest_distance = min(zone_distances, key=lambda x: x[1])
                    current_zone_distance = zone_distances[active_zone_idx][1]
                    
                    # Switch zones if price has moved significantly closer to a different zone
                    # (e.g., price moved from Zone 1 up to Zone 3 territory)
                    if closest_zone_idx != active_zone_idx and closest_distance < current_zone_distance * 0.5:
                        # New zone is much closer, switch zones
                        print(
                            f"[{datetime.now().strftime('%H:%M:%S')}] 🔄 ZONE CHANGE DETECTED: "
                            f"Price moved away from {ZONE_PARAMS[active_zone_idx]['name']} "
                            f"(distance: ${current_zone_distance:.2f}) to {ZONE_PARAMS[closest_zone_idx]['name']} "
                            f"(distance: ${closest_distance:.2f})"
                        )
                        cancelled = _cancel_all_orders_for_symbol(SYMBOL)
                        if cancelled > 0:
                            print(f"  ✓ Cancelled {cancelled} order(s)")
                        
                        active_zone_idx = closest_zone_idx
                        first_target_sold = False
                        current_stop_loss = ZONE_PARAMS[active_zone_idx]['stop_loss']
                        second_target_stop_loss = ZONE_PARAMS[active_zone_idx]['stop_loss']
                        highest_price = None
                        waiting_for_price_level = False
                        
                        zone_info = ZONE_PARAMS[active_zone_idx]
                        print(f"  ⚡ Activated {zone_info['name']} | Buy @ ${zone_info['buy_price']:.2f}")
                        
                        # Check if buy price is above current price
                        if zone_info['buy_price'] > current_price:
                            waiting_for_price_level = True
                            print(f"  ⏳ Waiting for price to reach ${zone_info['buy_price']:.2f}...")
                        else:
                            # Place order for new zone
                            try:
                                entry_order = LimitOrderRequest(
                                    symbol=SYMBOL,
                                    qty=TOTAL_QTY,
                                    side=OrderSide.BUY,
                                    time_in_force=ORDER_TIME_IN_FORCE,
                                    limit_price=zone_info['buy_price'],
                                )
                                entry_response = _timed("submit_order(entry)", trading_client.submit_order, order_data=entry_order)
                                entry_order_id = str(entry_response.id)
                                print(f"  ✓ Order placed for {zone_info['name']}. ID: {entry_order_id}")
                            except Exception as e:
                                print(f"  ✗ Error placing order: {e}")
                                active_zone_idx = None
                                entry_order_id = None
                    else:
                        # Still waiting for same zone to fill
                        if active_zone_idx is not None:
                            zone_info = ZONE_PARAMS[active_zone_idx]
                            print(
                                f"[{datetime.now().strftime('%H:%M:%S')}] 🔍 WAITING FOR FILL | {zone_info['name']} | "
                                f"Buy @ ${zone_info['buy_price']:.2f} | Current: ${current_price:.2f} | "
                                f"{len(buy_orders)} order(s) open"
                            )
                else:
                    # Determine which zone to activate
                    # Find the zone closest to current price
                    zone_distances = [
                        (idx, abs(current_price - params['buy_price']))
                        for idx, params in enumerate(ZONE_PARAMS)
                    ]
                    closest_zone_idx, closest_distance = min(zone_distances, key=lambda x: x[1])

                    # Check if we should activate a new zone
                    if active_zone_idx is None or awaiting_zone_completion:
                        # Cancel any existing orders first
                        cancelled = _cancel_all_orders_for_symbol(SYMBOL)
                        if cancelled > 0:
                            print(
                                f"[{datetime.now().strftime('%H:%M:%S')}] ✓ Cancelled {cancelled} order(s) from other zone(s)"
                            )
                        
                        # Activate the closest zone
                        active_zone_idx = closest_zone_idx
                        awaiting_zone_completion = False
                        first_target_sold = False
                        current_stop_loss = ZONE_PARAMS[active_zone_idx]['stop_loss']
                        second_target_stop_loss = ZONE_PARAMS[active_zone_idx]['stop_loss']
                        highest_price = None
                        waiting_for_price_level = False

                        zone_info = ZONE_PARAMS[active_zone_idx]
                        print(
                            f"[{datetime.now().strftime('%H:%M:%S')}] ⚡ ZONE ACTIVATED: {zone_info['name']} (Current Price: ${current_price:.2f})"
                        )
                        print(
                            f"  📍 BUY: {NUM_STOCKS} {SYMBOL} @ Limit ${zone_info['buy_price']:.2f} | Stop: ${zone_info['stop_loss']:.2f} | T1: ${zone_info['target1']:.2f} | T2: ${zone_info['target2']:.2f}"
                        )

                        # Check if buy price is above current price
                        if zone_info['buy_price'] > current_price:
                            waiting_for_price_level = True
                            print(f"  ⏳ Waiting for price to reach ${zone_info['buy_price']:.2f}...")
                        else:
                            # Place limit order immediately
                            try:
                                entry_order = LimitOrderRequest(
                                    symbol=SYMBOL,
                                    qty=TOTAL_QTY,
                                    side=OrderSide.BUY,
                                    time_in_force=ORDER_TIME_IN_FORCE,
                                    limit_price=zone_info['buy_price'],
                                )
                                entry_response = _timed("submit_order(entry)", trading_client.submit_order, order_data=entry_order)
                                entry_order_id = str(entry_response.id)
                                print(f"  ✓ Order submitted. ID: {entry_order_id} | Waiting for fill...")
                            except Exception as e:
                                print(f"  ✗ Error placing order: {e}")
                                active_zone_idx = None
                                entry_order_id = None
                    
                    # If waiting for price to reach level
                    elif waiting_for_price_level and active_zone_idx is not None:
                        zone_info = ZONE_PARAMS[active_zone_idx]
                        if current_price >= zone_info['buy_price']:
                            # Price reached! Place the order
                            waiting_for_price_level = False
                            print(
                                f"[{datetime.now().strftime('%H:%M:%S')}] ✓ Price reached ${zone_info['buy_price']:.2f}! Placing order..."
                            )
                            try:
                                entry_order = LimitOrderRequest(
                                    symbol=SYMBOL,
                                    qty=TOTAL_QTY,
                                    side=OrderSide.BUY,
                                    time_in_force=ORDER_TIME_IN_FORCE,
                                    limit_price=zone_info['buy_price'],
                                )
                                entry_response = _timed("submit_order(entry)", trading_client.submit_order, order_data=entry_order)
                                entry_order_id = str(entry_response.id)
                                print(f"  ✓ Order submitted. ID: {entry_order_id} | Waiting for fill...")
                            except Exception as e:
                                print(f"  ✗ Error placing order: {e}")
                                active_zone_idx = None
                                entry_order_id = None
                        else:
                            # Still waiting
                            print(
                                f"[{datetime.now().strftime('%H:%M:%S')}] ⏳ Waiting for level | {zone_info['name']} | "
                                f"Target: ${zone_info['buy_price']:.2f} | Current: ${current_price:.2f}"
                            )

                last_observed_price = current_price
                time.sleep(FLAT_CHECK_INTERVAL)
                continue

            # Position exists - manage the trade
            current_qty = _normalize_position_qty(float(current_position.qty))
            unrealized_pnl = float(current_position.unrealized_pl)

            if current_qty == 0:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Position qty is 0, waiting...")
                time.sleep(CHECK_INTERVAL)
                continue

            zone_info = ZONE_PARAMS[active_zone_idx]
            
            # Track highest price for stop loss management
            if highest_price is None or current_price > highest_price:
                highest_price = current_price

            # Print status
            print(
                f"[{datetime.now().strftime('%H:%M:%S')}] {zone_info['name']:<20} | "
                f"Price: ${current_price:.2f} | Qty: {current_qty} | "
                f"Stop: ${current_stop_loss:.2f} | T1: ${zone_info['target1']:.2f} | "
                f"T2: ${zone_info['target2']:.2f} | P&L: ${unrealized_pnl:.2f}",
                end=""
            )

            # CHECK STOP LOSS
            if not first_target_sold and current_price <= current_stop_loss:
                print(f"\n✗ STOP LOSS at ${current_price:.2f}! Closing all {current_qty}...")
                try:
                    close_order = MarketOrderRequest(
                        symbol=SYMBOL,
                        qty=current_qty,
                        side=OrderSide.SELL,
                        time_in_force=ORDER_TIME_IN_FORCE
                    )
                    close_response = trading_client.submit_order(order_data=close_order)
                    print(f"✓ Closed. Order ID: {close_response.id}")
                    print(f"  Loss: ${unrealized_pnl:.2f}\n")
                    
                    # Reset for next zone
                    active_zone_idx = None
                    first_target_sold = False
                    current_stop_loss = None
                    highest_price = None
                    waiting_for_price_level = False
                    awaiting_zone_completion = True
                except Exception as e:
                    print(f"✗ Error: {e}")

            # CHECK FIRST TARGET (50% of position)
            elif not first_target_sold and current_price >= zone_info['target1']:
                print(f"\n✓ TARGET 1 at ${current_price:.2f}! Selling {FIRST_TARGET_QTY}...")
                try:
                    sell_order = MarketOrderRequest(
                        symbol=SYMBOL,
                        qty=FIRST_TARGET_QTY,
                        side=OrderSide.SELL,
                        time_in_force=ORDER_TIME_IN_FORCE
                    )
                    sell_response = trading_client.submit_order(order_data=sell_order)
                    print(f"✓ Sold. Order ID: {sell_response.id}")
                    
                    # Try to sell 1 call option if it exists
                    call_option = _find_call_option_for_stock(SYMBOL)
                    if call_option:
                        print(f"  Found call option: {call_option}")
                        _sell_one_call_option(call_option)
                    
                    first_target_sold = True
                    second_target_stop_loss = zone_info['buy_price']  # Breakeven
                    print(f"✓ Breakeven stop set at ${second_target_stop_loss:.2f}")
                except Exception as e:
                    print(f"✗ Error: {e}")

            # CHECK BREAKEVEN STOP (after first target sold)
            elif first_target_sold and current_price <= second_target_stop_loss:
                print(f"\n✗ BREAKEVEN STOP at ${current_price:.2f}! Closing remaining {current_qty}...")
                try:
                    close_order = MarketOrderRequest(
                        symbol=SYMBOL,
                        qty=current_qty,
                        side=OrderSide.SELL,
                        time_in_force=ORDER_TIME_IN_FORCE
                    )
                    close_response = trading_client.submit_order(order_data=close_order)
                    print(f"✓ Closed. Order ID: {close_response.id}")
                    print(f"  Final P&L: ${unrealized_pnl:.2f}\n")
                    
                    # Reset for next zone
                    active_zone_idx = None
                    first_target_sold = False
                    current_stop_loss = None
                    highest_price = None
                    waiting_for_price_level = False
                    awaiting_zone_completion = True
                except Exception as e:
                    print(f"✗ Error: {e}")

            # CHECK SECOND TARGET (remaining 50%)
            elif first_target_sold and current_price >= zone_info['target2']:
                print(f"\n✓ TARGET 2 at ${current_price:.2f}! Closing remaining {current_qty}...")
                try:
                    close_order = MarketOrderRequest(
                        symbol=SYMBOL,
                        qty=current_qty,
                        side=OrderSide.SELL,
                        time_in_force=ORDER_TIME_IN_FORCE
                    )
                    close_response = trading_client.submit_order(order_data=close_order)
                    print(f"✓ Closed. Order ID: {close_response.id}")
                    print(f"  Final P&L: ${unrealized_pnl:.2f}\n")
                    
                    # Reset for next zone
                    active_zone_idx = None
                    first_target_sold = False
                    current_stop_loss = None
                    highest_price = None
                    awaiting_zone_completion = True
                except Exception as e:
                    print(f"✗ Error: {e}")

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
