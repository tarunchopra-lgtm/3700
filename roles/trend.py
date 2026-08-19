"""Trend analysis role - detects breakouts and trend lines"""
import pandas as pd
from datetime import datetime, timedelta
from alpaca.data.enums import DataFeed
from alpaca.data.requests import StockTradesRequest, CryptoTradesRequest
from .base import BaseRole


class TrendRole(BaseRole):
    """Analyzes trends and detects breakouts"""
    
    VOLUME_PER_CANDLE = 200  # Volume threshold per candle (lowered for more granularity)
    LOOKBACK_CANDLES = 10    # Last 10 candles for trend line
    
    def __init__(self, symbol):
        super().__init__(symbol)
    
    def get_volume_candles(self, lookback_days=7):
        """Fetch trades and convert to volume-based candles"""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching trade data for {self.symbol}...")
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=lookback_days)
        
        if self.is_crypto:
            from alpaca.data.requests import CryptoTradesRequest
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
                end=end_date,
                feed=DataFeed.IEX,
            )
            trades_data = self.data_client.get_stock_trades(request)
        
        df = trades_data.df
        print(f"✓ Fetched {len(df)} trades")
        
        # Reset index if needed
        if isinstance(df.index, pd.MultiIndex):
            df = df.reset_index(level=0, drop=True)
        df = df.sort_index()
        
        # Convert to volume-based candles
        candles = []
        current_volume = 0
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
            if candle_data['volume'] >= self.VOLUME_PER_CANDLE:
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
        print(f"✓ Created {len(df_candles)} volume candles ({self.VOLUME_PER_CANDLE} volume each)")
        
        return df_candles
    
    def get_trend_line(self, df_candles):
        """Calculate trend line using highest high of last 20 candles"""
        if len(df_candles) < self.LOOKBACK_CANDLES:
            print(f"⚠ Not enough candles ({len(df_candles)} < {self.LOOKBACK_CANDLES})")
            return None
        
        # Get last 20 candles and their highest highs
        last_20 = df_candles.tail(self.LOOKBACK_CANDLES).copy()
        last_20['candle_num'] = range(len(last_20))
        
        # Create trend line points (highest high of each candle)
        trend_points = last_20[['candle_num', 'high']].values
        
        # Simple linear regression to create trend line
        x = trend_points[:, 0].astype(float)
        y = trend_points[:, 1].astype(float)
        
        # Calculate slope and intercept
        n = len(x)
        slope = (n * (x * y).sum() - x.sum() * y.sum()) / (n * (x ** 2).sum() - (x.sum() ** 2))
        intercept = (y.sum() - slope * x.sum()) / n
        
        print(f"✓ Trend line calculated (slope: {slope:.6f})")
        
        return {
            'slope': slope,
            'intercept': intercept,
            'last_20_candles': last_20,
            'trend_points': trend_points
        }
    
    def detect_breakout(self, df_candles, trend_line_data):
        """Detect if latest candle breaks out above trend line"""
        if trend_line_data is None:
            return {'breakout': False, 'reason': 'No trend line'}
        
        last_candle = df_candles.iloc[-1]
        last_20 = trend_line_data['last_20_candles']
        
        # Calculate trend line value at current position
        current_x = len(last_20) - 1  # Position of latest candle in trend
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
    
    def get_stop_loss_level(self, df_candles, trend_line_data):
        """Get stop loss: lowest low of last candle that was below trend line"""
        if trend_line_data is None:
            return None
        
        last_20 = trend_line_data['last_20_candles']
        
        lowest_below_trend = None
        for i, (idx, row) in enumerate(last_20.iterrows()):
            trend_value = trend_line_data['slope'] * i + trend_line_data['intercept']
            if row['close'] <= trend_value:  # Candle below trend line
                if lowest_below_trend is None or row['low'] < lowest_below_trend:
                    lowest_below_trend = row['low']
        
        return lowest_below_trend
    
    def analyze(self):
        """Run complete trend analysis"""
        df_candles = self.get_volume_candles()
        trend_line = self.get_trend_line(df_candles)
        breakout = self.detect_breakout(df_candles, trend_line)
        stop_loss = self.get_stop_loss_level(df_candles, trend_line)
        
        return {
            'candles': df_candles,
            'trend_line': trend_line,
            'breakout': breakout,
            'stop_loss': stop_loss
        }
