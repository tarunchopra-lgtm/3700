#!/usr/bin/env python3
"""
Risk Management Bot - Automated stop loss and target management for open positions

Monitors open positions and applies risk management rules:
  - Stop loss: customizable percentage below average entry price (default 1%)
  - Target 1:  entry + (entry - stop_loss)  [1:1 risk/reward]
  - After Target 1: stop moves to entry (breakeven)
  - Target 2:  entry + (2 * risk) [2:1 risk/reward]

Usage:
    python risk_management.py                    # All positions: 1% stop, 1% target1, 2% target2
    python risk_management.py --ticker SPY       # Only SPY: 1% stop, 1% target1, 2% target2
    python risk_management.py --ticker SPY --stop-loss 0.5 --target1 0.5 --target2 1.0
    python risk_management.py -h                 # Show this help

Options:
    --ticker SYMBOL      Apply risk management only to this symbol
    --stop-loss PCT      Stop loss as percentage (e.g., 0.5 or .05 = 0.05%)
    --target1 PCT        Target 1 as percentage (moves stop to entry on hit)
    --target2 PCT        Target 2 as percentage (close remaining position)
    -h, --help           Show this help message

Examples:
    python risk_management.py                              # 1% stop, 1% tgt1, 2% tgt2 on all
    python risk_management.py --ticker SPY                 # 1% stop, 1% tgt1, 2% tgt2 on SPY only
    python risk_management.py --stop-loss 2 --target1 2    # 2% stop, 2% tgt1, 2% tgt2 on all

Runs continuously every 30 seconds. Press Ctrl+C to stop.
"""

import argparse
import math
import os
import re
import sys
import time
from datetime import datetime
from alpaca.trading.requests import (
    MarketOrderRequest, LimitOrderRequest, GetOrdersRequest
)
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest, CryptoLatestTradeRequest

from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth

# ── Command-line argument parsing ────────────────────────────────────────────
def parse_arguments():
    """Parse command-line arguments for risk management configuration."""
    parser = argparse.ArgumentParser(
        description="""
RISK_MANAGEMENT.PY - Automated Position Stop Loss and Target Management

Monitors open positions and automatically executes stop loss and profit-taking orders.
Manages position lifecycle: Entry → Stop Loss → Target 1 (partial exit) → Target 2 (full exit).

STRATEGY:
  - Stop loss: Close position if price drops below entry - stop_loss_pct
  - Target 1:  Sell 50% at entry + target1_pct, move stop to breakeven
  - Target 2:  Close remaining 50% at entry + target2_pct
  - Monitors every 30 seconds for price conditions
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES:
  python strategies/risk_management.py
    Default: 1%% stop, 1%% tgt1, 2%% tgt2 on ALL open positions

  python strategies/risk_management.py --ticker SPY
    Manage only SPY: 1%% stop, 1%% tgt1, 2%% tgt2

  python strategies/risk_management.py --stop-loss 2 --target1 2
    Wider stops: 2%% stop, 2%% tgt1, 2%% tgt2 on all positions

  python strategies/risk_management.py --ticker SPY --stop-loss 0.5 --target1 0.75
    Aggressive on SPY: 0.5%% stop, 0.75%% tgt1, 1.5%% tgt2

CONFIGURATION FILES:
  Loads from: lists/fomo_trade.txt
  Format: SYMBOL STOP% TARGET1% TARGET2%
  Example:
    AAPL 1.0 1.0 2.0
    SPY 0.5 1.5 3.0
    MU 2.0 2.0 4.0
  Per-symbol config overrides command-line defaults

LOG FILE:
  Saved to: strategies/risk_management_log.txt
  Records all stop losses, target hits, and exit executions

POSITION MANAGEMENT WORKFLOW:
  1. Monitors price vs. entry price
  2. If price < entry - stop_loss%: Sell entire position (STOP)
  3. If price > entry + target1%: Sell 50% (TARGET1), move stop to entry
  4. If remaining > entry + target2%: Sell remaining 50% (TARGET2)
  5. If target1 hit, trailing stops until target2 or stop hit
  6. Runs continuously every 30 seconds

NOTES:
  - Requires valid Alpaca API credentials and active trading account
  - Runs as background process indefinitely
  - Press Ctrl+C to stop
  - Logs all activity to risk_management_log.txt
  - Per-symbol configuration in lists/fomo_trade.txt takes precedence
  - Percentages can be decimals (0.05 = 0.05%)
  - Works with both stocks and cryptocurrencies
  - Ideal for automated position management during market hours
        """
    )
    parser.add_argument(
        "--ticker",
        type=str,
        default=None,
        help="Apply risk management only to this symbol (e.g., SPY, BTC/USD)"
    )
    parser.add_argument(
        "--stop-loss",
        type=float,
        default=1.0,
        help="Stop loss as percentage (default: 1.0, can be decimal like 0.05)"
    )
    parser.add_argument(
        "--target1",
        type=float,
        default=None,
        help="Target 1 profit as percentage (default: same as stop-loss)"
    )
    parser.add_argument(
        "--target2",
        type=float,
        default=None,
        help="Target 2 profit as percentage (default: 2 * target1)"
    )
    return parser.parse_args()

