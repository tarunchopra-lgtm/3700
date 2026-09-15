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
from datetime import datetime, time as dt_time
import json

from pathlib import Path
import sys

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth

# Position tracking directory
POSITIONS_DIR = WORKSPACE_ROOT / "positions"
POSITIONS_DIR.mkdir(exist_ok=True)

REQUEST_TIMEOUT_SECONDS = float(os.getenv("ALPACA_HTTP_TIMEOUT", "15"))


# ============================================================================
# POSITION TRACKING & MANAGEMENT FUNCTIONS
# ============================================================================

def save_position_file(symbol: str, entry_price: float, stop_loss: float, target1: float, 
                       target2: float, adjusted_stop_loss: float = None, 
                       first_target_filled: bool = False, qty: float = None) -> None:
    """
    Save position status to a tracking file.
    This allows positions to persist across program restarts.
    
    Args:
        symbol: Trading symbol
        entry_price: Entry price of position
        stop_loss: Original stop loss price
        target1: First target price
        target2: Second target price
        adjusted_stop_loss: Adjusted stop loss (after target 1 hit)
        first_target_filled: Whether target 1 was filled
        qty: Current position quantity
    """
    position_file = POSITIONS_DIR / f"{symbol}.txt"
    
    position_data = {
        "symbol": symbol,
        "entry_price": entry_price,
        "original_stop_loss": stop_loss,
        "adjusted_stop_loss": adjusted_stop_loss or stop_loss,
        "target1": target1,
        "target2": target2,
        "first_target_filled": first_target_filled,
        "qty": qty,
        "last_updated": datetime.now().isoformat(),
    }
    
    try:
        with open(position_file, 'w') as f:
            json.dump(position_data, f, indent=2)
    except Exception as e:
        print(f"[ERROR] Could not save position file for {symbol}: {e}")


def load_position_file(symbol: str) -> dict | None:
    """
    Load position status from tracking file.
    
    Args:
        symbol: Trading symbol
    
    Returns:
        Position data dict or None if file doesn't exist
    """
    position_file = POSITIONS_DIR / f"{symbol}.txt"
    
    if not position_file.exists():
        return None
    
    try:
        with open(position_file, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] Could not load position file for {symbol}: {e}")
        return None


def remove_position_file(symbol: str) -> bool:
    """
    Remove position tracking file (called when position is closed).
    
    Args:
        symbol: Trading symbol
    
    Returns:
        True if file was removed, False otherwise
    """
    position_file = POSITIONS_DIR / f"{symbol}.txt"
    
    try:
        if position_file.exists():
            position_file.unlink()
            print(f"[CLEANUP] Removed position file for {symbol}")
            return True
        return False
    except Exception as e:
        print(f"[ERROR] Could not remove position file for {symbol}: {e}")
        return False


def calculate_adjusted_stop_loss(entry_price: float, original_stop_loss: float) -> float:
    """
    Calculate adjusted stop loss: 1/2 the distance between entry and original stop loss.
    
    Example: Entry=100, Original Stop=90, Distance=10 → Adjusted Stop=95
    
    Args:
        entry_price: Entry price
        original_stop_loss: Original stop loss price
    
    Returns:
        Adjusted stop loss price
    """
    distance = entry_price - original_stop_loss
    adjusted = entry_price - (distance / 2)
    return adjusted


def check_gap_open(symbol: str, entry_price: float, stop_loss: float, data_client=None) -> str | None:
    """
    Check if market opened with a gap relative to entry/stop.
    
    Returns:
        'gap_down' if opened below stop loss (exit with market order)
        'gap_up_above_entry' if opened above original entry (market order exit)
        None if no gap condition met
    """
    try:
        current_price = _get_current_price(symbol)
        
        # Gap down - opened below stop loss
        if current_price < stop_loss:
            return 'gap_down'
        
        # Gap up above entry
        if current_price > entry_price:
            return 'gap_up_above_entry'
        
        return None
    except Exception as e:
        print(f"[ERROR] Could not check gap open for {symbol}: {e}")
        return None


def is_trading_allowed(symbol: str, cfg: dict) -> bool:
    """
    Check if trading is allowed for a symbol based on time and asset type.
    
    Rules:
    - Stocks: Only trade between 6:30 AM and 12:40 PM ET
    - Crypto: Trade anytime (24/7)
    - Options: Only trade between 6:30 AM and 12:40 PM ET (like stocks)
    
    Returns:
        True if trading is allowed, False otherwise
    """
    current_time = datetime.now().time()
    trading_start = dt_time(6, 30)   # 6:30 AM
    trading_end = dt_time(12, 40)    # 12:40 PM
    
    # Crypto trades anytime
    if cfg.get("is_crypto", False):
        return True
    
    # Stocks and options: only during specified hours
    return trading_start <= current_time <= trading_end



def _install_http_timeout() -> None:
    original_request = requests.sessions.Session.request

    def _request_with_timeout(self, method, url, **kwargs):
        kwargs.setdefault("timeout", REQUEST_TIMEOUT_SECONDS)
        return original_request(self, method, url, **kwargs)

    requests.sessions.Session.request = _request_with_timeout


_install_http_timeout()

DEBUG_TIMING = os.getenv("FOMO_DEBUG_TIMING", "1").strip().lower() not in ("0", "false", "no")

# Extended hours and after-hours trading support
EXTENDED_HOURS_ENABLED = os.getenv("FOMO_EXTENDED_HOURS", "1").strip().lower() not in ("0", "false", "no")

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


def _timed(label: str, fn, *args, **kwargs):
    started = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed = time.perf_counter() - started
    if DEBUG_TIMING and elapsed >= 0.5:
        print(f"[TIMING] {label}: {elapsed:.2f}s", flush=True)
    return result


