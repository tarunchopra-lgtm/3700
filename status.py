#!/usr/bin/env python3
"""
Status - Today's orders and current positions

Usage: python status.py [SYMBOL]
       SYMBOL is optional - if provided, filters to that ticker only

Examples:
    python status.py        # Show all orders and positions
    python status.py MU     # Show only MU orders and position
"""

import sys
from datetime import datetime, date, timezone
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus

from roles.credentials import bootstrap_trading_auth

try:
    credentials, trading_client = bootstrap_trading_auth("status.py")
except Exception as exc:
    print(f"Authentication failed: {exc}")
    raise SystemExit(1)

PAPER = credentials.paper

SYMBOL_FILTER = sys.argv[1].upper() if len(sys.argv) > 1 else None

today = date.today()

def filter_symbol(items, key='symbol'):
    if SYMBOL_FILTER is None:
        return items
    return [i for i in items if getattr(i, key, '').upper() == SYMBOL_FILTER]

def format_price(val):
    try:
        return f"${float(val):.2f}"
    except (TypeError, ValueError):
        return "Market"

print(f"\n╔{'═'*62}╗")
print(f"║  ACCOUNT STATUS  {'Paper' if PAPER else 'Live':>6} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S'):<20}  ║")
if SYMBOL_FILTER:
    print(f"║  Filter: {SYMBOL_FILTER:<52}║")
print(f"╚{'═'*62}╝")

# ── OPEN ORDERS ──────────────────────────────────────────────
print(f"\n{'─'*64}")
print(f"  OPEN ORDERS")
print(f"{'─'*64}")
try:
    open_orders = list(trading_client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=100)))
    open_orders = filter_symbol(open_orders)
    if not open_orders:
        print("  (none)")
    else:
        for i, o in enumerate(open_orders, 1):
            side  = (o.side.value if hasattr(o.side, 'value') else str(o.side)).upper()
            price = format_price(o.limit_price)
            qty   = o.qty
            created = o.created_at.strftime('%H:%M:%S') if hasattr(o.created_at, 'strftime') else str(o.created_at)
            tif   = o.time_in_force.value if hasattr(o.time_in_force, 'value') else str(o.time_in_force)
            print(f"  [{i}] {side:<5} {qty:>4} {o.symbol:<6} @ {price:<10} | placed {created} | {tif.upper()}")
except Exception as e:
    print(f"  ✗ Error fetching open orders: {e}")

# ── FILLED ORDERS TODAY ───────────────────────────────────────
print(f"\n{'─'*64}")
print(f"  FILLED ORDERS TODAY")
print(f"{'─'*64}")
try:
    filled_orders = list(trading_client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=200)))
    filled_orders = filter_symbol(filled_orders)

    # Keep only orders filled today
    today_filled = []
    for o in filled_orders:
        if o.filled_at is None:
            continue
        filled_date = o.filled_at.date() if hasattr(o.filled_at, 'date') else None
        if filled_date == today:
            today_filled.append(o)

    if not today_filled:
        print("  (none)")
    else:
        for i, o in enumerate(today_filled, 1):
            side       = (o.side.value if hasattr(o.side, 'value') else str(o.side)).upper()
            fill_price = format_price(o.filled_avg_price)
            qty        = o.filled_qty or o.qty
            filled_at  = o.filled_at.strftime('%H:%M:%S') if hasattr(o.filled_at, 'strftime') else str(o.filled_at)
            print(f"  [{i}] {side:<5} {qty:>4} {o.symbol:<6} @ {fill_price:<10} | filled {filled_at}")
except Exception as e:
    print(f"  ✗ Error fetching filled orders: {e}")

# ── CURRENT POSITIONS ─────────────────────────────────────────
print(f"\n{'─'*64}")
print(f"  CURRENT POSITIONS")
print(f"{'─'*64}")
try:
    positions = list(trading_client.get_all_positions())
    positions = filter_symbol(positions)
    if not positions:
        print("  (none)")
    else:
        for pos in positions:
            qty         = float(pos.qty)
            entry       = float(pos.avg_entry_price)
            current     = float(pos.current_price)
            pnl         = float(pos.unrealized_pl)
            pnl_pct     = float(pos.unrealized_plpc) * 100
            mkt_val     = float(pos.market_value)
            side        = pos.side.value.upper() if hasattr(pos.side, 'value') else str(pos.side).upper()
            arrow       = '▲' if pnl >= 0 else '▼'
            pnl_color   = '+' if pnl >= 0 else ''

            print(f"  {pos.symbol:<6} {side:<5} {qty:>5.2f} shares")
            print(f"         Entry:   {format_price(entry)}")
            print(f"         Current: {format_price(current)}")
            print(f"         P&L:     {pnl_color}${pnl:.2f} ({pnl_color}{pnl_pct:.2f}%) {arrow}")
            print(f"         Value:   ${mkt_val:.2f}")
            print()
except Exception as e:
    print(f"  ✗ Error fetching positions: {e}")

print(f"{'─'*64}\n")