# Parse arguments before authenticating
args = parse_arguments()

try:
    credentials, trading_client = bootstrap_trading_auth("risk_management.py")
except Exception as exc:
    print(f"Authentication failed: {exc}")
    raise SystemExit(1)

# ── Load fomo_trade configuration ───────────────────────────────────────────
def _load_fomo_trade_config() -> dict:
    """Load trading configuration from lists/fomo_trade.txt"""
    config = {}
    
    fomo_path = WORKSPACE_ROOT / "lists" / "fomo_trade.txt"
    if not fomo_path.exists():
        print(f"Warning: {fomo_path} not found. Risk management will use default percentages.")
        return config
    
    try:
        with open(fomo_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                parts = line.split()
                if len(parts) < 6:
                    print(f"Warning: Skipping invalid line in fomo_trade.txt: {line}")
                    continue
                
                symbol = parts[0].upper()
                try:
                    num_stocks = int(parts[1])
                    entry_price = float(parts[2])
                    stop_price = float(parts[3])
                    target1_price = float(parts[4])
                    target2_price = float(parts[5])
                    
                    config[symbol] = {
                        "num_stocks": num_stocks,
                        "entry_price": entry_price,
                        "stop_price": stop_price,
                        "target1_price": target1_price,
                        "target2_price": target2_price,
                    }
                except ValueError as e:
                    print(f"Warning: Skipping line with invalid values: {line}")
                    continue
    except Exception as e:
        print(f"Warning: Could not read fomo_trade.txt: {e}")
    
    return config


def _load_excluded_symbols() -> set:
    """Load symbols from lists/spray.txt to exclude from risk management."""
    excluded = set()
    
    # Read spray.txt
    spray_path = WORKSPACE_ROOT / "lists" / "spray.txt"
    if spray_path.exists():
        try:
            with open(spray_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    parts = line.split()
                    if parts:
                        symbol = parts[0].upper()
                        excluded.add(symbol)
        except Exception as e:
            print(f"Warning: Could not read spray.txt: {e}")
    
    return excluded


def _is_call_option(symbol: str) -> bool:
    """Check if symbol is a call option (e.g., NVDA250221C00300000)."""
    pattern = re.compile(r"^[A-Z]{1,6}\d{6}[C]\d{8}$")
    return bool(pattern.match(symbol))


def _get_underlying_from_option(symbol: str) -> str:
    """Extract underlying symbol from option symbol (e.g., NVDA250221C00300000 -> NVDA)."""
    # Option format: STOCK250221C00300000
    # Extract letters at start until we hit a digit
    match = re.match(r"^([A-Z]+)", symbol)
    if match:
        return match.group(1)
    return None


# Load configurations
FOMO_TRADE_CONFIG = _load_fomo_trade_config()
EXCLUDED_SYMBOLS = _load_excluded_symbols()

# Calculate effective percentages (convert from % to decimal) - only used if symbol not in fomo_trade config
STOP_LOSS_PCT = args.stop_loss / 100.0 if args.stop_loss else 0.01
TARGET1_PCT = (args.target1 / 100.0 if args.target1 else args.stop_loss / 100.0)
TARGET2_PCT = (args.target2 / 100.0 if args.target2 else (2 * TARGET1_PCT))
TICKER_FILTER = args.ticker.upper() if args.ticker else None

PAPER = credentials.paper
stock_data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
crypto_data_client = CryptoHistoricalDataClient(credentials.api_key, credentials.secret_key)

# ── Constants ────────────────────────────────────────────────────────────────
CHECK_INTERVAL   = 30    # seconds between market checks
BUY_QTY          = 2     # shares per entry
LOG_FILE         = os.path.join(os.path.dirname(__file__), 'risk_management_log.txt')

# ── Per-symbol state ─────────────────────────────────────────────────────────
# state keys per symbol:
#   entry_price, stop_loss, target1, target1_hit, breakeven_set
#   waiting_reentry, internal_qty
state: dict = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def round_up_to_penny(price: float) -> float:
    """Round up sub-penny prices to the nearest penny (2 decimal places)."""
    return math.ceil(price * 100) / 100

def is_crypto(symbol: str) -> bool:
    return '/' in symbol


def _normalize_symbol(symbol: str) -> str:
    return str(symbol).replace('/', '').replace('-', '').upper()


def _build_latest_buy_fill_map() -> dict[str, float]:
    latest_buy_fill_by_symbol: dict[str, float] = {}
    try:
        orders = list(
            trading_client.get_orders(
                filter=GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=500)
            )
        )
    except Exception as exc:
        print(f"  ⚠ Could not read closed orders for entry cross-check: {exc}")
        return latest_buy_fill_by_symbol

    candidates = []
    for order in orders:
        side = (order.side.value if hasattr(order.side, 'value') else str(order.side)).upper()
        if side != 'BUY':
            continue
        filled_at = getattr(order, 'filled_at', None)
        filled_avg_price = getattr(order, 'filled_avg_price', None)
        if filled_at is None or filled_avg_price in (None, ''):
            continue
        try:
            price = float(filled_avg_price)
        except (TypeError, ValueError):
            continue
        candidates.append((filled_at, _normalize_symbol(order.symbol), price))

    candidates.sort(key=lambda row: row[0], reverse=True)
    for _, norm_symbol, price in candidates:
        if norm_symbol not in latest_buy_fill_by_symbol:
            latest_buy_fill_by_symbol[norm_symbol] = price

    return latest_buy_fill_by_symbol


def _resolve_entry_price(symbol: str, position, latest_buy_fill_by_symbol: dict[str, float]):
    position_entry = None
    try:
        raw_entry = getattr(position, 'avg_entry_price', None)
        if raw_entry not in (None, ''):
            position_entry = float(raw_entry)
    except (TypeError, ValueError):
        position_entry = None

    order_entry = latest_buy_fill_by_symbol.get(_normalize_symbol(symbol))

    if position_entry is not None and position_entry > 0:
        return position_entry, 'position.avg_entry_price', position_entry, order_entry
    if order_entry is not None and order_entry > 0:
        return order_entry, 'latest filled BUY order', position_entry, order_entry
    return None, 'unavailable', position_entry, order_entry

def get_price(symbol: str) -> float:
    try:
        if is_crypto(symbol):
            r = CryptoLatestTradeRequest(symbol_or_symbols=symbol)
            return float(crypto_data_client.get_crypto_latest_trade(r)[symbol].price)
        else:
            r = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
            return float(stock_data_client.get_stock_latest_trade(r)[symbol].price)
    except Exception as e:
        print(f"  ✗ Could not get price for {symbol}: {e}")
        return None

def calculate_levels(entry: float):
    """Calculate stop loss and profit targets based on configured percentages."""
    stop  = round_up_to_penny(entry * (1 - STOP_LOSS_PCT))
    tgt1  = round_up_to_penny(entry * (1 + TARGET1_PCT))
    tgt2  = round_up_to_penny(entry * (1 + TARGET2_PCT))
    return stop, tgt1, tgt2

def log_stopped_trade(symbol, entry, stop, qty, reason='STOP'):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {reason:<12} {symbol:<8} entry={entry:.4f}  stop={stop:.4f}  qty={qty}\n"
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(line)
    except Exception as e:
        print(f"  ✗ Could not write log: {e}")
    print(f"  📝 Logged: {line.strip()}")

def cancel_open_orders(symbol: str):
    try:
        orders = list(trading_client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=100)
        ))
        sym_orders = [o for o in orders if o.symbol == symbol]
        for o in sym_orders:
            trading_client.cancel_order_by_id(o.id)
            side = o.side.value if hasattr(o.side, 'value') else str(o.side)
            print(f"  ✓ Cancelled open {side.upper()} order for {symbol}")
    except Exception as e:
        print(f"  ✗ Error cancelling orders for {symbol}: {e}")

