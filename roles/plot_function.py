#!/usr/bin/env python3
"""
Plot Function Module - Reusable trend plotting for stocks

This module provides the plot_stock_chart function that can be imported and used
by other programs to visualize stock data with trend lines.
"""

import os
import pandas as pd
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from alpaca.data.requests import StockBarsRequest, StockTradesRequest
from alpaca.data.timeframe import TimeFrame
from roles.base import BaseRole


class StockPlotter(BaseRole):
    """Plotter class for stock analysis and visualization"""
    
    def __init__(self, symbol):
        super().__init__(symbol)
    
    def get_daily_bars(self, lookback_candles=14):
        """Fetch daily bars"""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching daily bars for {self.symbol}...")
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=lookback_candles + 5)
        
        try:
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
            df = df.sort_index()
            
            # Keep last lookback_candles
            if len(df) > lookback_candles:
                df = df.tail(lookback_candles).reset_index(drop=True)
            
            print(f"✓ Fetched {len(df)} daily bars")
            return df
        
        except Exception as e:
            print(f"✗ Error fetching daily bars: {e}")
            return None
    
    def get_volume_candles(self, volume_per_candle, lookback_candles=14):
        """Fetch trades and convert to volume-based candles"""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching trade data for {self.symbol}...")
        
        end_date = datetime.now()
        start_date = end_date - timedelta(hours=6)
        
        try:
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
                
                if candle_data['volume'] >= volume_per_candle:
                    candles.append(candle_data.copy())
                    candle_data = {
                        'open': None,
                        'high': None,
                        'low': None,
                        'close': None,
                        'volume': 0,
                        'timestamp': None
                    }
            
            if candle_data['open'] is not None:
                candles.append(candle_data)
            
            df_candles = pd.DataFrame(candles)
            print(f"✓ Created {len(df_candles)} volume candles ({volume_per_candle:,} volume each)")
            
            # Keep only last N candles
            if len(df_candles) > lookback_candles:
                df_candles = df_candles.tail(lookback_candles).reset_index(drop=True)
            
            return df_candles
        
        except Exception as e:
            print(f"✗ Error fetching trade data: {e}")
            return None
    
    def get_yesterday_close(self):
        """Get yesterday's closing price"""
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=5)
            
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
            df = df.sort_index()
            
            if len(df) >= 2:
                return float(df.iloc[-2]['close'])
            return None
        except:
            return None
    
    def calculate_trend_lines(self, df):
        """Calculate highest high and lowest low trend lines"""
        if df is None or len(df) < 2:
            return None
        
        # Get highest high and lowest low for each candle
        df['candle_num'] = range(len(df))
        
        # Highest high trend line (linear regression on highs)
        x = df['candle_num'].values.astype(float)
        y_high = df['high'].values.astype(float)
        y_low = df['low'].values.astype(float)
        
        # Linear regression for highest high
        n = len(x)
        slope_high = (n * (x * y_high).sum() - x.sum() * y_high.sum()) / (n * (x ** 2).sum() - (x.sum() ** 2))
        intercept_high = (y_high.sum() - slope_high * x.sum()) / n
        
        # Linear regression for lowest low
        slope_low = (n * (x * y_low).sum() - x.sum() * y_low.sum()) / (n * (x ** 2).sum() - (x.sum() ** 2))
        intercept_low = (y_low.sum() - slope_low * x.sum()) / n
        
        return {
            'high_trend': {'slope': slope_high, 'intercept': intercept_high},
            'low_trend': {'slope': slope_low, 'intercept': intercept_low},
            'highs': y_high,
            'lows': y_low,
            'x': x
        }
    
    def plot_chart(self, df, chart_type='daily', filename=None):
        """Draw candlestick chart with trend lines"""
        if df is None or len(df) == 0:
            print("Cannot plot: no data")
            return
        
        # Reset index to ensure numeric indexing
        df = df.reset_index(drop=True)
        
        # Calculate trend lines
        trend_data = self.calculate_trend_lines(df)
        if trend_data is None:
            print("Cannot plot: insufficient data")
            return
        
        # Get yesterday's close
        yesterday_close = self.get_yesterday_close()
        
        fig, ax = plt.subplots(figsize=(14, 7))
        
        # Plot candlesticks
        for idx, row in df.iterrows():
            x = float(idx)  # Ensure x is numeric
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
        
        # Plot trend lines
        x_vals = trend_data['x']
        y_high = trend_data['high_trend']['slope'] * x_vals + trend_data['high_trend']['intercept']
        y_low = trend_data['low_trend']['slope'] * x_vals + trend_data['low_trend']['intercept']
        
        ax.plot(x_vals, y_high, 'b-', linewidth=2, label='Highest High Trend', alpha=0.7)
        ax.plot(x_vals, y_low, 'r-', linewidth=2, label='Lowest Low Trend', alpha=0.7)
        
        # Plot yesterday's close
        if yesterday_close:
            ax.axhline(y=yesterday_close, color='orange', linestyle='--', linewidth=2, label=f"Yesterday's Close: ${yesterday_close:.2f}")
        
        # Current price
        current_price = df.iloc[-1]['close']
        ax.axhline(y=current_price, color='purple', linestyle=':', linewidth=2, label=f'Current: ${current_price:.2f}')
        
        # Labels and title
        ax.set_xlabel('Candle #', fontsize=11)
        ax.set_ylabel('Price ($)', fontsize=11)
        
        if chart_type == 'vol':
            title = f'{self.symbol} - Volume-Based Candles ({len(df)} candles)\n'
        else:
            title = f'{self.symbol} - Daily Chart ({len(df)} candles)\n'
        
        title += f'High Trend: y = {trend_data["high_trend"]["slope"]:.6f}x + {trend_data["high_trend"]["intercept"]:.2f}'
        title += f' | Low Trend: y = {trend_data["low_trend"]["slope"]:.6f}x + {trend_data["low_trend"]["intercept"]:.2f}'
        
        ax.set_title(title, fontsize=12, fontweight='bold')
        ax.legend(loc='upper left', fontsize=10)
        ax.grid(True, alpha=0.3)
        
        plt.tight_layout()
        
        # Save figure
        if filename:
            try:
                plt.savefig(filename, dpi=100, bbox_inches='tight')
                print(f"✓ Chart saved to {filename}")
            except Exception as e:
                print(f"⚠ Could not save chart: {e}")
        
        plt.show()


