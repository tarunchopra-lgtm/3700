#!/usr/bin/env python3
"""Debug script to analyze why find_daily_trend is giving false positives."""

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth
from strategies.long_trend import ENTRY_LOOKBACK, _first_cent_above, _fit_trend

ET = ZoneInfo("America/New_York")
HISTORY_DAYS = 50


def _bar_date_et(bar) -> date | None:
    timestamp = getattr(bar, "timestamp", None)
    if timestamp is None:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(ET).date()


def debug_symbol(symbol: str):
    """Analyze a symbol to see why it passed/failed the breakout check."""
    try:
        credentials, _ = bootstrap_trading_auth("debug_daily_trend.py")
        data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
        
        now = datetime.now(timezone.utc)
        response = data_client.get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=[symbol],
                timeframe=TimeFrame.Day,
                start=now - timedelta(days=HISTORY_DAYS),
                end=now,
                feed=DataFeed.IEX,
            )
        )
        
        data = getattr(response, "data", {})
        bars = list(data.get(symbol, []))
        
        if not bars:
            print(f"{symbol}: No data available")
            return
        
        today_et = datetime.now(ET).date()
        completed = [bar for bar in bars if _bar_date_et(bar) and _bar_date_et(bar) < today_et]
        today_bars = [bar for bar in bars if _bar_date_et(bar) == today_et]
        
        print(f"\n{'='*70}")
        print(f"SYMBOL: {symbol}")
        print(f"{'='*70}")
        print(f"Completed days: {len(completed)}, Today bars: {len(today_bars)}")
        
        if len(completed) < ENTRY_LOOKBACK:
            print(f"Not enough history ({len(completed)} < {ENTRY_LOOKBACK})")
            return
        
        prior = completed[-ENTRY_LOOKBACK:]
        highs = [float(getattr(bar, "high")) for bar in prior]
        resistance = _fit_trend(highs)
        
        print(f"\nLast {ENTRY_LOOKBACK} days of highs:")
        for i, (bar, high) in enumerate(zip(prior, highs)):
            bar_date = _bar_date_et(bar)
            print(f"  Day {i:2d} ({bar_date}): HIGH ${high:.2f}")
        
        print(f"\nTrend line fit:")
        print(f"  Slope: {resistance.slope:.6f}")
        print(f"  Intercept: {resistance.intercept:.2f}")
        print(f"  Projected at x=15 (today): ${resistance.projected:.2f}")
        
        # Calculate line values at each day
        print(f"\nTrend line values at each day:")
        for i in range(len(prior)):
            line_val = resistance.intercept + resistance.slope * i
            high = highs[i]
            print(f"  Day {i:2d}: Line=${line_val:.2f}, High=${high:.2f}, Diff=${high-line_val:+.2f}")
        
        line_at_yesterday = resistance.intercept + resistance.slope * (len(prior) - 1)
        trigger = _first_cent_above(resistance.projected)
        
        print(f"\nBreakout check:")
        print(f"  Yesterday (day 14):")
        yesterday_close = float(getattr(prior[-1], "close"))
        yesterday_trend_line = resistance.intercept + resistance.slope * (ENTRY_LOOKBACK - 1)
        today_trend_line = resistance.intercept + resistance.slope * ENTRY_LOOKBACK
        trigger = _first_cent_above(today_trend_line)
        
        print(f"    Close: ${yesterday_close:.2f}")
        print(f"    Trend line value at day 14: ${yesterday_trend_line:.2f}")
        print(f"  Today (day 15):")
        print(f"    Trend line value: ${today_trend_line:.2f}")
        print(f"    Trigger (1¢ above): ${trigger:.2f}")
        print(f"  Resistance slope: {resistance.slope:.6f}")
        
        if resistance.slope >= 0:
            print(f"  ❌ REJECTED: Slope is not negative (ascending trend)")
        else:
            if today_bars:
                today_bar = today_bars[-1]
                today_open = float(getattr(today_bar, "open"))
                print(f"\n  Today's open: ${today_open:.2f}")
                print(f"\n  Checks:")
                print(f"  1. Yesterday close below yesterday's trend line: ${yesterday_close:.2f} < ${yesterday_trend_line:.2f}? {yesterday_close < yesterday_trend_line}")
                print(f"  2. Today open above today's trend line:        ${today_open:.2f} > ${trigger:.2f}? {today_open > trigger}")
                
                if yesterday_close >= yesterday_trend_line:
                    print(f"  ❌ REJECTED: Yesterday didn't close below the trend line")
                elif today_open < trigger:
                    print(f"  ❌ REJECTED: Today didn't open above the trend line")
                else:
                    above_pct = (today_open - trigger) / trigger * 100.0
                    print(f"  ✓ ACCEPTED: True breakout ({above_pct:.2f}% above trend line)")
            else:
                print(f"  ❌ No bars for today")
    
    except Exception as e:
        print(f"{symbol}: Error - {e}")


if __name__ == "__main__":
    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["CMI", "ANET", "EW", "DELL", "BKR", "TXN"]
    
    for symbol in symbols:
        debug_symbol(symbol)