def _create_limit_order(symbol: str, qty: float, side: OrderSide, limit_price: float, 
                       time_in_force: TimeInForce, is_stock: bool = False) -> LimitOrderRequest:
    """
    Create a limit order with extended hours support for stocks.
    
    Args:
        symbol: Trading symbol
        qty: Order quantity
        side: OrderSide.BUY or OrderSide.SELL
        limit_price: Limit price
        time_in_force: TimeInForce enum
        is_stock: Whether this is a stock (for extended hours support)
    
    Returns:
        LimitOrderRequest configured for extended hours if applicable
    """
    order_dict = {
        "symbol": symbol,
        "qty": qty,
        "side": side,
        "time_in_force": time_in_force,
        "limit_price": round(limit_price, 2),
    }
    
    # Add extended hours support for stocks
    if is_stock and EXTENDED_HOURS_ENABLED:
        order_dict["extended_hours"] = True
    
    return LimitOrderRequest(**order_dict)


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


def _get_current_price(symbol: str) -> float:
    is_crypto = "/" in symbol
    is_option = _is_option_symbol(symbol)
    
    if is_crypto:
        price_request = CryptoLatestTradeRequest(symbol_or_symbols=symbol)
        latest_trade = _timed("get_crypto_latest_trade", data_client.get_crypto_latest_trade, price_request)
    elif is_option:
        price_request = OptionLatestTradeRequest(symbol_or_symbols=symbol, feed=OptionsFeed.INDICATIVE)
        latest_trade = _timed("get_option_latest_trade", data_client.get_option_latest_trade, price_request)
    else:
        price_request = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
        latest_trade = _timed("get_stock_latest_trade", data_client.get_stock_latest_trade, price_request)
    return float(latest_trade[symbol].price)


def _normalize_position_qty(raw_qty: float) -> float | int:
    """Normalize position quantity to absolute value, keeping decimals for crypto"""
    # Note: Cannot determine if crypto from quantity alone - that info is in the symbol
    # Crypto positions can have decimals, stocks are integers
    # Since we get here after fetching position, just return the absolute value as-is
    qty_value = abs(float(raw_qty))
    
    # Return as float to preserve decimals for crypto positions
    return qty_value


def _read_config_from_file() -> list[tuple]:
    """Read ALL active (non-comment) lines from lists/fomo_trade.txt"""
    config_path = WORKSPACE_ROOT / "lists" / "fomo_trade.txt"
    
    if not config_path.exists():
        print(f"Error: Configuration file not found at {config_path}")
        print("Please create lists/fomo_trade.txt with content like:")
        print("MU 2 780 770 800 900")
        print("INTC 2 55 50 60 70")
        sys.exit(1)
    
    configs = []
    try:
        with open(config_path, 'r') as f:
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if not line or line.startswith('#'):
                    continue
                
                parts = line.split()
                if len(parts) < 6:
                    print(f"Warning: Skipping invalid configuration line: {line}")
                    print("Expected format: SYMBOL NUM_STOCKS ENTRY_PRICE STOP_PRICE TARGET1_PRICE TARGET2_PRICE")
                    continue
                
                symbol = parts[0].upper()
                try:
                    num_stocks = int(parts[1])
                    entry_price = float(parts[2])
                    stop_price = float(parts[3])
                    target1_price = float(parts[4])
                    target2_price = float(parts[5])
                except ValueError as e:
                    print(f"Warning: Skipping invalid value in configuration line: {line}")
                    print(f"Details: {e}")
                    continue
                
                configs.append((symbol, num_stocks, entry_price, stop_price, target1_price, target2_price))
        
        if not configs:
            print("Error: No valid configuration lines found in lists/fomo_trade.txt")
            sys.exit(1)
        
        return configs
    except Exception as e:
        print(f"Error reading configuration file: {e}")
        sys.exit(1)


# Fixed flags
REFRESH_OPTION_MIDPOINT = False
SINGLE_ENTRY_MODE = False
CHECK_INTERVAL = 10  # Check market AND config every 10 seconds (unified)

# Dynamic trade state management
trade_states = {}  # {symbol: {config, state_data}}
previous_configs = {}  # Track previous config to detect changes


