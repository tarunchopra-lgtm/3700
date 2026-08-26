#!/usr/bin/env python3
"""Market open trend strategy: identify daily breakouts and execute long_trend trades.

Behavior:
1) Check if lists/today-breakout was updated today; if so, skip running find_daily_trend.py.
2) Read lists/today-breakout to get today's breakout candidates.
3) For each ticker:
   - Fetch average daily volume over the last 30 days.
   - Calculate 1% of average volume as the volume-per-candle target.
   - Start long_trend.py with 2 shares and calculated volume target.
4) Run continuously every 20 seconds, checking for new breakouts.

Usage:
    python strategies/mkt-open-trend.py

Environment:
    QUANTITY (default 2): Shares to trade per symbol (configurable at top of file)
    VOLUME_PERCENT (default 1.0): Percentage of average volume to use as volume-per-candle
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
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

# Configuration
QUANTITY = 2  # Number of shares per trade
VOLUME_PERCENT = 1.0  # Use 1% of average volume as volume-per-candle target
VOLUME_LOOKBACK_DAYS = 30  # Days to use for average volume calculation
CHECK_INTERVAL_SECONDS = 20  # Check for new breakouts every 20 seconds

BREAKOUT_FILE = WORKSPACE_ROOT / "lists" / "today-breakout"
ET = ZoneInfo("America/New_York")


def _is_breakout_file_today() -> bool:
    """Check if today-breakout file was updated today."""
    if not BREAKOUT_FILE.exists():
        return False
    
    file_mtime = datetime.fromtimestamp(BREAKOUT_FILE.stat().st_mtime, tz=ET)
    file_date = file_mtime.date()
    today_et = datetime.now(ET).date()
    
    return file_date == today_et


def _run_find_daily_trend() -> None:
    """Run find_daily_trend.py to identify breakout candidates."""
    script_path = WORKSPACE_ROOT / "strategies" / "find_daily_trend.py"
    print("[INIT] Running find_daily_trend.py to identify breakouts...")
    result = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"[WARN] find_daily_trend.py exited with code {result.returncode}")
        if result.stderr:
            print(f"       stderr: {result.stderr.strip()}")
    else:
        print("[OK] Breakout list generated")


def _read_breakout_list() -> list[str]:
    """Read the breakout candidates from lists/today-breakout."""
    if not BREAKOUT_FILE.exists():
        return []

    symbols = []
    for line in BREAKOUT_FILE.read_text(encoding="utf-8").splitlines():
        symbol = line.strip().upper()
        if symbol and not symbol.startswith("#"):
            symbols.append(symbol)
    
    return symbols


def _get_average_volume(
    data_client: StockHistoricalDataClient,
    symbol: str,
    lookback_days: int = VOLUME_LOOKBACK_DAYS,
) -> float | None:
    """Fetch average daily volume for the given symbol."""
    try:
        now = datetime.now(timezone.utc)
        response = data_client.get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                start=now - timedelta(days=lookback_days),
                end=now,
                feed=DataFeed.IEX,
            )
        )
        
        bars = getattr(response, "data", {}).get(symbol, [])
        if not bars:
            print(f"[WARN] No volume data for {symbol}; skipping")
            return None
        
        total_volume = sum(float(getattr(bar, "volume", 0)) for bar in bars)
        avg_volume = total_volume / len(bars)
        
        if avg_volume <= 0:
            print(f"[WARN] Invalid average volume for {symbol}: {avg_volume}")
            return None
        
        return avg_volume
    except Exception as exc:
        print(f"[WARN] Could not fetch volume data for {symbol}: {exc}")
        return None


def _get_current_price(
    data_client: StockHistoricalDataClient,
    symbol: str,
) -> float | None:
    """Fetch current/latest trade price for the given symbol."""
    try:
        response = data_client.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=symbol)
        )
        if isinstance(response, dict):
            trade = response.get(symbol)
        else:
            trade = response
        
        if trade:
            current_price = float(getattr(trade, "price", 0))
            if current_price > 0:
                return current_price
        
        return None
    except Exception as exc:
        print(f"[WARN] Could not fetch current price for {symbol}: {exc}")
        return None


def _calculate_volume_target(avg_volume: float, percent: float = VOLUME_PERCENT) -> float:
    """Calculate volume-per-candle target as a percentage of average volume."""
    target = round(avg_volume * (percent / 100.0), 2)
    return max(target, 1.0)  # Ensure minimum of 1.0


def _start_long_trend(symbol: str, quantity: int, volume_target: float) -> None:
    """Start long_trend.py for the given symbol with calculated volume target."""
    script_path = WORKSPACE_ROOT / "strategies" / "long_trend.py"
    cmd = [
        sys.executable,
        str(script_path),
        symbol,
        str(quantity),
        str(volume_target),
    ]
    
    # Run in same console (no new window), display output in real-time
    try:
        subprocess.Popen(
            cmd,
            text=True,
            creationflags=0,  # No CREATE_NEW_CONSOLE
        )
    except Exception as exc:
        print(f"[WARN] Could not start long_trend.py for {symbol}: {exc}")


def main() -> int:
    """Main entry point."""
    print("=" * 140)
    print("MARKET OPEN TREND STRATEGY - Auto-Trade Daily Breakouts with Volume")
    print("=" * 140)
    
    try:
        credentials, trading_client = bootstrap_trading_auth("mkt-open-trend.py")
    except Exception as exc:
        print(f"Authentication failed: {exc}")
        return 1
    
    data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
    
    # Step 1: Check if breakout file is already updated today
    if _is_breakout_file_today():
        print(f"[SKIP] {BREAKOUT_FILE.name} is already updated today; using existing list")
    else:
        print(f"[UPDATE] {BREAKOUT_FILE.name} needs updating; running find_daily_trend.py...")
        _run_find_daily_trend()
    
    # Track which symbols have been traded to avoid duplicates
    traded_symbols: set[str] = set()
    
    print(f"\n[MONITOR] Running continuously every {CHECK_INTERVAL_SECONDS} seconds (Ctrl+C to stop)")
    print("=" * 140)
    
    try:
        while True:
            # Read current breakout list
            symbols = _read_breakout_list()
            current_time = datetime.now(ET).strftime('%H:%M:%S')
            
            if not symbols:
                print(f"[{current_time}] No breakout candidates found")
                time.sleep(CHECK_INTERVAL_SECONDS)
                continue
            
            # Collect trade data for all symbols
            trades_data = []
            new_trades = 0
            
            for symbol in symbols:
                current_price = _get_current_price(data_client, symbol)
                avg_volume = _get_average_volume(data_client, symbol)
                
                if avg_volume is None or current_price is None:
                    continue
                
                volume_target = _calculate_volume_target(avg_volume)
                
                trades_data.append({
                    'symbol': symbol,
                    'current': current_price,
                    'avg_volume': avg_volume,
                    'volume_target': volume_target,
                    'quantity': QUANTITY,
                    'is_new': symbol not in traded_symbols,
                })
                
                if symbol not in traded_symbols:
                    _start_long_trend(symbol, QUANTITY, volume_target)
                    traded_symbols.add(symbol)
                    new_trades += 1
                    time.sleep(0.2)
            
            # Display table
            if trades_data:
                print(f"\n[{current_time}] Trades Summary - Total: {len(trades_data)} | New: {new_trades} | Total Traded: {len(traded_symbols)}")
                print("-" * 140)
                print(
                    f"{'SYMBOL':8} | {'CURRENT':12} | {'AVG_VOLUME':15} | {'VOLUME_TARGET':15} | {'QTY':5} | {'STATUS':10}"
                )
                print("-" * 140)
                
                for trade in trades_data:
                    status = "NEW" if trade['is_new'] else "MONITORED"
                    print(
                        f"{trade['symbol']:8} | ${trade['current']:11.2f} | {trade['avg_volume']:14.0f} | "
                        f"{trade['volume_target']:14.2f} | {trade['quantity']:5} | {status:10}"
                    )
                
                print("-" * 140)
            else:
                print(f"[{current_time}] No valid trades to display")
            
            time.sleep(CHECK_INTERVAL_SECONDS)
    
    except KeyboardInterrupt:
        print(f"\n[EXIT] Stopped by user")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
