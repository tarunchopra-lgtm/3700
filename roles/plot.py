"""Plot role - draws charts with trend lines"""
import sys
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from .trend import TrendRole


class PlotRole(TrendRole):
    """Plots charts with trend lines and candles"""
    
    def plot_chart(self):
        """Draw candlestick chart with trend line"""
        print(f"\nGenerating chart for {self.symbol}...")
        
        # Get trend analysis
        analysis = self.analyze()
        df_candles = analysis['candles']
        trend_line_data = analysis['trend_line']
        breakout = analysis['breakout']
        stop_loss = analysis['stop_loss']
        
        # Create figure
        fig, ax = plt.subplots(figsize=(16, 8))
        
        # Draw candlesticks
        width = 0.6
        for i, (idx, row) in enumerate(df_candles.iterrows()):
            open_price = row['open']
            close_price = row['close']
            high_price = row['high']
            low_price = row['low']
            
            # Color: green if close > open, red otherwise
            color = 'green' if close_price >= open_price else 'red'
            
            # Draw high-low wicks
            ax.plot([i, i], [low_price, high_price], color=color, linewidth=1)
            
            # Draw open-close body
            body_height = abs(close_price - open_price)
            body_bottom = min(open_price, close_price)
            rect = Rectangle(
                (i - width/2, body_bottom), width, body_height,
                facecolor=color, edgecolor=color, linewidth=1
            )
            ax.add_patch(rect)
        
        # Draw trend line (last 20 candles)
        if trend_line_data:
            last_20 = trend_line_data['last_20_candles']
            start_idx = len(df_candles) - len(last_20)
            
            trend_x = []
            trend_y = []
            for i, (idx, row) in enumerate(last_20.iterrows()):
                x_pos = start_idx + i
                y_pos = trend_line_data['slope'] * i + trend_line_data['intercept']
                trend_x.append(x_pos)
                trend_y.append(y_pos)
            
            ax.plot(trend_x, trend_y, 'b-', linewidth=2, label='Trend Line (20 candles)', alpha=0.7)
        
        # Draw stop loss level
        if stop_loss:
            ax.axhline(y=stop_loss, color='red', linestyle='--', linewidth=2, 
                       label=f'Stop Loss: ${stop_loss:.2f}')
        
        # Draw current price
        current_price = df_candles.iloc[-1]['close']
        ax.axhline(y=current_price, color='black', linestyle='-', linewidth=2,
                   label=f'Current Price: ${current_price:.2f}')
        
        # Highlight trend line value at latest candle
        if trend_line_data:
            latest_trend = trend_line_data['slope'] * (len(last_20) - 1) + trend_line_data['intercept']
            ax.axhline(y=latest_trend, color='blue', linestyle=':', linewidth=1.5,
                       label=f'Trend Value: ${latest_trend:.2f}')
        
        # Labels
        ax.set_xlabel('Candle Number', fontsize=12)
        ax.set_ylabel('Price ($)', fontsize=12)
        title = f"{self.symbol} - Volume Candles ({self.VOLUME_PER_CANDLE} vol/candle)"
        if breakout['breakout']:
            title += " ✓ BREAKOUT DETECTED"
        ax.set_title(title, fontsize=14, fontweight='bold')
        ax.legend(loc='best', fontsize=10)
        ax.grid(True, alpha=0.3)
        
        plt.tight_layout()
        plt.show()
    
    def print_analysis(self):
        """Print analysis to console"""
        analysis = self.analyze()
        breakout = analysis['breakout']
        stop_loss = analysis['stop_loss']
        
        print(f"\n{'='*60}")
        print(f"TREND ANALYSIS for {self.symbol}")
        print(f"{'='*60}")
        print(f"Current Price: ${breakout['current_price']:.2f}")
        print(f"Trend Line Value: ${breakout['trend_value']:.2f}")
        print(f"Distance from Trend: ${breakout['distance']:.2f}")
        print(f"\nBreakout Status: {'✓ YES' if breakout['breakout'] else '✗ NO'}")
        if stop_loss:
            print(f"Stop Loss Level: ${stop_loss:.2f}")
        print(f"{'='*60}\n")


def main():
    """Run as standalone: python -m roles.plot SYMBOL"""
    if len(sys.argv) < 2:
        print("Usage: python -m roles.plot <SYMBOL>")
        print("Example: python -m roles.plot BTC/USD")
        sys.exit(1)
    
    symbol = sys.argv[1].upper()
    
    plot = PlotRole(symbol)
    plot.print_analysis()
    plot.plot_chart()


if __name__ == '__main__':
    main()
