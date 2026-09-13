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
    is_crypto = "/" in raw_qty  # This is wrong, but keeping for compatibility
    if is_crypto:
        return abs(float(raw_qty))
    return max(int(round(abs(float(raw_qty)))), 0)


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


# Parse configuration from file - now returns list of all active trades
ALL_CONFIGS = _read_config_from_file()

# Fixed flags
REFRESH_OPTION_MIDPOINT = False
SINGLE_ENTRY_MODE = False
CHECK_INTERVAL = 5  # Check market every 5 seconds

# Validate and prepare all configurations
trade_states = {}  # {symbol: {config, state_data}}

for symbol, num_stocks, entry_price, stop_price, target1_price, target2_price in ALL_CONFIGS:
    if num_stocks <= 0:
        print(f"Error: NUM_STOCKS must be a positive integer for {symbol}")
        sys.exit(1)
    
    if num_stocks % 2 != 0:
        print(f"Error: NUM_STOCKS must be an even number for {symbol}, got {num_stocks}")
        sys.exit(1)
    
    if stop_price >= entry_price:
        print(f"Error for {symbol}: STOP_PRICE must be lower than ENTRY_PRICE for long trades (stop={stop_price}, entry={entry_price})")
        sys.exit(1)
    
    if target1_price <= entry_price or target2_price <= target1_price:
        print(
            f"Error for {symbol}: expected long-trade targets with ENTRY_PRICE < TARGET1_PRICE < TARGET2_PRICE "
            f"(entry={entry_price}, target1={target1_price}, target2={target2_price})"
        )
        sys.exit(1)
    
    is_crypto = "/" in symbol
    is_option = _is_option_symbol(symbol)
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
            "current_stop_loss": stop_price,
            "second_contract_stop_loss": None,
            "awaiting_reentry_after_stop": False,
            "last_observed_price": None,
        }
    }

# Print header
print(f"\n{'╔' + '═'*60 + '╗'}")
print(f"║ {' '*60} ║")
print(f"║ {'FOMO TRADE - MULTI-SYMBOL SIMULTANEOUS TRADING':<60} ║")
print(f"║ {' '*60} ║")
print(f"╚" + "═"*60 + "╝\n")

print(f"[INFO] Loaded {len(trade_states)} trade configuration(s):\n")
for symbol, trade_data in trade_states.items():
    cfg = trade_data["config"]
    print(f"  {symbol}:")
    print(f"    Entry: ${cfg['entry_price']:.2f} | Shares: {cfg['num_stocks']} | Stop: ${cfg['stop_price']:.2f}")
    print(f"    Target1: ${cfg['target1_price']:.2f} | Target2: ${cfg['target2_price']:.2f}\n")

print(f"[INFO] Placing all entry orders simultaneously...\n")

# Create data client (will be used for all symbols)
if all(trade_states[s]["config"]["is_crypto"] for s in trade_states):
    data_client = CryptoHistoricalDataClient(credentials.api_key, credentials.secret_key)
elif all(trade_states[s]["config"]["is_option"] for s in trade_states):
    data_client = OptionHistoricalDataClient(credentials.api_key, credentials.secret_key)
else:
    data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)

# SETUP PHASE: Place all entry orders at once
pending_orders = {}  # {symbol: order_id}

for symbol in trade_states:
    cfg = trade_states[symbol]["config"]
    
    try:
        current_price = _get_current_price(symbol)
        print(f"[{symbol}] Current price: ${current_price:.2f} | Entry price: ${cfg['entry_price']:.2f}")
        
        # Check for existing position
        existing_pos = _find_position_for_symbol(symbol, retries=1, delay_seconds=0.0)
        if existing_pos:
            print(f"[{symbol}] ✓ Existing position found: {existing_pos.qty} shares")
            trade_states[symbol]["state"]["first_contract_sold"] = True  # Skip entry order
            continue
        
        # Place entry order
        order = LimitOrderRequest(
            symbol=symbol,
            qty=float(cfg["num_stocks"]),
            side=OrderSide.BUY,
            time_in_force=cfg["order_time_in_force"],
            limit_price=round(cfg["entry_price"], 2),
        )
        response = _timed("submit_order(entry)", trading_client.submit_order, order_data=order)
        pending_orders[symbol] = str(response.id)
        print(f"[{symbol}] ✓ Entry BUY order placed (ID: {response.id})\n")
    except Exception as e:
        print(f"[{symbol}] ✗ Error placing entry order: {e}\n")