def _validate_and_add_trade(symbol: str, num_stocks: int, entry_price: float, stop_price: float, 
                            target1_price: float, target2_price: float, data_client=None) -> bool:
    """Validate trade config and add to trade_states. Returns True if added successfully."""
    if symbol in trade_states:
        return False  # Already exists
    
    if num_stocks <= 0:
        print(f"[CONFIG] ✗ {symbol}: NUM_STOCKS must be positive")
        return False
    
    if num_stocks % 2 != 0:
        print(f"[CONFIG] ✗ {symbol}: NUM_STOCKS must be even (got {num_stocks})")
        return False
    
    if stop_price >= entry_price:
        print(f"[CONFIG] ✗ {symbol}: STOP must be < ENTRY")
        return False
    
    if target1_price <= entry_price or target2_price <= target1_price:
        print(f"[CONFIG] ✗ {symbol}: Must have ENTRY < TARGET1 < TARGET2")
        return False
    
    is_crypto = "/" in symbol
    is_option = _is_option_symbol(symbol)
    
    # For stocks with extended hours, use GTC (Good-Till-Cancelled) which works across all hours
    # For options, use DAY (they don't support extended hours)
    # For crypto, use GTC
    order_time_in_force = TimeInForce.DAY if is_option else TimeInForce.GTC
    
    trade_states[symbol] = {
        "config": {
            "symbol": symbol,
            "num_stocks": num_stocks,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "target1_price": target1_price,
            "target2_price": target2_price,
            "is_crypto": is_crypto,
            "is_option": is_option,
            "order_time_in_force": order_time_in_force,
            "first_target_qty": num_stocks // 2,
            "second_target_qty": num_stocks - (num_stocks // 2),
        },
        "state": {
            "entry_order_id": None,
            "first_contract_sold": False,
            "breakeven_stop_set": False,
            "target2_placed": False,
            "current_stop_loss": stop_price,
            "adjusted_stop_loss": calculate_adjusted_stop_loss(entry_price, stop_price),
            "second_contract_stop_loss": None,
            "awaiting_reentry_after_stop": False,
            "last_observed_price": None,
        }
    }
    return True


def _remove_trade(symbol: str):
    """Remove a trade from active tracking (it will finish its current orders)."""
    if symbol in trade_states:
        del trade_states[symbol]
        print(f"[CONFIG] ⚠ Removed {symbol} from tracking")
        return True
    return False


def _place_entry_orders_for_symbols(symbols_to_place):
    """Place entry orders for specified symbols."""
    for symbol in symbols_to_place:
        if symbol not in trade_states:
            continue
        
        cfg = trade_states[symbol]["config"]
        state = trade_states[symbol]["state"]
        
        # Check if trading is allowed at this time
        if not is_trading_allowed(symbol, cfg):
            print(f"[{symbol}] ⏸ Trading outside allowed hours, skipping entry order")
            continue
        
        try:
            current_price = _get_current_price(symbol)
            print(f"[{symbol}] Current: ${current_price:.2f} | Entry: ${cfg['entry_price']:.2f}")
            
            # Check for existing position
            existing_pos = _find_position_for_symbol(symbol, retries=1, delay_seconds=0.0)
            if existing_pos:
                print(f"[{symbol}] ✓ Existing position: {existing_pos.qty} shares\n")
                state["first_contract_sold"] = True  # Skip entry
                continue
            
            # Place entry order
            order = _create_limit_order(
                symbol=symbol,
                qty=float(cfg["num_stocks"]),
                side=OrderSide.BUY,
                limit_price=cfg["entry_price"],
                time_in_force=cfg["order_time_in_force"],
                is_stock=not cfg["is_crypto"] and not cfg["is_option"]
            )
            response = _timed("submit_order(entry)", trading_client.submit_order, order_data=order)
            state["entry_order_id"] = str(response.id)
            print(f"[{symbol}] ✓ Entry BUY order placed (ID: {response.id})\n")
        except Exception as e:
            print(f"[{symbol}] ✗ Error placing entry: {e}\n")

    """
    Scan existing positions and open orders, reconcile with fomo_trade.txt.
    Update trade_states to reflect actual account state.
    """
    print("\n[STARTUP] Reconciling with existing positions and orders...\n")
    
    # Get all open orders
    try:
        all_orders = list(trading_client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500)))
    except Exception as e:
        print(f"[STARTUP] ⚠ Could not fetch open orders: {e}")
        all_orders = []
    
    # Get all positions
    try:
        all_positions = _timed("get_all_positions", trading_client.get_all_positions)
    except Exception:
        all_positions = []
    
    # Process each trade in trade_states
    for symbol in list(trade_states.keys()):
        cfg = trade_states[symbol]["config"]
        state = trade_states[symbol]["state"]
        
        # Check for existing position
        existing_pos = _find_position_for_symbol(symbol, retries=1, delay_seconds=0.0)
        
        # Get orders for this symbol
        symbol_orders = _get_open_orders_for_symbol(symbol)
        entry_orders = [o for o in symbol_orders if o.side == OrderSide.BUY]
        stop_orders = [o for o in symbol_orders if o.side == OrderSide.SELL and abs(float(o.limit_price) - cfg["stop_price"]) < 0.01]
        breakeven_orders = [o for o in symbol_orders if o.side == OrderSide.SELL and abs(float(o.limit_price) - cfg["entry_price"]) < 0.01]
        target1_orders = [o for o in symbol_orders if o.side == OrderSide.SELL and abs(float(o.limit_price) - cfg["target1_price"]) < 0.01]
        target2_orders = [o for o in symbol_orders if o.side == OrderSide.SELL and abs(float(o.limit_price) - cfg["target2_price"]) < 0.01]
        
        if existing_pos:
            # Position exists - check what still needs to be set up
            pos_qty = _normalize_position_qty(float(existing_pos.qty))
            print(f"[STARTUP] [{symbol}] Found position: {pos_qty} shares @ ${existing_pos.avg_entry_price:.2f}")
            
            # If we have a full position and targets exist, this is the initial state
            if pos_qty == cfg["num_stocks"] and target1_orders and target2_orders:
                print(f"  ✓ Entry order filled, stops/targets already placed")
                # Determine if Target 1 was already sold
                if stop_orders and not breakeven_orders:
                    state["first_contract_sold"] = False
                    print(f"  → Initial stop/targets in place")
                elif breakeven_orders:
                    state["first_contract_sold"] = True
                    state["breakeven_stop_set"] = True
                    print(f"  → Breakeven stop already set (Target 1 filled)")
            elif pos_qty < cfg["num_stocks"] and breakeven_orders:
                # Position is smaller and has breakeven stop = Target 1 already sold
                state["first_contract_sold"] = True
                state["breakeven_stop_set"] = True
                print(f"  ✓ Target 1 already filled, breakeven stop in place")
            elif pos_qty > 0 and not target1_orders and not target2_orders:
                # Position exists but no targets - need to place them
                print(f"  ⚠ Position exists but no targets - will place stops/targets now")
                # Don't mark stops_placed yet so the monitoring loop will place them
            
        elif entry_orders:
            # Entry order exists but position doesn't fill yet
            print(f"[STARTUP] [{symbol}] Entry order pending (ID: {entry_orders[0].id})")
            print(f"  → Waiting for fill at ${cfg['entry_price']:.2f}")
            state["entry_order_id"] = str(entry_orders[0].id)
        else:
            # No position, no entry order - need to place entry order
            print(f"[STARTUP] [{symbol}] No position or orders - will place entry order")
    
    print()


