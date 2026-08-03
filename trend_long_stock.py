import os
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockTradesRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from datetime import datetime, timedelta
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from roles.credentials import bootstrap_trading_auth

try:
    credentials, trading_client = bootstrap_trading_auth("trend_long_stock.py")
except Exception as exc:
    print(f"Authentication failed: {exc}")
    raise SystemExit(1)

PAPER = credentials.paper
data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)

SYMBOL = "AAPL"
QTY = 10
TRADE_THRESHOLD = 50 # Number of trades to form one custom candle
LOOKBACK = 5 # Lookback window for high/low breakout metrics

# 1. Fetch raw trade data to construct custom volume bars
print(f"Fetching trade data for {SYMBOL}...")
end_date = datetime.now()
start_date = end_date - timedelta(days=5) # Pull enough days to reliably form 16+ volume bars

trade_request = StockTradesRequest(
symbol_or_symbols=SYMBOL,
start=start_date,
end=end_date
)
trades_data = data_client.get_stock_trades(trade_request)
df_trades = trades_data.df
print(f"Fetched {len(df_trades)} trades")

# Reset index to clean pandas DataFrame
if isinstance(df_trades.index, pd.MultiIndex):
    df_trades = df_trades.reset_index(level=0, drop=True)

df_trades = df_trades.sort_index()

# 2. Transform raw trades into 500-trade custom candles
# Group rows mathematically into chunks of 500 records
print("Creating custom 50-trade candles...")
df_trades['bar_group'] = range(len(df_trades))
df_trades['bar_group'] = df_trades['bar_group'] // TRADE_THRESHOLD

# Aggregate OHLCV based on trade groups
custom_bars = df_trades.groupby('bar_group').agg(
open=('price', 'first'),
high=('price', 'max'),
low=('price', 'min'),
close=('price', 'last'),
volume=('size', 'sum'),
timestamp=('timestamp', 'first')
).set_index('timestamp')
print(f"Created {len(custom_bars)} custom candles")

# 3. Calculate Breakout Levels (excluding the current live forming candle)
print("Calculating breakout levels...")
custom_bars["Highest_High"] = custom_bars["high"].shift(1).rolling(window=LOOKBACK).max()
custom_bars["Lowest_Low"] = custom_bars["low"].shift(1).rolling(window=LOOKBACK).min()

current_price = custom_bars["close"].iloc[-1]
highest_high = custom_bars["Highest_High"].iloc[-1]
lowest_low = custom_bars["Lowest_Low"].iloc[-1]
print(f"Current Price: {current_price}")
print(f"16-bar High: {highest_high}")
print(f"16-bar Low: {lowest_low}")
# Fetch today's daily candle for high/low
print("\nFetching today's daily data...")
today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
today_end = datetime.now()
daily_request = StockBarsRequest(
    symbol_or_symbols=SYMBOL,
    start=today_start,
    end=today_end,
    timeframe=TimeFrame.Day
)
daily_data = data_client.get_stock_bars(daily_request)
if SYMBOL in daily_data.df.index.get_level_values(0):
    today_bars = daily_data.df.loc[SYMBOL]
    today_high = today_bars['high'].iloc[-1]
    today_low = today_bars['low'].iloc[-1]
    print(f"Today's High: {today_high}")
    print(f"Today's Low: {today_low}")
# 4. Check current open portfolio positions
print("Checking positions...")
positions = trading_client.get_all_positions()
has_position = any(pos.symbol == SYMBOL for pos in positions)
print(f"Has {SYMBOL} position: {has_position}")

# 5. Breakout Execution Logic (Long Only)
if not has_position:
    # Entry condition: Price breaks above the 16-candle highest high
    if current_price > highest_high:
        entry_order = MarketOrderRequest(
            symbol=SYMBOL,
            qty=QTY,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC
        )
        response = trading_client.submit_order(order_data=entry_order)
        print(f"LONG ENTRY: Price ({current_price}) broke above 16-bar High ({highest_high}). Order ID: {response.id}")
    else:
        print(f"No entry signal. Price: {current_price}, 16-bar High target: {highest_high}")

else:
    # Exit condition: Price drops below the 16-candle lowest low
    if current_price < lowest_low:
        response = trading_client.close_position(symbol_or_symbols=SYMBOL)
        print(f"LONG EXIT: Price ({current_price}) broke below 16-bar Low ({lowest_low}). Position closed.")
    else:
        print(f"Holding position. Price: {current_price}, 16-bar Low trail: {lowest_low}")

# 6. Plot candlestick chart
print("\nGenerating chart...")
fig, ax = plt.subplots(figsize=(14, 6))

# Reset custom_bars index for plotting
plot_bars = custom_bars.reset_index()
plot_bars['x'] = range(len(plot_bars))

# Draw candlesticks
width = 0.6
for idx, row in plot_bars.iterrows():
    x = row['x']
    open_price = row['open']
    close_price = row['close']
    high_price = row['high']
    low_price = row['low']
    
    # Color: green if close > open, red otherwise
    color = 'green' if close_price >= open_price else 'red'
    
    # Draw high-low wicks
    ax.plot([x, x], [low_price, high_price], color=color, linewidth=1)
    
    # Draw open-close body
    body_height = abs(close_price - open_price)
    body_bottom = min(open_price, close_price)
    rect = Rectangle((x - width/2, body_bottom), width, body_height, 
                     facecolor=color, edgecolor=color, linewidth=1)
    ax.add_patch(rect)

# Plot 10-bar high/low levels
ax.axhline(y=highest_high, color='blue', linestyle='--', linewidth=2, label=f'10-bar High: ${highest_high:.2f}')
ax.axhline(y=lowest_low, color='orange', linestyle='--', linewidth=2, label=f'10-bar Low: ${lowest_low:.2f}')

# Plot current price
ax.axhline(y=current_price, color='black', linestyle='-', linewidth=2, label=f'Current Price: ${current_price:.2f}')

# Plot today's high/low if available
if 'today_high' in locals():
    ax.axhline(y=today_high, color='cyan', linestyle=':', linewidth=1.5, label=f"Today's High: ${today_high:.2f}")
    ax.axhline(y=today_low, color='magenta', linestyle=':', linewidth=1.5, label=f"Today's Low: ${today_low:.2f}")

# Labels and formatting
ax.set_xlabel('Candle Number', fontsize=12)
ax.set_ylabel('Price ($)', fontsize=12)
ax.set_title(f'{SYMBOL} - Custom Volume Candles (50 trades/candle, 10-candle lookback)', fontsize=14, fontweight='bold')
ax.legend(loc='best', fontsize=10)
ax.grid(True, alpha=0.3)

plt.tight_layout()
plt.show()
