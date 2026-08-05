#!/usr/bin/env python3
"""
Risk Management Bot - Automated stop loss and target management for all open positions

Monitors ALL open positions and applies risk management rules:
  - Stop loss: 5% below average entry price
  - Target 1:  entry + (entry - stop_loss)  [1:1 risk/reward]
  - After Target 1: stop moves to entry (breakeven)
  - If stopped out: logs trade, waits for reversal, re-enters 2 shares

Usage: python risk_management.py

Runs continuously every 30 seconds. Press Ctrl+C to stop.
"""

import os
import sys
import time
from datetime import datetime
from alpaca.trading.requests import (
    MarketOrderRequest, LimitOrderRequest, GetOrdersRequest
)
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest, CryptoLatestTradeRequest

from roles.credentials import bootstrap_trading_auth

try:
    credentials, trading_client = bootstrap_trading_auth("risk_management.py")
except Exception as exc:
    print(f"Authentication failed: {exc}")
    raise SystemExit(1)

PAPER = credentials.paper
stock_data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
crypto_data_client = CryptoHistoricalDataClient(credentials.api_key, credentials.secret_key)

# ── Constants ────────────────────────────────────────────────────────────────
CHECK_INTERVAL   = 30    # seconds between market checks
STOP_LOSS_PCT    = 0.05  # 5% below entry
BUY_QTY          = 2     # shares per entry
LOG_FILE         = os.path.join(os.path.dirname(__file__), 'risk_management_log.txt')

# ── Per-symbol state ─────────────────────────────────────────────────────────
# state keys per symbol:
#   entry_price, stop_loss, target1, target1_hit, breakeven_set
#   waiting_reentry, internal_qty
state: dict = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

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
            r = StockLatestTradeRequest(symbol_or_symbols=symbol)
            return float(stock_data_client.get_stock_latest_trade(r)[symbol].price)
    except Exception as e:
        print(f"  ✗ Could not get price for {symbol}: {e}")
        return None

def calculate_levels(entry: float):
    stop  = round(entry * (1 - STOP_LOSS_PCT), 4)
    risk  = entry - stop
    tgt1  = round(entry + risk, 4)
    tgt2  = round(entry + (2 * risk), 4)
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
    print(f"\n{'═'*64}")
    print(f"  RISK MANAGEMENT BOT  |  {'Paper' if PAPER else 'Live'}  |  {datetime.now().strftime('%H:%M:%S')}")
    print(f"  Stop: {STOP_LOSS_PCT*100:.0f}% below entry  |  Buy qty: {BUY_QTY}  |  Check: {CHECK_INTERVAL}s")
    print(f"{'═'*64}")

def run():
    print(f"\n╔{'═'*62}╗")
    print(f"║  Risk Management Bot starting...                              ║")
    print(f"║  Log file: {os.path.basename(LOG_FILE):<50}║")
    print(f"║  Press Ctrl+C to stop                                         ║")
    print(f"╚{'═'*62}╝\n")

    while True:
        try:
            print_header()

            # Fetch all current positions
            try:
                positions = {p.symbol: p for p in trading_client.get_all_positions()}
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
