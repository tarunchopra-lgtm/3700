#!/usr/bin/env python3
"""
Status - Today's orders and current positions

Usage: python status.py [SYMBOL] [--help]
       SYMBOL is optional - if provided, filters to that ticker only

Examples:
    python status.py        # Show all orders and positions
    python status.py MU     # Show only MU orders and position
    python status.py --help # Show detailed usage
"""

import sys

# Handle help flag early
if len(sys.argv) > 1 and sys.argv[1] in ["--help", "-h", "help"]:
    print("""
STATUS.PY - View Today's Orders and Current Positions

SYNTAX:
  python strategies/status.py [SYMBOL] [--help]

OPTIONAL:
  SYMBOL    Filter results to specific ticker (e.g., MU, AAPL, SPY)
  --help, -h  Show this help message

DESCRIPTION:
  Displays today's trading activity and current account status
  Shows open orders, filled orders (today), and open positions
  Optional symbol filter to view only specific ticker activity
  Real-time account balance and buying power display

DISPLAY SECTIONS:
  
  Account Balance:
  - Available cash
  - Buying power (margin available)
  - Total equity
  - Portfolio value

  Open Orders:
  - Unfilled pending orders
  - Order type, quantity, price
  - Time placed and time-in-force
  - Displayed with numeric index for reference

  Filled Orders (Today):
  - Orders filled today only
  - Fill price, timestamp, quantity
  - P&L per fill
  - Total P&L for day

  Current Positions:
  - All open holdings
  - Quantity, entry price, current price
  - Market value per position
  - Unrealized P&L
  - Total position value

EXAMPLES:
  python strategies/status.py
    - Show all account activity and positions
    - No filtering applied

  python strategies/status.py MU
    - Filter to Micron only
    - Show MU orders and positions

  python strategies/status.py AAPL
    - Show Apple activity only
    - Open orders for AAPL
    - Filled trades today for AAPL
    - Current AAPL position

  python strategies/status.py SPY --help
    - Show help (filter ignored when --help present)

FEATURES:
  - Paper vs. Live trading indicator
  - Timestamp of status report
  - Symbol filtering for focused view
  - Interactive close position options
  - Real-time data from Alpaca

OUTPUT:
  Console display with:
  - Account header with timestamp
  - Paper/Live indicator
  - Symbol filter indicator
  - Multiple sections for orders/positions
  - Formatted prices and quantities
  - Error handling per section

ACCOUNT BALANCE DISPLAY:
  - Cash: Available uninvested funds
  - Buying Power: Margin available for new orders
  - Equity: Total account value
  - Portfolio Value: Current holdings value

NOTES:
  - Requires valid Alpaca API credentials
  - Works with both paper and live accounts
  - Filters apply to all sections
  - Optional symbol filter for focused analysis
  - Run anytime to get current account snapshot
  - Paper/Live mode determined by credentials
  - Use daily for account monitoring
  - Great for post-market position review
""")
    sys.exit(0)

from datetime import datetime, date, timezone
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus

from pathlib import Path
import sys

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth
import re

try:
    credentials, trading_client = bootstrap_trading_auth("status.py")
except Exception as exc:
    print(f"Authentication failed: {exc}")
    raise SystemExit(1)

PAPER = credentials.paper

SYMBOL_FILTER = sys.argv[1].upper() if len(sys.argv) > 1 else None

today = date.today()

def _extract_underlying_symbol(symbol: str) -> str:
    """Extract underlying symbol from option symbol (e.g., AAPL260911C00315000 -> AAPL)."""
    # Option format: STOCK260911C00315000 (stock + date + C/P + price)
    match = re.match(r"^([A-Z]+)", symbol)
    if match:
        return match.group(1)
    return symbol

def _is_option_symbol(symbol: str) -> bool:
    """Check if symbol is an option (e.g., AAPL260911C00315000)."""
    pattern = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")
    return bool(pattern.match(symbol))

