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
        description="Risk Management Bot - Automated stop loss and target management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                                    # 1%% stop, 1%% tgt1, 2%% tgt2 on all positions
  %(prog)s --ticker SPY                       # 1%% stop, 1%% tgt1, 2%% tgt2 on SPY only
  %(prog)s --stop-loss 2 --target1 2          # 2%% stop, 2%% tgt1, 2%% tgt2 on all positions
  %(prog)s --ticker SPY --stop-loss 0.5       # 0.5%% stop, 0.5%% tgt1, 1.0%% tgt2 on SPY
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

# ── Load exclusion lists ─────────────────────────────────────────────────────
def _load_excluded_symbols() -> set:
    """Load symbols from lists/fomo_trade.txt and lists/spray.txt to exclude from risk management."""
    excluded = set()
    
    # Read fomo_trade.txt
    fomo_path = WORKSPACE_ROOT / "lists" / "fomo_trade.txt"
    if fomo_path.exists():
        try:
            with open(fomo_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    parts = line.split()
                    if parts:
                        symbol = parts[0].upper()
                        excluded.add(symbol)
        except Exception as e:
            print(f"Warning: Could not read fomo_trade.txt: {e}")
    
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


# Load excluded symbols
EXCLUDED_SYMBOLS = _load_excluded_symbols()

# Calculate effective percentages (convert from % to decimal)
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

def init_symbol_state(symbol: str, entry: float):
    entry = round_up_to_penny(entry)
    stop, tgt1, tgt2 = calculate_levels(entry)
    state[symbol] = {
        'entry_price':   entry,
        'stop_loss':     stop,
        'target1':       tgt1,
        'target2':       tgt2,
        'target1_hit':   False,
        'breakeven_set': False,
        'waiting_reentry': False,
        'internal_qty':  BUY_QTY,
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

def manage_symbol(symbol: str, position=None, latest_buy_fill_by_symbol=None):
    """Run one monitoring tick for a symbol."""
    if latest_buy_fill_by_symbol is None:
        latest_buy_fill_by_symbol = {}

    s = state.get(symbol)

    # ── No position branch ────────────────────────────────────────────────────
    if position is None:
        if s and s['waiting_reentry']:
            price = get_price(symbol)
            if price is None:
                return
            if price >= s['entry_price']:
                print(f"\n  [{symbol}] Price ${price:.4f} recovered to entry ${s['entry_price']:.4f} — re-entering")
                cancel_open_orders(symbol)
                ok = place_limit_buy(symbol, BUY_QTY, s['entry_price'], 'RE-ENTRY')
                if ok:
                    stop, tgt1, tgt2 = calculate_levels(s['entry_price'])
                    s.update({
                        'stop_loss':     stop,
                        'target1':       tgt1,
                        'target2':       tgt2,
                        'target1_hit':   False,
                        'breakeven_set': False,
                        'waiting_reentry': False,
                        'internal_qty':  BUY_QTY,
                    })
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
    if s is None or s.get('waiting_reentry'):
        s = init_symbol_state(symbol, entry)

    # Always keep risk levels aligned with current live entry and qty.
    if abs(s['entry_price'] - entry) > 1e-9:
        s = init_symbol_state(symbol, entry)

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
                'waiting_reentry': True,
                'internal_qty':    BUY_QTY,
                'target1_hit':     False,
                'breakeven_set':   False,
            })

    # ── TARGET 1 ──────────────────────────────────────────────────────────────
    elif not s['target1_hit'] and price >= target1:
        sell_qty = 1 if current_qty > 1 else current_qty
        print(f"\n  ✓ [{symbol}] TARGET 1 HIT at ${price:.4f} (tgt=${target1:.4f}) — selling {sell_qty} share")
        ok = place_market_sell(symbol, sell_qty, 'TARGET1')
        if ok:
            s['target1_hit']   = True
            s['internal_qty']  = max(current_qty - sell_qty, 0)
            s['stop_loss']     = entry_price   # move stop to breakeven
            s['breakeven_set'] = True
            print(f"  ✓ [{symbol}] Stop moved to breakeven ${entry_price:.4f}")

    # ── TARGET 2 ──────────────────────────────────────────────────────────────
    elif s['target1_hit'] and current_qty > 0 and price >= target2:
        print(f"\n  ✓ [{symbol}] TARGET 2 HIT at ${price:.4f} (tgt=${target2:.4f}) — closing remaining {current_qty}")
        ok = place_market_sell(symbol, current_qty, 'TARGET2')
        if ok:
            s['internal_qty'] = 0
            s['target1_hit'] = False
            s['breakeven_set'] = False
            s['waiting_reentry'] = False

    # ── No action ─────────────────────────────────────────────────────────────
    else:
        pass  # status already printed by print_state


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
        excluded_str = ", ".join(sorted(EXCLUDED_SYMBOLS)) if EXCLUDED_SYMBOLS else "none"
        scope_info = f" (excluding: {excluded_str})"
    
    print(f"\n╔{'═'*78}╗")
    print(f"║  Risk Management Bot starting{scope_info:<48}║")
    print(f"║  Stop: {STOP_LOSS_PCT*100:.3f}% | Target1: {TARGET1_PCT*100:.3f}% | Target2: {TARGET2_PCT*100:.3f}%{' '*28}║")
    print(f"║  Log file: {os.path.basename(LOG_FILE):<60}║")
    print(f"║  Press Ctrl+C to stop{' '*54}║")
    print(f"╚{'═'*78}╝\n")

    while True:
        try:
            print_header()

            # Fetch all current positions
            try:
                all_positions = {p.symbol: p for p in trading_client.get_all_positions()}
                
                # Filter by ticker if specified (for backward compatibility)
                if TICKER_FILTER:
                    positions = {k: v for k, v in all_positions.items() if k.upper() == TICKER_FILTER}
                else:
                    # Include all positions EXCEPT those in exclusion lists and their call options
                    positions = {}
                    for k, v in all_positions.items():
                        symbol_upper = k.upper()
                        
                        # Exclude if symbol is in exclusion list
                        if symbol_upper in EXCLUDED_SYMBOLS:
                            continue
                        
                        # Exclude if it's a call option for an excluded stock
                        if _is_call_option(symbol_upper):
                            underlying = _get_underlying_from_option(symbol_upper)
                            if underlying and underlying in EXCLUDED_SYMBOLS:
                                continue
                        
                        positions[k] = v
                
                latest_buy_fill_by_symbol = _build_latest_buy_fill_map()
            except Exception as e:
                print(f"  ✗ Error fetching positions: {e}")
                time.sleep(CHECK_INTERVAL)
                continue

            if not positions and not any(s.get('waiting_reentry') for s in state.values()):
                print("  No open positions and no pending re-entries.")
            else:
                # Manage active positions
                for sym, pos in positions.items():
                    manage_symbol(sym, pos, latest_buy_fill_by_symbol)

                # Manage waiting re-entries (positions already closed)
                for sym, s in state.items():
                    if sym not in positions and s.get('waiting_reentry'):
                        manage_symbol(sym, None)

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

