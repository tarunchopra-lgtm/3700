#!/usr/bin/env python3
"""
Stock Chart Plotter - Interactive command-line tool

Usage: python plot.py [SYMBOL] [CHART_TYPE] [LOOKBACK_CANDLES] [VOLUME_PER_CANDLE]

Arguments:
    SYMBOL: Stock ticker (default: MU)
    CHART_TYPE: 'daily' for daily bars, 'vol' for volume-based candles (default: daily)
    LOOKBACK_CANDLES: Number of candles to display (default: 14)
    VOLUME_PER_CANDLE: Volume threshold per candle - required if chart_type='vol'

Examples:
    python plot.py                              # MU daily, 14 candles
    python plot.py AAPL                         # AAPL daily, 14 candles
    python plot.py TSLA daily 20                # TSLA daily, 20 candles
    python plot.py MU vol 50 1000               # MU volume candles, 50 candles, 1000 vol each
"""

import sys
from roles.plot_function import plot_stock_chart

def main():
    # Parse arguments with defaults
    symbol = 'MU'
    chart_type = 'daily'
    lookback_candles = 14
    volume_per_candle = None
    
    if len(sys.argv) > 1:
        symbol = sys.argv[1].upper()
    
    if len(sys.argv) > 2:
        chart_type = sys.argv[2].lower()
        if chart_type not in ['daily', 'vol']:
            print(f"Error: chart_type must be 'daily' or 'vol', got '{chart_type}'")
            print(__doc__)
            sys.exit(1)
    
    if len(sys.argv) > 3:
        try:
            lookback_candles = int(sys.argv[3])
        except ValueError:
            print(f"Error: LOOKBACK_CANDLES must be an integer")
            print(__doc__)
            sys.exit(1)
    
    if chart_type == 'vol':
        if len(sys.argv) < 5:
            print(f"Error: VOLUME_PER_CANDLE required for 'vol' chart type")
            print(__doc__)
            sys.exit(1)
        try:
            volume_per_candle = int(sys.argv[4])
        except ValueError:
            print(f"Error: VOLUME_PER_CANDLE must be an integer")
            print(__doc__)
            sys.exit(1)
    
    # Call the plot function
    try:
        plot_stock_chart(
            symbol=symbol,
            chart_type=chart_type,
            lookback_candles=lookback_candles,
            volume_per_candle=volume_per_candle
        )
    except Exception as e:
        print(f"\n✗ Error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