def filter_symbol(items, key='symbol'):
    if SYMBOL_FILTER is None:
        return items
    return [i for i in items if getattr(i, key, '').upper() == SYMBOL_FILTER]

def format_price(val):
    try:
        return f"${float(val):.2f}"
    except (TypeError, ValueError):
        return "Market"

def print_account_balance():
    try:
        account = trading_client.get_account()
        cash = getattr(account, 'cash', None)
        buying_power = getattr(account, 'buying_power', None)
        portfolio_value = getattr(account, 'portfolio_value', None)
        equity = getattr(account, 'equity', None)

        print(f"\n{'─'*64}")
        print(f"  ACCOUNT BALANCE")
        print(f"{'─'*64}")
        print(f"  Cash:          {format_price(cash)}")
        print(f"  Buying Power:  {format_price(buying_power)}")
        if equity is not None:
            print(f"  Equity:        {format_price(equity)}")
        if portfolio_value is not None:
            print(f"  Portfolio Val: {format_price(portfolio_value)}")
    except Exception as e:
        print(f"  ✗ Error fetching account balance: {e}")

def get_current_positions():
    positions = list(trading_client.get_all_positions())
    return filter_symbol(positions)

def close_position_by_symbol(symbol: str):
    try:
        response = trading_client.close_position(symbol)
        print(f"  ✓ Closed {symbol} at market price. Order ID: {getattr(response, 'id', 'N/A')}")
        return True
    except Exception as e:
        print(f"  ✗ Error closing {symbol}: {e}")
        return False

def close_all_displayed_positions(positions):
    if not positions:
        print("  ✗ No positions to close")
        return

    confirm = input(f"Close ALL {len(positions)} displayed position(s) at market price? (Y/N): ").strip().upper()
    if confirm != 'Y':
        print("  Cancelled.")
        return

    closed = 0
    for pos in positions:
        if close_position_by_symbol(pos.symbol):
            closed += 1

    print(f"  ✓ Closed {closed}/{len(positions)} position(s)")

print(f"\n╔{'═'*62}╗")
print(f"║  ACCOUNT STATUS  {'Paper' if PAPER else 'Live':>6} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S'):<20}  ║")
if SYMBOL_FILTER:
    print(f"║  Filter: {SYMBOL_FILTER:<52}║")
print(f"╚{'═'*62}╝")

print_account_balance()

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
positions = []
try:
    positions = get_current_positions()
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

# ── P&L SUMMARY BY TICKER (TODAY'S FILLED ORDERS) ─────────────
print(f"\n{'─'*64}")
print(f"  P&L SUMMARY BY TICKER - TODAY'S TRADES (stocks + options)")
print(f"{'─'*64}")