# Print header
print(f"\n{'╔' + '═'*60 + '╗'}")
print(f"║ {' '*60} ║")
print(f"║ {'FOMO TRADE - MULTI-SYMBOL SIMULTANEOUS TRADING':<60} ║")
print(f"║ {'(Dynamic Config - Hot Reload Enabled)':<60} ║")
if EXTENDED_HOURS_ENABLED:
    print(f"║ {'✓ Extended/After-Hours Trading ENABLED':<60} ║")
else:
    print(f"║ {'Regular Market Hours Only':<60} ║")
print(f"║ {' '*60} ║")
print(f"║ {'📅 TRADING HOURS:':<60} ║")
print(f"║ {'  • Stocks/Options: 6:30 AM - 12:40 PM ET':<60} ║")
print(f"║ {'  • Crypto: 24/7 (anytime)':<60} ║")
print(f"║ {' '*60} ║")
print(f"╚" + "═"*60 + "╝\n")

# Read initial configuration
initial_configs = _read_config_from_file()
for symbol, num_stocks, entry_price, stop_price, target1_price, target2_price in initial_configs:
    _validate_and_add_trade(symbol, num_stocks, entry_price, stop_price, target1_price, target2_price)
    previous_configs[symbol] = (num_stocks, entry_price, stop_price, target1_price, target2_price)

if not trade_states:
    print("Error: No valid trades loaded")
    sys.exit(1)

print(f"[INFO] Loaded {len(trade_states)} initial trade configuration(s):\n")
for symbol, trade_data in trade_states.items():
    cfg = trade_data["config"]
    print(f"  {symbol}:")
    print(f"    Entry: ${cfg['entry_price']:.2f} | Shares: {cfg['num_stocks']} | Stop: ${cfg['stop_price']:.2f}")
    print(f"    Target1: ${cfg['target1_price']:.2f} | Target2: ${cfg['target2_price']:.2f}\n")

print(f"[INFO] Placing entry orders...\n")

# Create data client
if all(trade_states[s]["config"]["is_crypto"] for s in trade_states):
    data_client = CryptoHistoricalDataClient(credentials.api_key, credentials.secret_key)
elif all(trade_states[s]["config"]["is_option"] for s in trade_states):
    data_client = OptionHistoricalDataClient(credentials.api_key, credentials.secret_key)
else:
    data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)

# Determine which symbols need entry orders placed (not already placed or filled)
symbols_needing_entry_orders = []
for symbol in trade_states:
    state = trade_states[symbol]["state"]
    pos = _find_position_for_symbol(symbol, retries=1, delay_seconds=0.0)
    
    if not pos:  # No position - need entry order
        existing_entry_orders = _get_open_orders_for_symbol(symbol)
        existing_entry_orders = [o for o in existing_entry_orders if o.side == OrderSide.BUY]
        
        if not existing_entry_orders:
            # No entry order either - need to place one
            symbols_needing_entry_orders.append(symbol)
        else:
            # Entry order exists, just waiting for fill
            print(f"[STARTUP] [{symbol}] Skipping entry (order already placed, waiting for fill)\n")
    else:
        # Position exists
        print(f"[STARTUP] [{symbol}] Skipping entry (position already filled)\n")

# SETUP PHASE: Place only MISSING entry orders
if symbols_needing_entry_orders:
    print(f"[INFO] Placing {len(symbols_needing_entry_orders)} missing entry orders...\n")
    _place_entry_orders_for_symbols(symbols_needing_entry_orders)
else:
    print(f"[INFO] All entry orders already in place or filled\n")

print(f"[INFO] Monitoring started (unified 10s check for market & config)...\n")
print(f"[INFO] Position files: positions/TICKER.txt (e.g., positions/COHR.txt)\n")

