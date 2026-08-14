#!/usr/bin/env python3
"""
Orders Menu - Interactive order management for stocks

Displays all open orders from today and provides options to:
- Cancel individual orders
- Cancel all orders
- Refresh order list

Usage: python orders_menu.py
"""

import os
import sys
from datetime import datetime

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from pathlib import Path
import sys

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth


def build_authenticated_client() -> tuple[TradingClient, bool]:
    credentials, trading_client = bootstrap_trading_auth("orders_menu.py")
    return trading_client, credentials.paper

def get_today_orders(trading_client: TradingClient):
    """Fetch all open orders"""
    try:
        request = GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=100)
        all_orders = trading_client.get_orders(filter=request)
        orders_list = list(all_orders)
        print(f"  [DEBUG] API returned {len(orders_list)} open orders")
        for o in orders_list:
            print(f"  [DEBUG] {o.symbol} {o.side} {o.qty} @ {o.limit_price} status={o.status}")
        return orders_list
    except Exception as e:
        print(f"âœ— Error fetching orders: {e}")
        import traceback; traceback.print_exc()
        return []


def get_open_positions(trading_client: TradingClient):
    """Fetch all open positions."""
    try:
        positions = list(trading_client.get_all_positions())
        print(f"  [DEBUG] API returned {len(positions)} open positions")
        return positions
    except Exception as e:
        print(f"âœ— Error fetching positions: {e}")
        import traceback; traceback.print_exc()
        return []


def display_positions(positions):
    """Display open positions before orders."""
    print(f"\n{'â”€' * 64}")
    print("  OPEN POSITIONS")
    print(f"{'â”€' * 64}")

    if not positions:
        print("  (none)")
        return

    for position in positions:
        symbol = getattr(position, 'symbol', '?')
        qty = getattr(position, 'qty', '?')
        side = getattr(position, 'side', None)
        if hasattr(side, 'value'):
            side = side.value
        side = str(side).upper() if side is not None else "LONG"

        avg_entry_price = getattr(position, 'avg_entry_price', None)
        market_value = getattr(position, 'market_value', None)
        unrealized_pl = getattr(position, 'unrealized_pl', None)

        avg_text = f"${avg_entry_price}" if avg_entry_price not in (None, "") else "N/A"
        mv_text = f"${market_value}" if market_value not in (None, "") else "N/A"
        pl_text = f"${unrealized_pl}" if unrealized_pl not in (None, "") else "N/A"

        print(
            f"  {side:<5} {qty:>8} {symbol:<8} | "
            f"avg {avg_text:<12} mv {mv_text:<14} uP/L {pl_text}"
        )

def display_orders(orders):
    """Display orders in a formatted menu"""
    if not orders:
        print("\nâœ— No open orders from today\n")
        return False
    
    print(f"\nâ•”â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•—")
    print(f"â•‘           ALL OPEN ORDERS - {datetime.now().strftime('%Y-%m-%d')}                   â•‘")
    print(f"â• â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•£")
    
    for i, order in enumerate(orders, 1):
        symbol = order.symbol
        side = order.side.value if hasattr(order.side, 'value') else str(order.side)
        qty = order.qty
        limit_price = order.limit_price if order.limit_price else "Market"
        order_id = order.id
        created_at = order.created_at.strftime('%H:%M:%S') if hasattr(order.created_at, 'strftime') else str(order.created_at)
        
        print(f"â•‘ [{i}] {side.upper():<5} {qty:>4} {symbol:<6} @ ${limit_price:<8} | {created_at}  â•‘")
    
    print(f"â• â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•£")
    print(f"â•‘ [0] Cancel All Orders                                      â•‘")
    print(f"â•‘ [Q] Quit                                                   â•‘")
    print(f"â•šâ•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•\n")
    
    return True

def cancel_order(trading_client: TradingClient, order_id):
    """Cancel a single order by ID"""
    try:
        trading_client.cancel_order_by_id(order_id)
        return True
    except Exception as e:
        print(f"âœ— Error canceling order: {e}")
        return False

def cancel_all_orders(trading_client: TradingClient, orders):
    """Cancel all orders"""
    if not orders:
        print("âœ— No orders to cancel")
        return
    
    cancelled_count = 0
    for order in orders:
        try:
            trading_client.cancel_order_by_id(order.id)
            cancelled_count += 1
            print(f"âœ“ Cancelled: {order.symbol} {order.side.value if hasattr(order.side, 'value') else order.side} {order.qty}")
        except Exception as e:
            print(f"âœ— Error canceling {order.symbol}: {e}")
    
    print(f"\nâœ“ Total cancelled: {cancelled_count} orders\n")

def main():
    """Main menu loop"""
    try:
        trading_client, paper_mode = build_authenticated_client()
    except Exception as exc:
        print(f"\nâœ— Failed to initialize authenticated trading client: {exc}\n")
        return

    print(f"\nâ•”â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•—")
    print(f"â•‘              ORDERS MANAGEMENT MENU                        â•‘")
    print(f"â•‘ Paper Trading: {paper_mode}                                â•‘")
    print(f"â•šâ•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•\n")
    
    while True:
        positions = get_open_positions(trading_client)
        display_positions(positions)

        # Fetch orders
        orders = get_today_orders(trading_client)
        
        # Display orders
        has_orders = display_orders(orders)
        
        if not has_orders:
            # No orders, ask if user wants to quit or try again
            choice = input("Press [R] to refresh or [Q] to quit: ").strip().upper()
            if choice == 'Q':
                print("\nExiting...\n")
                break
            elif choice == 'R':
                continue
            else:
                print("Invalid choice. Please try again.")
                continue
        
        # Get user input
        choice = input("Select order number to cancel (or 0 for all, Q to quit): ").strip().upper()
        
        if choice == 'Q':
            print("\nExiting...\n")
            break
        elif choice == '0':
            # Cancel all orders
            confirm = input("âš   Are you sure? This will cancel ALL open orders. (Y/N): ").strip().upper()
            if confirm == 'Y':
                cancel_all_orders(trading_client, orders)
            else:
                print("Cancelled.\n")
        elif choice.isdigit():
            order_num = int(choice)
            if 1 <= order_num <= len(orders):
                selected_order = orders[order_num - 1]
                symbol = selected_order.symbol
                side = selected_order.side.value if hasattr(selected_order.side, 'value') else selected_order.side
                qty = selected_order.qty
                limit_price = selected_order.limit_price if selected_order.limit_price else "Market"
                
                print(f"\nâœ“ Selected: {side.upper()} {qty} {symbol} @ ${limit_price}")
                confirm = input("Cancel this order? (Y/N): ").strip().upper()
                
                if confirm == 'Y':
                    if cancel_order(trading_client, selected_order.id):
                        print(f"âœ“ Order cancelled successfully\n")
                    else:
                        print(f"âœ— Failed to cancel order\n")
                else:
                    print("Cancelled.\n")
            else:
                print(f"âœ— Invalid order number. Please select 1-{len(orders)}\n")
        else:
            print("âœ— Invalid input. Please enter a number or Q to quit.\n")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nBot stopped by user\n")
        sys.exit(0)

