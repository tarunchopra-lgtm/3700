#!/usr/bin/env python3
"""Close positions for MU and INTC"""

from roles.credentials import CredentialsRole
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus

# Setup credentials
creds = CredentialsRole()
trading_client = creds.build_trading_client()

# Get all open orders
orders = list(trading_client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500)))
print(f'Found {len(orders)} open orders\n')

# Cancel MU and INTC orders
for order in orders:
    symbol = order.symbol
    if symbol in ['MU', 'INTC']:
        print(f'Canceling {symbol}: {order.id} - {order.side} {order.qty} @ {order.limit_price}')
        try:
            trading_client.cancel_order(order.id)
            print(f'  ✓ Canceled\n')
        except Exception as e:
            print(f'  ✗ Error: {e}\n')

# Get all open positions
positions = trading_client.get_all_positions()
print(f'\nFound {len(positions)} open position(s)\n')

for pos in positions:
    symbol = pos.symbol
    if symbol in ['MU', 'INTC']:
        print(f'Closing {symbol}: {pos.qty} shares at market')
        try:
            # Submit market sell order
            sell_order = MarketOrderRequest(
                symbol=symbol,
                qty=float(pos.qty),
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY
            )
            response = trading_client.submit_order(sell_order)
            print(f'  ✓ Market sell order submitted: {response.id}\n')
        except Exception as e:
            print(f'  ✗ Error: {e}\n')

print("Done!")