try:
    stops_placed = set()  # Track which symbols have stops/targets placed
    
    # Initialize stops_placed based on existing orders
    print("[STARTUP] Detecting already-placed stops/targets...\n")
    for symbol in trade_states:
        cfg = trade_states[symbol]["config"]
        pos = _find_position_for_symbol(symbol, retries=1, delay_seconds=0.0)
        
        if pos:
            # Position exists, check if stops/targets are placed
            symbol_orders = _get_open_orders_for_symbol(symbol)
            target1_orders = [o for o in symbol_orders if o.side == OrderSide.SELL and abs(float(o.limit_price) - cfg["target1_price"]) < 0.01]
            target2_orders = [o for o in symbol_orders if o.side == OrderSide.SELL and abs(float(o.limit_price) - cfg["target2_price"]) < 0.01]
            stop_orders = [o for o in symbol_orders if o.side == OrderSide.SELL and abs(float(o.limit_price) - cfg["stop_price"]) < 0.01]
            breakeven_orders = [o for o in symbol_orders if o.side == OrderSide.SELL and abs(float(o.limit_price) - cfg["entry_price"]) < 0.01]
            
            if target1_orders and target2_orders:
                # Stops/targets already placed
                stops_placed.add(symbol)
                print(f"  [{symbol}] Already has targets placed")
                
                # Check if breakeven stop is set (Target 1 filled)
                if breakeven_orders:
                    trade_states[symbol]["state"]["first_contract_sold"] = True
                    trade_states[symbol]["state"]["breakeven_stop_set"] = True
                    print(f"  [{symbol}] Breakeven stop detected (Target 1 filled)")
    
    print()
    
    while True:
        current_time = datetime.now()
        
        # ========== UNIFIED 10-SECOND CHECK: MARKET + CONFIG ==========
        print(f"\n[{current_time.strftime('%H:%M:%S')}] 🔍 Market & Config Check ({len(trade_states)} symbols)...")
        
        # CONFIG RELOAD CHECK (every 10 seconds)
        try:
            current_configs = _read_config_from_file()
            current_symbols = {cfg[0] for cfg in current_configs}
            previous_symbols = set(previous_configs.keys())
            
            # Find NEW trades
            new_symbols = current_symbols - previous_symbols
            if new_symbols:
                print(f"\n[CONFIG] 🆕 Found {len(new_symbols)} new trade(s):")
                for symbol, num_stocks, entry_price, stop_price, target1_price, target2_price in current_configs:
                    if symbol in new_symbols:
                        if _validate_and_add_trade(symbol, num_stocks, entry_price, stop_price, target1_price, target2_price):
                            previous_configs[symbol] = (num_stocks, entry_price, stop_price, target1_price, target2_price)
                            cfg = trade_states[symbol]["config"]
                            print(f"  ✓ {symbol}: Entry ${cfg['entry_price']:.2f} | Stop ${cfg['stop_price']:.2f}")
                
                # Place entry orders for new trades
                _place_entry_orders_for_symbols(list(new_symbols))
                print()
            
            # Find REMOVED trades
            removed_symbols = previous_symbols - current_symbols
            if removed_symbols:
                print(f"\n[CONFIG] ❌ Removed {len(removed_symbols)} trade(s):")
                for symbol in removed_symbols:
                    _remove_trade(symbol)
                    del previous_configs[symbol]
                print()
            
            # Find UPDATED trades (same symbol, but config changed)
            common_symbols = current_symbols & previous_symbols
            updated_symbols = []
            
            for symbol, num_stocks, entry_price, stop_price, target1_price, target2_price in current_configs:
                if symbol in common_symbols:
                    prev_config = previous_configs[symbol]
                    # Compare all parameters
                    if (num_stocks, entry_price, stop_price, target1_price, target2_price) != prev_config:
                        updated_symbols.append((symbol, num_stocks, entry_price, stop_price, target1_price, target2_price))
            
            if updated_symbols:
                print(f"\n[CONFIG] 🔄 Found {len(updated_symbols)} updated trade(s):")
                for symbol, num_stocks, entry_price, stop_price, target1_price, target2_price in updated_symbols:
                    cfg = trade_states[symbol]["config"]
                    state = trade_states[symbol]["state"]
                    
                    # Check if position already filled
                    pos = _find_position_for_symbol(symbol, retries=1, delay_seconds=0.0)
                    
                    if pos:
                        # Position already filled - don't modify, just note the change
                        print(f"  ⚠ {symbol}: Position filled, config change ignored")
                    else:
                        # No position yet - check if entry order exists
                        if state["entry_order_id"]:
                            # Cancel old entry order and place new one
                            try:
                                trading_client.cancel_order(state["entry_order_id"])
                                print(f"  ✓ {symbol}: Cancelled old entry order @ ${cfg['entry_price']:.2f}")
                            except Exception as e:
                                print(f"  ✗ {symbol}: Error cancelling old entry order: {e}")
                        
                        # Update config with new values
                        cfg["num_stocks"] = num_stocks
                        cfg["entry_price"] = entry_price
                        cfg["stop_price"] = stop_price
                        cfg["target1_price"] = target1_price
                        cfg["target2_price"] = target2_price
                        cfg["first_target_qty"] = num_stocks // 2
                        cfg["second_target_qty"] = num_stocks - (num_stocks // 2)
                        
                        # Recalculate adjusted stop loss
                        state["adjusted_stop_loss"] = calculate_adjusted_stop_loss(entry_price, stop_price)
                        
                        # Place new entry order
                        try:
                            current_price = _get_current_price(symbol)
                            order = _create_limit_order(
                                symbol=symbol,
                                qty=float(num_stocks),
                                side=OrderSide.BUY,
                                limit_price=entry_price,
                                time_in_force=cfg["order_time_in_force"],
                                is_stock=not cfg["is_crypto"] and not cfg["is_option"]
                            )
                            response = _timed("submit_order(entry)", trading_client.submit_order, order_data=order)
                            state["entry_order_id"] = str(response.id)
                            print(f"  ✓ {symbol}: Placed new entry order @ ${entry_price:.2f} (was ${prev_config[1]:.2f})")
                        except Exception as e:
                            print(f"  ✗ {symbol}: Error placing new entry order: {e}")
                    
                    # Update previous config tracking
                    previous_configs[symbol] = (num_stocks, entry_price, stop_price, target1_price, target2_price)
                
                print()
        except Exception as e:
            pass  # Ignore config read errors, will retry next cycle
        
        # ========== MONITOR ALL ACTIVE TRADES ==========
        for symbol in list(trade_states.keys()):  # Use list() to avoid dict size changes during iteration
            cfg = trade_states[symbol]["config"]
            state = trade_states[symbol]["state"]
            
            # Check if trading is allowed for this symbol at this time
            if not is_trading_allowed(symbol, cfg):
                # Skip trading outside of allowed hours
                continue
            symbol_orders = _get_open_orders_for_symbol(symbol) if symbol in stops_placed or not pos else []
            
            # Show enhanced status: Current Price | Fill Price | Next Order
            if symbol in stops_placed or pos or state["entry_order_id"]:
                try:
                    current_price = _get_current_price(symbol)
                    state["last_observed_price"] = current_price
                except:
                    current_price = state.get("last_observed_price", None)
                
                # Determine fill price and next order
                fill_price = None
                next_order = None
                
                if pos:
                    fill_price = float(pos.avg_entry_price)
                    pos_qty = _normalize_position_qty(float(pos.qty))
                    
                    # Determine next order based on position state
                    stop_orders = [o for o in symbol_orders if o.side == OrderSide.SELL and abs(float(o.limit_price) - cfg["stop_price"]) < 0.01]
                    breakeven_orders = [o for o in symbol_orders if o.side == OrderSide.SELL and abs(float(o.limit_price) - state["adjusted_stop_loss"]) < 0.01]
                    target1_orders = [o for o in symbol_orders if o.side == OrderSide.SELL and abs(float(o.limit_price) - cfg["target1_price"]) < 0.01]
                    target2_orders = [o for o in symbol_orders if o.side == OrderSide.SELL and abs(float(o.limit_price) - cfg["target2_price"]) < 0.01]
                    
                    if stop_orders:
                        next_order = f"Stop: ${cfg['stop_price']:.2f}"
                    elif breakeven_orders:
                        next_order = f"Breakeven: ${state['adjusted_stop_loss']:.2f}"
                        if target2_orders:
                            next_order += f" + T2: ${cfg['target2_price']:.2f}"
                    elif target1_orders:
                        next_order = f"Target 1: ${cfg['target1_price']:.2f}"
                    elif target2_orders:
                        next_order = f"Target 2: ${cfg['target2_price']:.2f}"
                    
                    status_str = f"  📊 {symbol}: Qty={pos_qty:.2f} | Current: ${current_price:.2f} | Fill: ${fill_price:.2f}"
                    if next_order:
                        status_str += f" | Next: {next_order}"
                    print(status_str)
                    
                elif state["entry_order_id"]:
                    fill_price = cfg["entry_price"]
                    next_order = f"Entry Pending: ${fill_price:.2f}"
                    status_str = f"  📊 {symbol}: Current: ${current_price:.2f} | {next_order}"
                    print(status_str)
            
            # PHASE 1: Check for entry fill and place stop/targets
            pos = _find_position_for_symbol(symbol, retries=1, delay_seconds=0.0)
            
            if pos and symbol not in stops_placed:
                current_qty = _normalize_position_qty(float(pos.qty))
                avg_price = float(pos.avg_entry_price)
                state["last_observed_price"] = avg_price
                
                print(f"[{current_time.strftime('%H:%M:%S')}] [{symbol}] Position filled! {current_qty} shares @ ${avg_price:.2f}")
                
                # Save position tracking file
                save_position_file(
                    symbol,
                    entry_price=cfg["entry_price"],
                    stop_loss=cfg["stop_price"],
                    target1=cfg["target1_price"],
                    target2=cfg["target2_price"],
                    adjusted_stop_loss=state["adjusted_stop_loss"],
                    first_target_filled=False,
                    qty=current_qty
                )
                
                # Place stop loss
                try:
                    stop_qty = current_qty // 2 if current_qty > 1 else current_qty
                    stop_order = _create_limit_order(
                        symbol=symbol,
                        qty=float(stop_qty),
                        side=OrderSide.SELL,
                        limit_price=cfg["stop_price"],
                        time_in_force=cfg["order_time_in_force"],
                        is_stock=not cfg["is_crypto"] and not cfg["is_option"]
                    )
                    stop_resp = trading_client.submit_order(order_data=stop_order)
                    state["second_contract_stop_loss"] = str(stop_resp.id)
                    print(f"  ✓ Stop loss placed at ${cfg['stop_price']:.2f}")
                except Exception as e:
                    print(f"  ✗ Error placing stop: {e}")
                
                # Place target 1
                try:
                    t1_qty = cfg["first_target_qty"]
                    t1_order = _create_limit_order(
                        symbol=symbol,
                        qty=float(t1_qty),
                        side=OrderSide.SELL,
                        limit_price=cfg["target1_price"],
                        time_in_force=cfg["order_time_in_force"],
                        is_stock=not cfg["is_crypto"] and not cfg["is_option"]
                    )
                    t1_resp = trading_client.submit_order(order_data=t1_order)
                    print(f"  ✓ Target 1 placed at ${cfg['target1_price']:.2f} ({t1_qty} shares)")
                except Exception as e:
                    print(f"  ✗ Error placing target 1: {e}")
                
                # NOTE: Target 2 will be placed AFTER Target 1 fills to avoid qty conflicts
                print(f"  ℹ️ Target 2 will be placed after Target 1 fills\n")
                
                stops_placed.add(symbol)
            
            # PHASE 2: Check if Target 1 filled, then move stop to 1/2 distance (adjusted stop loss)
            if symbol in stops_placed and not state["first_contract_sold"]:
                if pos:
                    current_qty = _normalize_position_qty(float(pos.qty))
                    
                    if current_qty < cfg["num_stocks"]:
                        state["first_contract_sold"] = True
                        adjusted_stop = state["adjusted_stop_loss"]
                        
                        print(f"[{current_time.strftime('%H:%M:%S')}] [{symbol}] ✓ Target 1 FILLED! Position reduced to {current_qty} shares")
                        print(f"  → Moving stop loss to ${adjusted_stop:.2f} (1/2 distance from entry)")
                        
                        # Update position file
                        save_position_file(
                            symbol,
                            entry_price=cfg["entry_price"],
                            stop_loss=cfg["stop_price"],
                            target1=cfg["target1_price"],
                            target2=cfg["target2_price"],
                            adjusted_stop_loss=adjusted_stop,
                            first_target_filled=True,
                            qty=current_qty
                        )
                        
                        try:
                            open_orders = _get_open_orders_for_symbol(symbol)
                            for order in open_orders:
                                if order.limit_price == cfg["stop_price"] and order.side == OrderSide.SELL:
                                    try:
                                        trading_client.cancel_order(order.id)
                                        print(f"  ✓ Old stop loss (${cfg['stop_price']:.2f}) cancelled")
                                    except Exception as e:
                                        print(f"  ✗ Error cancelling old stop: {e}")
                            
                            new_stop_order = _create_limit_order(
                                symbol=symbol,
                                qty=float(current_qty),
                                side=OrderSide.SELL,
                                limit_price=adjusted_stop,
                                time_in_force=cfg["order_time_in_force"],
                                is_stock=not cfg["is_crypto"] and not cfg["is_option"]
                            )
                            new_stop_resp = trading_client.submit_order(order_data=new_stop_order)
                            state["second_contract_stop_loss"] = str(new_stop_resp.id)
                            state["breakeven_stop_set"] = True
                            state["current_stop_loss"] = adjusted_stop
                            print(f"  ✓ Stop loss moved to adjusted level at ${adjusted_stop:.2f}")
                        except Exception as e:
                            print(f"  ✗ Error moving stop to adjusted level: {e}")
                        
                        # NOW place Target 2 (now that Target 1 has filled)
                        if not state["target2_placed"]:
                            try:
                                t2_qty = cfg["second_target_qty"]
                                t2_order = _create_limit_order(
                                    symbol=symbol,
                                    qty=float(t2_qty),
                                    side=OrderSide.SELL,
                                    limit_price=cfg["target2_price"],
                                    time_in_force=cfg["order_time_in_force"],
                                    is_stock=not cfg["is_crypto"] and not cfg["is_option"]
                                )
                                t2_resp = trading_client.submit_order(order_data=t2_order)
                                state["target2_placed"] = True
                                print(f"  ✓ Target 2 placed at ${cfg['target2_price']:.2f} ({t2_qty} shares)\n")
                            except Exception as e:
                                print(f"  ✗ Error placing target 2: {e}\n")
            
            # PHASE 5: Check if position is fully closed (all targets or stop triggered)
            pos = _find_position_for_symbol(symbol, retries=1, delay_seconds=0.0)
            if symbol in stops_placed and not pos:
                # Position fully closed - check if it was due to targets or stop
                symbol_orders = _get_open_orders_for_symbol(symbol)
                sell_orders = [o for o in symbol_orders if o.side == OrderSide.SELL]
                
                if not sell_orders:
                    # No more sell orders = position fully closed and cleared
                    print(f"[{current_time.strftime('%H:%M:%S')}] [{symbol}] ✓ Position fully CLOSED (all contracts filled or stopped)")
                    stops_placed.discard(symbol)
                    remove_position_file(symbol)
                    
                    # Reset state for potential re-entry
                    state["first_contract_sold"] = False
                    state["breakeven_stop_set"] = False
                    state["target2_placed"] = False
                    state["awaiting_reentry_after_stop"] = False
            
            # PHASE 6: Check for gap opens - exit with market order if conditions met
            if symbol in stops_placed and state["first_contract_sold"] and not state["awaiting_reentry_after_stop"]:
                gap_condition = check_gap_open(symbol, cfg["entry_price"], state["current_stop_loss"])
                
                if gap_condition == 'gap_down':
                    print(f"[{current_time.strftime('%H:%M:%S')}] [{symbol}] ⚠ GAP DOWN detected below stop loss!")
                    print(f"  → Cancelling pending orders and exiting with MARKET ORDER")
                    
                    try:
                        # First: cancel all pending sell orders (stop + target) to free up qty
                        symbol_orders = _get_open_orders_for_symbol(symbol)
                        for order in symbol_orders:
                            if order.side == OrderSide.SELL:
                                try:
                                    trading_client.cancel_order(order.id)
                                    print(f"  ✓ Cancelled pending order @ ${float(order.limit_price):.2f}")
                                except Exception as e:
                                    print(f"  ✗ Error cancelling order: {e}")
                        
                        # Second: place market order to exit position
                        pos = _find_position_for_symbol(symbol, retries=1, delay_seconds=0.0)
                        if pos:
                            current_qty = _normalize_position_qty(float(pos.qty))
                            market_order = MarketOrderRequest(
                                symbol=symbol,
                                qty=float(current_qty),
                                side=OrderSide.SELL,
                                time_in_force=TimeInForce.DAY,
                            )
                            market_resp = trading_client.submit_order(order_data=market_order)
                            print(f"  ✓ Market order submitted (ID: {market_resp.id})")
                            remove_position_file(symbol)
                            state["awaiting_reentry_after_stop"] = True  # Treat as stopped out
                    except Exception as e:
                        print(f"  ✗ Error during gap down exit: {e}")
                
                elif gap_condition == 'gap_up_above_entry':
                    print(f"[{current_time.strftime('%H:%M:%S')}] [{symbol}] ⚠ GAP UP above entry detected!")
                    print(f"  → Cancelling pending orders and exiting with MARKET ORDER")
                    
                    try:
                        # First: cancel all pending sell orders (stop + target) to free up qty
                        symbol_orders = _get_open_orders_for_symbol(symbol)
                        for order in symbol_orders:
                            if order.side == OrderSide.SELL:
                                try:
                                    trading_client.cancel_order(order.id)
                                    print(f"  ✓ Cancelled pending order @ ${float(order.limit_price):.2f}")
                                except Exception as e:
                                    print(f"  ✗ Error cancelling order: {e}")
                        
                        # Second: place market order to exit position
                        pos = _find_position_for_symbol(symbol, retries=1, delay_seconds=0.0)
                        if pos:
                            current_qty = _normalize_position_qty(float(pos.qty))
                            market_order = MarketOrderRequest(
                                symbol=symbol,
                                qty=float(current_qty),
                                side=OrderSide.SELL,
                                time_in_force=TimeInForce.DAY,
                            )
                            market_resp = trading_client.submit_order(order_data=market_order)
                            print(f"  ✓ Market order submitted (ID: {market_resp.id})")
                            remove_position_file(symbol)
                            state["awaiting_reentry_after_stop"] = True  # Treat as stopped out
                    except Exception as e:
                        print(f"  ✗ Error during gap up exit: {e}")

            
            # PHASE 7: Check if stop loss was triggered
            if state["first_contract_sold"] and not state["awaiting_reentry_after_stop"]:
                if not pos:
                    state["awaiting_reentry_after_stop"] = True
                    print(f"[{current_time.strftime('%H:%M:%S')}] [{symbol}] ⚠ Stop loss TRIGGERED! Waiting for re-entry opportunity...")
                    print(f"  Will place re-entry BUY order when price returns to ${cfg['entry_price']:.2f}\n")
            
            # PHASE 8: Check for re-entry opportunity
            if state["awaiting_reentry_after_stop"]:
                try:
                    current_price = _get_current_price(symbol)
                    state["last_observed_price"] = current_price
                    
                    entry_threshold = cfg["entry_price"] * 1.01  # Allow 1% above entry
                    
                    if current_price <= entry_threshold:
                        print(f"[{current_time.strftime('%H:%M:%S')}] [{symbol}] Price ${current_price:.2f} returned to entry (${cfg['entry_price']:.2f})")
                        print(f"  Placing RE-ENTRY order for {cfg['num_stocks']} shares...\n")
                        
                        try:
                            reentry_order = _create_limit_order(
                                symbol=symbol,
                                qty=float(cfg["num_stocks"]),
                                side=OrderSide.BUY,
                                limit_price=cfg["entry_price"],
                                time_in_force=cfg["order_time_in_force"],
                                is_stock=not cfg["is_crypto"] and not cfg["is_option"]
                            )
                            reentry_resp = trading_client.submit_order(order_data=reentry_order)
                            state["awaiting_reentry_after_stop"] = False
                            state["first_contract_sold"] = False
                            state["breakeven_stop_set"] = False
                            state["target2_placed"] = False
                            stops_placed.discard(symbol)
                            print(f"  ✓ Re-entry order placed (ID: {reentry_resp.id})\n")
                        except Exception as e:
                            print(f"  ✗ Error placing re-entry order: {e}")
                except Exception as e:
                    pass  # Ignore price check errors
        
        time.sleep(CHECK_INTERVAL)

except KeyboardInterrupt:
    print(f"\n\n{'─'*64}")
    print(f"  Monitoring stopped by user")
    print(f"{'─'*64}\n")
    sys.exit(0)


# ============================================================================
# HELPER FUNCTIONS FOR EXTERNAL USE (e.g., risk_management.py)
# ============================================================================

def get_all_active_positions() -> list[dict]:
    """
    Get all active positions from tracking files.
    Can be called by risk_management.py or other scripts.
    
    Returns:
        List of position dicts with status
    """
    positions = []
    
    for position_file in POSITIONS_DIR.glob("*.txt"):
        try:
            with open(position_file, 'r') as f:
                position_data = json.load(f)
                positions.append(position_data)
        except Exception as e:
            print(f"[ERROR] Could not read {position_file.name}: {e}")
    
    return positions


def update_stop_loss_for_symbol(symbol: str, new_stop_loss: float) -> bool:
    """
    Update stop loss for a position. Called by risk_management.py if needed.
    
    Args:
        symbol: Trading symbol
        new_stop_loss: New stop loss price
    
    Returns:
        True if update successful, False otherwise
    """
    position_data = load_position_file(symbol)
    
    if not position_data:
        print(f"[ERROR] No position file found for {symbol}")
        return False
    
    position_data["adjusted_stop_loss"] = new_stop_loss
    position_data["last_updated"] = datetime.now().isoformat()
    
    try:
        position_file = POSITIONS_DIR / f"{symbol}.txt"
        with open(position_file, 'w') as f:
            json.dump(position_data, f, indent=2)
        print(f"[UPDATE] Stop loss for {symbol} updated to ${new_stop_loss:.2f}")
        return True
    except Exception as e:
        print(f"[ERROR] Could not update stop loss for {symbol}: {e}")
        return False


def emergency_exit_position(symbol: str, trading_client_instance=None) -> bool:
    """
    Force exit a position with market order.
    Used by risk_management.py for emergency situations.
    
    Args:
        symbol: Trading symbol
        trading_client_instance: Alpaca trading client (uses global if None)
    
    Returns:
        True if exit successful, False otherwise
    """
    client = trading_client_instance or trading_client
    
    try:
        pos = _find_position_for_symbol(symbol, retries=1, delay_seconds=0.0)
        
        if not pos:
            print(f"[ERROR] No open position for {symbol}")
            return False
        
        current_qty = _normalize_position_qty(float(pos.qty))
        
        market_order = MarketOrderRequest(
            symbol=symbol,
            qty=float(current_qty),
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        
        response = client.submit_order(order_data=market_order)
        print(f"[EMERGENCY] Market order submitted for {symbol}: {current_qty} shares (ID: {response.id})")
        remove_position_file(symbol)
        return True
        
    except Exception as e:
        print(f"[ERROR] Could not exit position for {symbol}: {e}")
        return False


def get_position_status(symbol: str) -> dict | None:
    """
    Get status of a specific position.
    
    Args:
        symbol: Trading symbol
    
    Returns:
        Position status dict or None if not found
    """
    return load_position_file(symbol)