def place_market_sell(symbol: str, qty: int, reason: str) -> bool:
    try:
        order = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC
        )
        resp = trading_client.submit_order(order_data=order)
        print(f"  ✓ Market SELL {qty} {symbol} submitted ({reason}) — ID: {resp.id}")
        return True
    except Exception as e:
        print(f"  ✗ Error selling {symbol}: {e}")
        return False

def place_limit_buy(symbol: str, qty: int, price: float, reason: str) -> bool:
    try:
        order = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            limit_price=price,
            time_in_force=TimeInForce.GTC
        )
        resp = trading_client.submit_order(order_data=order)
        print(f"  ✓ Limit BUY {qty} {symbol} @ ${price:.4f} submitted ({reason}) — ID: {resp.id}")
        return True
    except Exception as e:
        print(f"  ✗ Error buying {symbol}: {e}")
        return False

def init_symbol_state(symbol: str, entry: float, fomo_config: dict = None):
    """Initialize symbol state. If fomo_config provided, use its stop/targets. Otherwise calculate from percentages."""
    entry = round_up_to_penny(entry)
    
    # Check if this symbol has specific configuration from fomo_trade.txt
    if fomo_config:
        stop = round_up_to_penny(fomo_config.get('stop_price', entry))
        tgt1 = round_up_to_penny(fomo_config.get('target1_price', entry))
        tgt2 = round_up_to_penny(fomo_config.get('target2_price', entry))
    else:
        # Fallback to percentage-based calculation
        stop, tgt1, tgt2 = calculate_levels(entry)
    
    state[symbol] = {
        'entry_price':        entry,
        'stop_loss':          stop,
        'target1':            tgt1,
        'target2':            tgt2,
        'target1_hit':        False,
        'breakeven_set':      False,
        'waiting_reentry':    False,
        'entry_order_placed': False,  # Track if we've placed initial entry order
        'entry_price_used':   entry,  # Track entry price that was used when order was placed
        'reentry_order_placed': False,  # Track if we've placed a re-entry order
        'internal_qty':       BUY_QTY,
    }
    return state[symbol]