def plot_stock_chart(symbol='MU', chart_type='daily', lookback_candles=14, volume_per_candle=None):
    """
    Main function to plot stock chart
    
    Args:
        symbol: Stock ticker (default: MU)
        chart_type: 'daily' for daily bars, 'vol' for volume-based candles (default: daily)
        lookback_candles: Number of candles to display (default: 14)
        volume_per_candle: Volume threshold for each candle (required if chart_type='vol')
    """
    print(f"\n{'='*60}")
    print(f"STOCK CHART PLOTTER")
    print(f"{'='*60}")
    print(f"Symbol: {symbol}")
    print(f"Chart Type: {chart_type.upper()}")
    print(f"Lookback Candles: {lookback_candles}")
    if chart_type == 'vol':
        print(f"Volume per Candle: {volume_per_candle:,}")
    print(f"{'='*60}\n")
    
    # Create plotter
    plotter = StockPlotter(symbol)
    
    # Get data
    if chart_type == 'vol':
        if volume_per_candle is None:
            print("✗ Error: volume_per_candle required for 'vol' chart type")
            return
        df = plotter.get_volume_candles(volume_per_candle, lookback_candles)
    else:
        df = plotter.get_daily_bars(lookback_candles)
    
    if df is None or len(df) == 0:
        print("✗ No data available")
        return
    
    # Generate filename
    today = datetime.now().strftime('%Y%m%d')
    filename = f"{symbol.replace('/', '_')}_{chart_type}_{today}.png"
    filepath = os.path.join(os.path.dirname(__file__), filename)
    
    # Print statistics
    print(f"Data Summary:")
    print(f"  Latest Close: ${df.iloc[-1]['close']:.2f}")
    print(f"  Highest High: ${df['high'].max():.2f}")
    print(f"  Lowest Low: ${df['low'].min():.2f}")
    yesterday_close = plotter.get_yesterday_close()
    if yesterday_close:
        print(f"  Yesterday Close: ${yesterday_close:.2f}")
    print()
    
    # Plot
    plotter.plot_chart(df, chart_type=chart_type, filename=filepath)