try:
    # Get all filled orders today
    filled_orders = list(trading_client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=200)))
    
    # Collect all fills for each symbol
    symbol_fills = {}  # symbol -> list of (time, side, qty, price)
    
    for o in filled_orders:
        if o.filled_at is None:
            continue
        filled_date = o.filled_at.date() if hasattr(o.filled_at, 'date') else None
        if filled_date != today:
            continue
        
        symbol = o.symbol
        side = (o.side.value if hasattr(o.side, 'value') else str(o.side)).upper()
        qty = float(o.filled_qty or o.qty)
        fill_price = float(o.filled_avg_price or 0)
        
        if symbol not in symbol_fills:
            symbol_fills[symbol] = []
        
        symbol_fills[symbol].append((o.filled_at, side, qty, fill_price))
    
    # Calculate P&L per symbol using FIFO accounting
    symbol_pnl = {}  # symbol -> total_realized_pnl
    
    for symbol in symbol_fills:
        fills = sorted(symbol_fills[symbol], key=lambda x: x[0])  # Sort by time
        
        # FIFO: track buy queue and match sells against oldest buys
        buy_queue = []  # list of (qty, price)
        pnl = 0.0
        
        for time, side, qty, price in fills:
            if side == 'BUY':
                buy_queue.append((qty, price))
            else:  # SELL
                qty_remaining = qty
                while qty_remaining > 0 and buy_queue:
                    buy_qty, buy_price = buy_queue.pop(0)
                    
                    # Match qty_remaining against this buy
                    matched_qty = min(qty_remaining, buy_qty)
                    pnl += (price - buy_price) * matched_qty
                    qty_remaining -= matched_qty
                    
                    # If not all of this buy was used, put remainder back
                    if matched_qty < buy_qty:
                        buy_queue.insert(0, (buy_qty - matched_qty, buy_price))
        
        symbol_pnl[symbol] = pnl
    
    # Group by underlying ticker and sum P&L
    ticker_summary = {}  # ticker -> {'total_pnl': X, 'symbol_pnl': {symbol: pnl}}
    
    for symbol in symbol_pnl:
        underlying = _extract_underlying_symbol(symbol)
        pnl = symbol_pnl[symbol]
        
        if underlying not in ticker_summary:
            ticker_summary[underlying] = {
                'total_pnl': 0.0,
                'symbols': {}
            }
        
        ticker_summary[underlying]['total_pnl'] += pnl
        ticker_summary[underlying]['symbols'][symbol] = {
            'pnl': pnl,
            'is_option': _is_option_symbol(symbol)
        }
    
    # Sort by total P&L descending
    sorted_tickers = sorted(ticker_summary.items(), key=lambda x: x[1]['total_pnl'], reverse=True)
    
    if sorted_tickers:
        for ticker, data in sorted_tickers:
            total_pnl = data['total_pnl']
            arrow = '▲' if total_pnl >= 0 else '▼'
            pnl_color = '+' if total_pnl >= 0 else ''
            
            print(f"  {ticker:<6} {pnl_color}${total_pnl:>8.2f} {arrow}")
            
            # Show individual symbols
            for symbol in sorted(data['symbols'].keys()):
                sym_data = data['symbols'][symbol]
                pos_type = "(OPTION)" if sym_data['is_option'] else "(STOCK)"
                sym_pnl = sym_data['pnl']
                sym_pnl_color = '+' if sym_pnl >= 0 else ''
                print(f"         {symbol:<15} {pos_type:<8} {sym_pnl_color}${sym_pnl:>8.2f}")
        
        # Total for all tickers
        total_all_pnl = sum(data['total_pnl'] for data in ticker_summary.values())
        total_arrow = '▲' if total_all_pnl >= 0 else '▼'
        total_color = '+' if total_all_pnl >= 0 else ''
        print(f"\n  {'TOTAL':<6} {total_color}${total_all_pnl:>8.2f} {total_arrow}")
    else:
        print("  (no filled orders today)")
        
except Exception as e:
    print(f"  ✗ Error calculating P&L summary: {e}")

if positions:
    while True:
        print(f"\n{'─'*64}")
        print("  POSITION ACTIONS")
        print(f"{'─'*64}")
        for i, pos in enumerate(positions, 1):
            print(f"  [{i}] Close {pos.symbol} at market")
        print("  [A] Close ALL displayed positions at market")
        print("  [R] Refresh")
        print("  [Q] Quit")

        choice = input("Select an option: ").strip().upper()

        if choice == 'Q':
            break
        if choice == 'R':
            break
        if choice == 'A':
            close_all_displayed_positions(positions)
            break
        if choice.isdigit():
            index = int(choice)
            if 1 <= index <= len(positions):
                selected = positions[index - 1]
                confirm = input(f"Close {selected.symbol} at market price? (Y/N): ").strip().upper()
                if confirm == 'Y':
                    close_position_by_symbol(selected.symbol)
                else:
                    print("  Cancelled.")
                break
            print(f"  ✗ Invalid selection. Choose 1-{len(positions)}, A, R, or Q.")
            continue
        print(f"  ✗ Invalid input. Choose 1-{len(positions)}, A, R, or Q.")

print(f"{'─'*64}\n")