def print_state(symbol: str, s: dict, current_price: float):
    pnl = (current_price - s['entry_price']) * s['internal_qty']
    print(f"  {symbol:<8} price=${current_price:.4f} | entry=${s['entry_price']:.4f}"
          f" | stop=${s['stop_loss']:.4f} | tgt1=${s['target1']:.4f} | tgt2=${s['target2']:.4f}"
          f" | qty={s['internal_qty']} | P&L≈${pnl:+.2f}"
          f"{' | waiting_reentry' if s['waiting_reentry'] else ''}"
          f"{' | tgt1_hit' if s['target1_hit'] else ''}")


# ── Core per-symbol logic ─────────────────────────────────────────────────────

def manage_symbol(symbol: str, position=None, latest_buy_fill_by_symbol=None, fomo_configs=None):
    """Run one monitoring tick for a symbol."""
    if latest_buy_fill_by_symbol is None:
        latest_buy_fill_by_symbol = {}
    if fomo_configs is None:
        fomo_configs = {}

    s = state.get(symbol)
    
    # Get fomo_trade config for this symbol (if any) - use the passed-in reloaded config
    fomo_cfg = fomo_configs.get(symbol)

    # ── No position branch ────────────────────────────────────────────────────
    if position is None:
        # Check if entry price has changed in fomo_trade.txt
        if s and fomo_cfg and not s['waiting_reentry']:
            config_entry_price = round_up_to_penny(fomo_cfg['entry_price'])
            last_used_price = s.get('entry_price_used', config_entry_price)
            
            # If entry price in config changed, update the order
            if abs(config_entry_price - last_used_price) > 1e-9:
                print(f"\n  [{symbol}] Entry price updated in fomo_trade.txt: ${last_used_price:.4f} → ${config_entry_price:.4f}")
                # Cancel existing entry orders
                cancel_open_orders(symbol)
                # Place new order with updated price
                ok = place_limit_buy(symbol, BUY_QTY, config_entry_price, 'ENTRY (UPDATED)')
                if ok:
                    s['entry_price'] = config_entry_price
                    s['entry_price_used'] = config_entry_price
                    # Recalculate stop/target if they depend on entry price
                    if fomo_cfg:
                        s['stop_loss'] = round_up_to_penny(fomo_cfg.get('stop_price', config_entry_price))
                        s['target1'] = round_up_to_penny(fomo_cfg.get('target1_price', config_entry_price))
                        s['target2'] = round_up_to_penny(fomo_cfg.get('target2_price', config_entry_price))
                    print(f"  ✓ [{symbol}] New entry order placed @ ${config_entry_price:.4f}")
                return
        
        # Place initial entry order (if not already placed)
        if s and not s['waiting_reentry'] and not s['entry_order_placed'] and fomo_cfg:
            # Place initial entry order
            print(f"\n  [{symbol}] Placing initial entry order from fomo_trade.txt")
            entry_price = round_up_to_penny(fomo_cfg['entry_price'])
            ok = place_limit_buy(symbol, BUY_QTY, entry_price, 'ENTRY')
            if ok:
                s['entry_order_placed'] = True
                s['entry_price_used'] = entry_price
                print(f"  ✓ [{symbol}] Entry limit buy placed @ ${entry_price:.4f}. Waiting for fill...")
            return
        
        # Check if we need to place re-entry order (after stop loss)
        if s and s['waiting_reentry']:
            price = get_price(symbol)
            if price is None:
                return
            if price >= s['entry_price']:
                # Only place re-entry order once (not multiple times)
                if not s.get('reentry_order_placed'):
                    print(f"\n  [{symbol}] Price ${price:.4f} recovered to entry ${s['entry_price']:.4f} — placing re-entry order")
                    cancel_open_orders(symbol)
                    ok = place_limit_buy(symbol, BUY_QTY, s['entry_price'], 'RE-ENTRY')
                    if ok:
                        s['reentry_order_placed'] = True
                        print(f"  ✓ [{symbol}] Re-entry limit buy placed. Waiting for fill...")
                else:
                    print(f"  [{symbol}] Re-entry limit buy pending @ ${s['entry_price']:.4f} | current=${price:.4f}")
            else:
                print(f"  [{symbol}] Waiting for reversal to ${s['entry_price']:.4f} | current=${price:.4f}")
        return

    # ── Position exists ───────────────────────────────────────────────────────
    entry, entry_source, position_entry, order_entry = _resolve_entry_price(
        symbol,
        position,
        latest_buy_fill_by_symbol,
    )
    if entry is None:
        print(
            f"  ✗ [{symbol}] Could not determine entry price "
            f"(position={position_entry}, latest_buy_fill={order_entry})"
        )
        return

    qty   = float(position.qty)

    print(
        f"  [ENTRY] {symbol}: qty={qty} | "
        f"position_entry={position_entry if position_entry is not None else 'N/A'} | "
        f"latest_buy_fill={order_entry if order_entry is not None else 'N/A'} | "
        f"using={entry:.4f} ({entry_source})"
    )

    # First time we see this symbol — initialise state
    if s is None:
        s = init_symbol_state(symbol, entry, fomo_cfg)

    # Always keep risk levels aligned with current live entry and qty.
    if abs(s['entry_price'] - entry) > 1e-9:
        s = init_symbol_state(symbol, entry, fomo_cfg)
    
    # Check if fomo config has changed (stop/target prices) and update state accordingly
    if fomo_cfg:
        new_stop = round_up_to_penny(fomo_cfg.get('stop_price', entry))
        new_tgt1 = round_up_to_penny(fomo_cfg.get('target1_price', entry))
        new_tgt2 = round_up_to_penny(fomo_cfg.get('target2_price', entry))
        
        # If config values changed, update state (except if we've already hit target1)
        if not s.get('target1_hit'):
            if abs(s['stop_loss'] - new_stop) > 1e-9 or abs(s['target1'] - new_tgt1) > 1e-9 or abs(s['target2'] - new_tgt2) > 1e-9:
                print(f"  ✓ [{symbol}] Config updated: stop ${s['stop_loss']:.4f}→${new_stop:.4f} | tgt1 ${s['target1']:.4f}→${new_tgt1:.4f} | tgt2 ${s['target2']:.4f}→${new_tgt2:.4f}")
                s['stop_loss'] = new_stop
                s['target1'] = new_tgt1
                s['target2'] = new_tgt2
        elif s.get('target1_hit') and not s.get('breakeven_set'):
            # Target 1 was hit, update target2 if it changed
            if abs(s['target2'] - new_tgt2) > 1e-9:
                print(f"  ✓ [{symbol}] Target2 updated: ${s['target2']:.4f}→${new_tgt2:.4f}")
                s['target2'] = new_tgt2

    live_qty = max(int(round(abs(qty))), 0)
    s['internal_qty'] = min(BUY_QTY, live_qty)

    price = get_price(symbol)
    if price is None:
        return

    print_state(symbol, s, price)

    current_qty  = s['internal_qty']
    if current_qty <= 0:
        return

    stop_loss    = s['stop_loss']
    target1      = s['target1']
    target2      = s['target2']
    entry_price  = s['entry_price']

    # ── STOP LOSS ─────────────────────────────────────────────────────────────
    if not s['waiting_reentry'] and price <= stop_loss:
        reason = 'BREAKEVEN_STOP' if s['breakeven_set'] else 'STOP_LOSS'
        print(f"\n  ✗ [{symbol}] {reason} HIT at ${price:.4f} (stop=${stop_loss:.4f})")
        cancel_open_orders(symbol)
        ok = place_market_sell(symbol, current_qty, reason)
        if ok:
            log_stopped_trade(symbol, entry_price, stop_loss, current_qty, reason)
            s.update({
                'waiting_reentry':       True,
                'reentry_order_placed':  False,  # Reset re-entry flag for new entry
                'entry_order_placed':    False,  # Reset entry flag
                'entry_price_used':      entry_price,  # Track what price was used
                'internal_qty':          BUY_QTY,
                'target1_hit':           False,
                'breakeven_set':         False,
            })

    # ── TARGET 1 ──────────────────────────────────────────────────────────────
    elif not s['target1_hit'] and price >= target1:
        # Sell 1/2 of position
        sell_qty = max(current_qty // 2, 1)
        print(f"\n  ✓ [{symbol}] TARGET 1 HIT at ${price:.4f} (tgt=${target1:.4f}) — selling {sell_qty} shares (1/2 position)")
        ok = place_market_sell(symbol, sell_qty, 'TARGET1')
        if ok:
            s['target1_hit']   = True
            s['internal_qty']  = max(current_qty - sell_qty, 0)
            s['stop_loss']     = entry_price   # move stop to entry price (breakeven)
            s['breakeven_set'] = True
            print(f"  ✓ [{symbol}] Stop moved to breakeven ${entry_price:.4f}")
            print(f"  ✓ [{symbol}] Remaining position: {s['internal_qty']} shares | Keep Target 2 at ${target2:.4f}")

    # ── TARGET 2 ──────────────────────────────────────────────────────────────
    elif s['target1_hit'] and s['internal_qty'] > 0 and price >= target2:
        remaining_qty = s['internal_qty']
        print(f"\n  ✓ [{symbol}] TARGET 2 HIT at ${price:.4f} (tgt=${target2:.4f}) — closing remaining {remaining_qty} shares")
        ok = place_market_sell(symbol, remaining_qty, 'TARGET2')
        if ok:
            s['internal_qty'] = 0
            s['target1_hit'] = False
            s['breakeven_set'] = False
            s['waiting_reentry'] = False
            s['reentry_order_placed'] = False
            s['entry_order_placed'] = False
            s['entry_price_used'] = s['entry_price']  # Reset to current entry price
            print(f"  ✓ [{symbol}] Position fully closed. Ready for new trade.")

    # ── No action ─────────────────────────────────────────────────────────────
    else:
        pass  # status already printed by print_state
    
    # Save state back to global dictionary
    state[symbol] = s


# ── Main loop ────────────────────────────────────────────────────────────────

def print_header():
    ticker_info = f" | Ticker: {TICKER_FILTER}" if TICKER_FILTER else ""
    print(f"\n{'═'*80}")
    print(f"  RISK MANAGEMENT BOT  |  {'Paper' if PAPER else 'Live'}  |  {datetime.now().strftime('%H:%M:%S')}{ticker_info}")
    print(f"  Stop: {STOP_LOSS_PCT*100:.3f}% | Target1: {TARGET1_PCT*100:.3f}% | Target2: {TARGET2_PCT*100:.3f}% | Check: {CHECK_INTERVAL}s")
    print(f"{'═'*80}")

def run():
    if TICKER_FILTER:
        scope_info = f" for {TICKER_FILTER}"
    else:
        # Get initial config to display
        initial_config = _load_fomo_trade_config()
        config_symbols = ", ".join(sorted(initial_config.keys())) if initial_config else "none"
        excluded_str = ", ".join(sorted(EXCLUDED_SYMBOLS)) if EXCLUDED_SYMBOLS else "none"
        scope_info = f" | Managing: {config_symbols} | Excluding: {excluded_str}"
    
    print(f"\n╔{'═'*100}╗")
    print(f"║  Risk Management Bot starting{scope_info:<70}║")
    print(f"║  Using stop/target levels from lists/fomo_trade.txt (reloads every check){' '*18}║")
    print(f"║  Logic: Stop → Exit & Wait | Target1 → Sell 1/2 (set stop to entry) | Target2 → Close{' '*8}║")
    print(f"║  Default Fallback: Stop: {STOP_LOSS_PCT*100:.3f}% | Target1: {TARGET1_PCT*100:.3f}% | Target2: {TARGET2_PCT*100:.3f}%{' '*20}║")
    print(f"║  Log file: {os.path.basename(LOG_FILE):<70}║")
    print(f"║  Press Ctrl+C to stop{' '*76}║")
    print(f"╚{'═'*100}╝\n")

    while True:
        try:
            # Reload fomo_trade.txt config on every iteration (in case levels changed)
            current_fomo_config = _load_fomo_trade_config()
            
            print_header()

            # Fetch all current positions
            try:
                all_positions = {p.symbol: p for p in trading_client.get_all_positions()}
                
                # Filter by ticker if specified (for backward compatibility)
                if TICKER_FILTER:
                    positions = {k: v for k, v in all_positions.items() if k.upper() == TICKER_FILTER}
                else:
                    # Only include positions that are in fomo_trade.txt config
                    positions = {}
                    for k, v in all_positions.items():
                        symbol_upper = k.upper()
                        
                        # Only manage if symbol is in fomo_trade config OR in state (re-entry waiting)
                        if symbol_upper in current_fomo_config or symbol_upper in state:
                            # Skip if symbol is in exclusion list (spray.txt)
                            if symbol_upper in EXCLUDED_SYMBOLS:
                                continue
                            positions[k] = v
                
                latest_buy_fill_by_symbol = _build_latest_buy_fill_map()
            except Exception as e:
                print(f"  ✗ Error fetching positions: {e}")
                time.sleep(CHECK_INTERVAL)
                continue

            # Manage all symbols in fomo_trade.txt config
            managed_any = False
            
            # Build set of all symbols we need to manage:
            # 1. Symbols with open positions in fomo_trade.txt
            # 2. Symbols in fomo_trade.txt that have state (tracking re-entries or previous activity)
            # 3. All symbols in fomo_trade.txt config (ensure continuous tracking)
            symbols_to_manage = set(positions.keys()) | set(state.keys()) | set(current_fomo_config.keys())
            
            for symbol in symbols_to_manage:
                # Skip if excluded
                if symbol in EXCLUDED_SYMBOLS:
                    continue
                
                pos = positions.get(symbol)
                s = state.get(symbol)
                
                # Initialize state if this is first time seeing this fomo_trade symbol
                if s is None and symbol in current_fomo_config:
                    s = init_symbol_state(symbol, current_fomo_config[symbol]['entry_price'], current_fomo_config[symbol])
                    print(f"  ℹ [{symbol}] Now tracking from fomo_trade.txt | entry=${s['entry_price']:.4f} | stop=${s['stop_loss']:.4f}")
                
                # Manage this symbol
                manage_symbol(symbol, pos, latest_buy_fill_by_symbol, current_fomo_config)
                managed_any = True
            
            if not managed_any:
                print("  No open positions matching fomo_trade.txt config and no pending re-entries.")

            print(f"\n  Next check in {CHECK_INTERVAL}s...\n")
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\n\nRisk Management Bot stopped by user.\n")
            break
        except Exception as e:
            print(f"\n  ✗ Unexpected error: {e}")
            time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    run()

