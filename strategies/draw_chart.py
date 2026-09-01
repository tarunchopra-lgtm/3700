#!/usr/bin/env python3
"""Draw a chart showing trend line based on highest highs and check if today's open is above it.

Usage:
    python strategies/draw_chart.py INTC 15         # Analyze INTC with 15-day lookback
    python strategies/draw_chart.py SPY 20          # Analyze SPY with 20-day lookback
    python strategies/draw_chart.py BKR 30          # Analyze BKR with 30-day lookback

The program fetches daily bars for the specified number of days, calculates a downtrend line
based on the highest highs, and displays both an ASCII chart and a detailed table showing
where the trend line is expected and whether today's open is above it.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth

ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class TrendLine:
    """Linear regression trend line."""
    slope: float
    intercept: float
    
    def value_at(self, index: int) -> float:
        return self.intercept + self.slope * index


def _bar_date_et(bar) -> date | None:
    """Extract date in ET timezone from a bar."""
    timestamp = getattr(bar, "timestamp", None)
    if timestamp is None:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(ET).date()


def _fit_trend(values: list[float]) -> TrendLine:
    """Fit a linear regression trend line to the values."""
    count = len(values)
    x_sum = sum(range(count))
    y_sum = sum(values)
    xx_sum = sum(index * index for index in range(count))
    xy_sum = sum(index * value for index, value in enumerate(values))
    denominator = count * xx_sum - x_sum * x_sum
    
    if denominator == 0:
        # Fallback for constant values
        slope = 0.0
        intercept = y_sum / count
    else:
        slope = (count * xy_sum - x_sum * y_sum) / denominator
        intercept = (y_sum - slope * x_sum) / count
    
    return TrendLine(slope=slope, intercept=intercept)


def _extract_bar_map(response) -> dict[str, list]:
    """Extract bars from API response."""
    data = getattr(response, "data", None)
    if data is None and isinstance(response, dict):
        data = response.get("data")
    if not isinstance(data, dict):
        return {}
    return {str(symbol).upper(): list(bars) for symbol, bars in data.items()}


def _fetch_daily_bars(
    data_client: StockHistoricalDataClient,
    symbol: str,
    days: int,
) -> list:
    """Fetch daily bars for the specified number of days."""
    now = datetime.now(timezone.utc)
    response = data_client.get_stock_bars(
        StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=TimeFrame.Day,
            start=now - timedelta(days=days + 5),  # Fetch extra days for buffer
            end=now,
            feed=DataFeed.IEX,
        )
    )
    bars_data = _extract_bar_map(response)
    return bars_data.get(symbol, [])


def _fetch_current_price(
    data_client: StockHistoricalDataClient,
    symbol: str,
) -> float | None:
    """Fetch latest trade price."""
    try:
        response = data_client.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=[symbol])
        )
        if isinstance(response, dict):
            trade = response.get(symbol)
            if trade:
                return float(getattr(trade, "price", 0))
    except Exception:
        pass
    return None


def _draw_ascii_chart(
    symbol: str,
    bars: list,
    trend_line: TrendLine,
    today_open: float | None,
) -> None:
    """Draw ASCII chart showing price bars and trend line."""
    if not bars:
        return
    
    highs = [float(getattr(bar, "high")) for bar in bars]
    lows = [float(getattr(bar, "low")) for bar in bars]
    
    # Find price range
    min_price = min(min(highs), min(lows))
    max_price = max(max(highs), max(lows))
    
    if today_open is not None:
        min_price = min(min_price, today_open)
        max_price = max(max_price, today_open)
    
    price_range = max_price - min_price
    if price_range < 0.01:
        price_range = 1.0
    
    # Chart dimensions
    chart_height = 20
    chart_width = len(bars) + 2
    
    # Build the chart
    lines = []
    for row in range(chart_height, 0, -1):
        price_at_row = min_price + (row / chart_height) * price_range
        line = f"${price_at_row:7.2f} |"
        
        for col, (high, low) in enumerate(zip(highs, lows)):
            trend_val = trend_line.value_at(col)
            
            # Determine what to display
            if low <= price_at_row <= high:
                line += "█"  # In daily range
            elif abs(trend_val - price_at_row) < (price_range / chart_height / 2.5):
                line += "─"  # Trend line
            else:
                line += " "
        
        # Today's open at the end
        if today_open is not None:
            if abs(today_open - price_at_row) < (price_range / chart_height / 2.5):
                line += "O"  # Today's open marker
            else:
                line += " "
        
        lines.append(line)
    
    # Bottom axis
    axis_line = "        +"
    for i in range(len(highs)):
        axis_line += "-"
    if today_open is not None:
        axis_line += "+"
    lines.append(axis_line)
    
    # Day numbers
    day_line = "         "
    for i in range(len(highs)):
        if i % 5 == 0:
            day_line += str(i % 10)
        else:
            day_line += " "
    if today_open is not None:
        day_line += "T"  # Today
    lines.append(day_line)
    
    # Print chart
    print(f"\n{symbol} - Trend Line Analysis")
    print("=" * (chart_width + 12))
    for line in lines:
        print(line)
    print(f"\nLegend: █=Daily range | ─=Trend line | O=Today's open")


def _show_analysis(
    symbol: str,
    bars: list,
    lookback_days: int,
    current_price: float | None = None,
) -> None:
    """Show detailed analysis with table."""
    if not bars:
        print(f"No bars data available for {symbol}")
        return
    
    today_et = datetime.now(ET).date()
    
    # Get bars for the lookback period
    analysis_bars = []
    for bar in reversed(bars):
        bar_date = _bar_date_et(bar)
        if bar_date and bar_date <= today_et:
            analysis_bars.append(bar)
            if len(analysis_bars) == lookback_days:
                break
    
    # Reverse to get chronological order
    analysis_bars = list(reversed(analysis_bars))
    
    if len(analysis_bars) < lookback_days:
        print(f"Warning: Only {len(analysis_bars)} bars available (requested {lookback_days})")
    
    # Calculate trend line from highest highs
    highs = [float(getattr(bar, "high")) for bar in analysis_bars]
    trend_line = _fit_trend(highs)
    
    # Today's data
    today_bars = [bar for bar in bars if _bar_date_et(bar) == today_et]
    today_open = None
    today_high = None
    today_low = None
    
    if today_bars:
        today_bar = today_bars[-1]
        today_open = float(getattr(today_bar, "open"))
        today_high = float(getattr(today_bar, "high"))
        today_low = float(getattr(today_bar, "low"))
    
    # Calculate trend line value for today (at position = lookback_days)
    today_trend_value = trend_line.value_at(lookback_days)
    
    # Print header
    print(f"\n{'='*100}")
    print(f"TREND LINE ANALYSIS: {symbol}")
    print(f"{'='*100}")
    print(f"Lookback Period: {lookback_days} days")
    print(f"Trend Line Slope: {trend_line.slope:.6f}")
    print(f"Trend Line Intercept: {trend_line.intercept:.2f}")
    
    # Print table header
    print(f"\n{'Day':<6} {'Date':<12} {'High':<10} {'Low':<10} {'Trend Val':<12} {'Status':<15}")
    print("-" * 80)
    
    # Print historical bars
    for i, bar in enumerate(analysis_bars):
        bar_date = _bar_date_et(bar)
        high = float(getattr(bar, "high"))
        low = float(getattr(bar, "low"))
        trend_val = trend_line.value_at(i)
        above_trend = high >= trend_val
        status = "✓ Above Trend" if above_trend else "✗ Below Trend"
        print(f"{i:<6} {str(bar_date):<12} ${high:<9.2f} ${low:<9.2f} ${trend_val:<11.2f} {status:<15}")
    
    # Print today's values
    print("-" * 80)
    if today_open is not None:
        today_status = "✓ ABOVE TREND" if today_open >= today_trend_value else "✗ BELOW TREND"
        print(f"{'TODAY':<6} {str(today_et):<12} ${today_high:<9.2f} ${today_low:<9.2f} ${today_trend_value:<11.2f} {today_status:<15}")
        
        # Additional metrics
        print(f"\n{'='*100}")
        print(f"TODAY'S SUMMARY")
        print(f"{'='*100}")
        print(f"Today's Open:              ${today_open:.2f}")
        print(f"Today's High:              ${today_high:.2f}")
        print(f"Today's Low:               ${today_low:.2f}")
        print(f"Expected Trend Value:      ${today_trend_value:.2f}")
        print(f"Distance from Trend:       ${today_open - today_trend_value:+.2f} ({(today_open - today_trend_value) / today_trend_value * 100:+.2f}%)")
        print(f"Above Trend Line:          {'✓ YES' if today_open >= today_trend_value else '✗ NO'}")
        
        if current_price is not None:
            print(f"Current Price:             ${current_price:.2f}")
    else:
        print("No data available for today")
    
    print(f"{'='*100}\n")
    
    # Draw chart
    _draw_ascii_chart(symbol, analysis_bars, trend_line, today_open)


def main() -> int:
    """Main entry point."""
    if len(sys.argv) < 3:
        print("Usage: python draw_chart.py <SYMBOL> <DAYS_LOOKBACK>")
        print("\nExamples:")
        print("  python draw_chart.py INTC 15")
        print("  python draw_chart.py SPY 20")
        print("  python draw_chart.py BKR 30")
        return 1
    
    symbol = sys.argv[1].upper()
    try:
        lookback_days = int(sys.argv[2])
        if lookback_days < 2:
            print("Error: DAYS_LOOKBACK must be at least 2")
            return 1
    except ValueError:
        print(f"Error: DAYS_LOOKBACK must be a number, got '{sys.argv[2]}'")
        return 1
    
    try:
        credentials, _ = bootstrap_trading_auth("draw_chart.py")
        data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
        
        # Fetch bars
        bars = _fetch_daily_bars(data_client, symbol, lookback_days)
        if not bars:
            print(f"No data available for {symbol}")
            return 1
        
        # Fetch current price
        current_price = _fetch_current_price(data_client, symbol)
        
        # Show analysis
        _show_analysis(symbol, bars, lookback_days, current_price)
        return 0
    
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
