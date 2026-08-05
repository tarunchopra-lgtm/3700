#!/usr/bin/env python3
"""
Trend Plot Tool - Volume-based candle analysis from trades

Converts individual trades into volume-based candles. Each candle forms when a
specified volume threshold is reached. Default: 1% of average daily volume.

Fetches last 6 hours of trades for speed (enough data for 50-100+ candles).

Usage: python trend_plot.py <SYMBOL> [VOLUME_PER_CANDLE] [LOOKBACK_CANDLES] [TREND_BARS]

Arguments (all optional except SYMBOL):
    SYMBOL: Ticker symbol (e.g., BTC/USD, AAPL) - REQUIRED
    VOLUME_PER_CANDLE: Volume threshold per candle (default: 1% of avg daily volume)
    LOOKBACK_CANDLES: Number of most recent candles to analyze (default: ~50)
    TREND_BARS: Number of bars for trend line (default: 10)

Examples:
    python trend_plot.py MU                    # Uses all defaults (~50 candles)
    python trend_plot.py BTC/USD 1000000       # Custom volume, other defaults
    python trend_plot.py AAPL 50000 100 15     # All custom values
    
Note: Fetches last 6 hours of trades. Each candle represents actual volume threshold.
"""

import sys
import pandas as pd
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from alpaca.data.requests import StockTradesRequest, CryptoTradesRequest, StockBarsRequest, CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame
from roles.base import BaseRole