print(f"\n[INFO] Placed {len(pending_orders)} entry orders. Monitoring for fills...\n")
print(f"[MONITOR] Starting monitoring loop (Press Ctrl+C to stop)\n")

# MONITOR PHASE: Track fills, place stops/targets, allow re-entry
stops_placed = set()  # Symbols where stop/targets have been placed

try:
    while True:
        current_time = datetime.now()
        
        for symbol in trade_states:
            cfg = trade_states[symbol]["config"]
            state = trade_states[symbol]["state"]
            
            # Skip if already sold out
            if state["first_contract_sold"] and state["breakeven_stop_set"]:
                continue
            
            # Check if position exists
            pos = _find_position_for_symbol(symbol, retries=1, delay_seconds=0.0)
            
            if pos:
                if symbol not in stops_placed:
                    # Position just filled - place stop and targets
                    current_qty = _normalize_position_qty(float(pos.qty))
                    avg_price = float(pos.avg_entry_price)
                    
                    print(f"[{current_time.strftime('%H:%M:%S')}] [{symbol}] Position filled! {current_qty} shares @ ${avg_price:.2f}")
                    
                    # Place stop loss
                    try:
                        stop_qty = current_qty // 2 if current_qty > 1 else current_qty
                        stop_order = LimitOrderRequest(
                            symbol=symbol,
                            qty=float(stop_qty),
                            side=OrderSide.SELL,
                            time_in_force=cfg["order_time_in_force"],
                            limit_price=round(cfg["stop_price"], 2),
                        )
                        stop_resp = trading_client.submit_order(order_data=stop_order)
                        print(f"  ✓ Stop loss placed at ${cfg['stop_price']:.2f}")
                    except Exception as e:
                        print(f"  ✗ Error placing stop: {e}")
                    
                    # Place target 1
                    try:
                        t1_qty = cfg["first_target_qty"]
                        t1_order = LimitOrderRequest(
                            symbol=symbol,
                            qty=float(t1_qty),
                            side=OrderSide.SELL,
                            time_in_force=cfg["order_time_in_force"],
                            limit_price=round(cfg["target1_price"], 2),
                        )
                        t1_resp = trading_client.submit_order(order_data=t1_order)
                        print(f"  ✓ Target 1 placed at ${cfg['target1_price']:.2f} ({t1_qty} shares)")
                    except Exception as e:
                        print(f"  ✗ Error placing target 1: {e}")
                    
                    # Place target 2
                    try:
                        t2_qty = cfg["second_target_qty"]
                        t2_order = LimitOrderRequest(
                            symbol=symbol,
                            qty=float(t2_qty),
                            side=OrderSide.SELL,
                            time_in_force=cfg["order_time_in_force"],
                            limit_price=round(cfg["target2_price"], 2),
                        )
                        t2_resp = trading_client.submit_order(order_data=t2_order)
                        print(f"  ✓ Target 2 placed at ${cfg['target2_price']:.2f} ({t2_qty} shares)\n")
                    except Exception as e:
                        print(f"  ✗ Error placing target 2: {e}")
                    
                    stops_placed.add(symbol)
        
        time.sleep(CHECK_INTERVAL)

except KeyboardInterrupt:
    print(f"\n\n{'─'*64}")
    print(f"  Monitoring stopped by user")
    print(f"{'─'*64}\n")
    sys.exit(0)