class CustomTrendAnalyzer(BaseRole):
    """Customizable trend analysis with configurable parameters"""
    
    def __init__(self, symbol, volume_per_candle=None, lookback_candles=None, lookback_bars=10):
        super().__init__(symbol)
        self.lookback_bars = lookback_bars
        
        # Calculate defaults if not provided
        if volume_per_candle is None or lookback_candles is None:
            avg_daily_vol = self.get_average_daily_volume()
            if volume_per_candle is None:
                volume_per_candle = max(int(avg_daily_vol * 0.01), 100)  # 1% of avg daily vol
            if lookback_candles is None:
                lookback_candles = 50  # Reasonable default for 2 days of candles
        
        self.volume_per_candle = volume_per_candle
        self.lookback_candles = lookback_candles
    
    def get_average_daily_volume(self):
        """Calculate 10-day average daily volume"""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Calculating average daily volume...")
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=10)
        
        try:
            if self.is_crypto:
                request = CryptoBarsRequest(
                    symbol_or_symbols=self.symbol,
                    start=start_date,
                    end=end_date,
                    timeframe=TimeFrame.Day
                )
                bars_data = self.data_client.get_crypto_bars(request)
            else:
                request = StockBarsRequest(
                    symbol_or_symbols=self.symbol,
                    start=start_date,
                    end=end_date,
                    timeframe=TimeFrame.Day
                )
                bars_data = self.data_client.get_stock_bars(request)
            
            df = bars_data.df
            if isinstance(df.index, pd.MultiIndex):
                df = df.reset_index(level=0, drop=True)
            
            avg_vol = df['volume'].mean()
            print(f"✓ Average daily volume: {avg_vol:,.0f}")
            return avg_vol
        except Exception as e:
            print(f"⚠ Could not calculate average daily volume: {e}")
            return 10000  # Fallback default
    
    def get_volume_candles(self):
        """Fetch trades and convert to volume-based candles (last 6 hours for speed)"""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching trade data for {self.symbol}...")
        
        end_date = datetime.now()
        start_date = end_date - timedelta(hours=6)  # Last 6 hours only (much faster)
        
        try:
            if self.is_crypto:
                request = CryptoTradesRequest(
                    symbol_or_symbols=self.symbol,
                    start=start_date,
                    end=end_date
                )
                trades_data = self.data_client.get_crypto_trades(request)
            else:
                request = StockTradesRequest(
                    symbol_or_symbols=self.symbol,
                    start=start_date,
                    end=end_date
                )
                trades_data = self.data_client.get_stock_trades(request)
            
            df = trades_data.df
            print(f"✓ Fetched {len(df):,} trades")
            
            if len(df) == 0:
                print("✗ No trade data available")
                return None
            
            # Reset index if needed
            if isinstance(df.index, pd.MultiIndex):
                df = df.reset_index(level=0, drop=True)
            df = df.sort_index()
            
            # Convert to volume-based candles
            candles = []
            candle_data = {
                'open': None,
                'high': None,
                'low': None,
                'close': None,
                'volume': 0,
                'timestamp': None
            }
            
            for idx, row in df.iterrows():
                price = row['price']
                volume = row['size']
                
                if candle_data['open'] is None:
                    candle_data['open'] = price
                    candle_data['timestamp'] = idx
                
                candle_data['high'] = max(candle_data['high'] or price, price)
                candle_data['low'] = min(candle_data['low'] or price, price)
                candle_data['close'] = price
                candle_data['volume'] += volume
                
                # Complete candle when volume threshold reached
                if candle_data['volume'] >= self.volume_per_candle:
                    candles.append(candle_data.copy())
                    candle_data = {
                        'open': None,
                        'high': None,
                        'low': None,
                        'close': None,
                        'volume': 0,
                        'timestamp': None
                    }
            
            # Add remaining partial candle if exists
            if candle_data['open'] is not None:
                candles.append(candle_data)
            
            df_candles = pd.DataFrame(candles)
            print(f"✓ Created {len(df_candles)} volume candles ({self.volume_per_candle:,} volume each)")
            print(f"  Data window: {start_date.strftime('%H:%M')} - {end_date.strftime('%H:%M')}")
            
            # Keep only last N candles
            if len(df_candles) > self.lookback_candles:
                df_candles = df_candles.tail(self.lookback_candles).reset_index(drop=True)
                print(f"✓ Keeping last {self.lookback_candles} candles")
            
            return df_candles
        
        except Exception as e:
            print(f"✗ Error fetching data: {e}")
            return None
    
    def get_trend_line(self, df_candles):
        """Calculate trend line using highest high of last N candles"""
        if len(df_candles) < self.lookback_bars:
            print(f"⚠ Not enough candles ({len(df_candles)} < {self.lookback_bars})")
            return None
        
        # Get last N candles and their highest highs
        last_candles = df_candles.tail(self.lookback_bars).copy()
        last_candles['candle_num'] = range(len(last_candles))
        
        # Create trend line points (highest high of each candle)
        trend_points = last_candles[['candle_num', 'high']].values
        
        # Simple linear regression to create trend line
        x = trend_points[:, 0].astype(float)
        y = trend_points[:, 1].astype(float)
        
        # Calculate slope and intercept
        n = len(x)
        slope = (n * (x * y).sum() - x.sum() * y.sum()) / (n * (x ** 2).sum() - (x.sum() ** 2))
        intercept = (y.sum() - slope * x.sum()) / n
        
        print(f"✓ Trend line calculated (slope: {slope:.6f}, intercept: {intercept:.2f})")
        
        return {
            'slope': slope,
            'intercept': intercept,
            'last_candles': last_candles,
            'trend_points': trend_points
        }
    
    def detect_breakout(self, df_candles, trend_line_data):
        """Detect if latest candle breaks out above trend line"""
        if trend_line_data is None:
            return None
        
        last_candle = df_candles.iloc[-1]
        last_candles = trend_line_data['last_candles']
        
        # Calculate trend line value at current position
        current_x = len(last_candles) - 1
        trend_value = trend_line_data['slope'] * current_x + trend_line_data['intercept']
        
        # Check if candle closed above trend line
        breakout = last_candle['close'] > trend_value
        
        return {
            'breakout': breakout,
            'current_price': last_candle['close'],
            'trend_value': trend_value,
            'distance': last_candle['close'] - trend_value,
            'last_candle': last_candle
        }
    
    def plot_chart(self, df_candles, trend_line_data, breakout_data):
        """Draw candlestick chart with trend line"""
        if trend_line_data is None or breakout_data is None:
            print("Cannot plot: insufficient data")
            return
        
        last_candles = trend_line_data['last_candles']
        
        fig, ax = plt.subplots(figsize=(14, 7))
        
        # Plot candlesticks from the last lookback_bars candles
        for i, row in last_candles.iterrows():
            x = row['candle_num']
            open_price = row['open']
            close_price = row['close']
            high = row['high']
            low = row['low']
            
            # Wick color
            color = 'green' if close_price >= open_price else 'red'
            
            # Draw wick
            ax.plot([x, x], [low, high], color=color, linewidth=1)
            
            # Draw body
            height = abs(close_price - open_price)
            y = min(open_price, close_price)
            rect = Rectangle((x - 0.3, y), 0.6, height, 
                            facecolor=color, edgecolor=color, linewidth=1, alpha=0.8)
            ax.add_patch(rect)
        
        # Plot trend line
        last_candles_data = trend_line_data['last_candles']
        xs = last_candles_data['candle_num'].values
        ys = trend_line_data['slope'] * xs + trend_line_data['intercept']
        ax.plot(xs, ys, 'b--', linewidth=2, label='Trend Line', alpha=0.7)
        
        # Plot trend line extended to current price level
        current_x = len(last_candles) - 1
        trend_at_current = trend_line_data['slope'] * current_x + trend_line_data['intercept']
        ax.axhline(y=trend_at_current, color='blue', linestyle='--', linewidth=1, alpha=0.5)
        
        # Highlight current price
        current_price = breakout_data['current_price']
        ax.axhline(y=current_price, color='orange', linestyle='-', linewidth=2, label=f'Current: ${current_price:.2f}')
        
        # Add breakout zone
        if breakout_data['breakout']:
            ax.fill_between(xs, trend_at_current, current_price, alpha=0.2, color='green', label='Breakout Zone')
            ax.text(len(last_candles) - 1, current_price, f" BREAKOUT +${breakout_data['distance']:.2f}", 
                   fontsize=10, color='green', fontweight='bold', va='bottom')
        else:
            ax.fill_between(xs, current_price, trend_at_current, alpha=0.2, color='red', label='Below Trend')
            ax.text(len(last_candles) - 1, current_price, f" Below -${abs(breakout_data['distance']):.2f}", 
                   fontsize=10, color='red', fontweight='bold', va='top')
        
        # Labels and title
        ax.set_xlabel('Candle #', fontsize=11)
        ax.set_ylabel('Price ($)', fontsize=11)
        ax.set_title(f'{self.symbol} - Volume Candle Trend Analysis\n(Last {len(df_candles)} candles | {self.volume_per_candle:,} vol/candle | Trend: {self.lookback_bars} bars)', fontsize=12, fontweight='bold')
        ax.legend(loc='upper left', fontsize=10)
        ax.grid(True, alpha=0.3)
        
        plt.tight_layout()
        plt.show()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    symbol = sys.argv[1].upper()
    
    # Parse optional arguments
    volume_per_candle = None
    lookback_candles = None
    trend_bars = 10  # Default
    
    if len(sys.argv) > 2:
        try:
            volume_per_candle = int(sys.argv[2])
        except ValueError:
            print(f"Error: VOLUME_PER_CANDLE must be an integer")
            sys.exit(1)
    
    if len(sys.argv) > 3:
        try:
            lookback_candles = int(sys.argv[3])
        except ValueError:
            print(f"Error: LOOKBACK_CANDLES must be an integer")
            sys.exit(1)
    
    if len(sys.argv) > 4:
        try:
            trend_bars = int(sys.argv[4])
        except ValueError:
            print(f"Error: TREND_BARS must be an integer")
            sys.exit(1)
    
    print(f"\n{'='*60}")
    print(f"TREND PLOT ANALYSIS - VOLUME-BASED CANDLES")
    print(f"{'='*60}")
    print(f"Symbol: {symbol}")
    
    # Create analyzer (will calculate defaults internally)
    analyzer = CustomTrendAnalyzer(symbol, volume_per_candle=volume_per_candle, 
                                   lookback_candles=lookback_candles, lookback_bars=trend_bars)
    
    print(f"Volume per Candle: {analyzer.volume_per_candle:,} shares {'(1% avg daily vol)' if volume_per_candle is None else ''}")
    print(f"Lookback Candles: {analyzer.lookback_candles} {'(2 days)' if lookback_candles is None else ''}")
    print(f"Trend Line Bars: {analyzer.lookback_bars}")
    print(f"{'='*60}\n")
    
    # Get candles
    df_candles = analyzer.get_volume_candles()
    
    if df_candles is None or len(df_candles) == 0:
        print("✗ No candle data available")
        sys.exit(1)
    
    # Validate we have enough candles
    if len(df_candles) < analyzer.lookback_bars:
        print(f"✗ Not enough candles ({len(df_candles)} < {analyzer.lookback_bars} required for trend line)")
        sys.exit(1)
    
    # Calculate trend line
    trend_line_data = analyzer.get_trend_line(df_candles)
    
    # Detect breakout
    breakout_data = analyzer.detect_breakout(df_candles, trend_line_data)
    
    # Print analysis
    if breakout_data:
        print(f"\n{'='*60}")
        print("BREAKOUT ANALYSIS")
        print(f"{'='*60}")
        print(f"Current Price: ${breakout_data['current_price']:.2f}")
        print(f"Trend Line Value: ${breakout_data['trend_value']:.2f}")
        print(f"Distance: ${breakout_data['distance']:.4f}")
        print(f"Status: {'✓ BREAKOUT ABOVE TREND' if breakout_data['breakout'] else '✗ BELOW TREND LINE'}")
        
        if breakout_data['breakout']:
            print(f"\n→ Breakout: Price is ${breakout_data['distance']:.4f} ABOVE trend line")
            print(f"  If candle opens/closes above ${breakout_data['trend_value']:.2f}, breakout confirmed")
        else:
            print(f"\n→ Below Trend: Price needs to rise ${abs(breakout_data['distance']):.4f} to break above trend")
            print(f"  Breakout occurs if price closes above ${breakout_data['trend_value']:.2f}")
        
        print(f"{'='*60}\n")
    
    # Plot chart
    print("Generating chart...")
    analyzer.plot_chart(df_candles, trend_line_data, breakout_data)


if __name__ == '__main__':
    main()
