#!/usr/bin/env python3
"""Market open strategy: identify daily trend breakouts and execute trades with calls.

Behavior:
1) Check if lists/today-breakout was updated today; if so, skip running find_daily_trend.py.
2) Read lists/today-breakout to get today's breakout candidates.
3) For each ticker:
   - Fetch yesterday's high price as entry price.
   - Calculate stop loss (2% below entry) and profit targets (3% and 5% above entry).
   - Buy 10 shares using fomo_trade.py with the calculated levels.
4) Simultaneously, start strategies/gocall.py to auto-manage call options on these positions.
5) Run continuously every 20 seconds, checking for new breakouts.

Usage:
    python strategies/mkt-open.py

Environment:
    MIN_OPEN_INTEREST (default 1000): Configurable in gocall.py
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
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

# Configuration
NUM_STOCKS = 10  # Number of shares to buy
STOP_LOSS_PERCENT = 2.0  # Stop loss 2% below yesterday's high
TARGET1_PERCENT = 3.0  # First target 3% above yesterday's high
TARGET2_PERCENT = 5.0  # Second target 5% above yesterday's high
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


def _get_yesterday_high(
    data_client: StockHistoricalDataClient,
    symbol: str,
) -> float | None:
    """Fetch yesterday's high price for the given symbol."""
    try:
        now = datetime.now(timezone.utc)
        # Fetch last 10 days to ensure we get yesterday's data
        response = data_client.get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                start=now - timedelta(days=10),
                end=now,
                feed=DataFeed.IEX,
            )
        )
        
        bars = getattr(response, "data", {}).get(symbol)
        if not bars or len(bars) < 2:
            print(f"[WARN] Insufficient data for {symbol}; skipping (bars={len(bars) if bars else 0})")
            return None
        
        # Get the second-to-last bar (yesterday in market time)
        yesterday_bar = bars[-2]
        yesterday_high = float(getattr(yesterday_bar, "high", 0))
        
        if yesterday_high <= 0:
            print(f"[WARN] Invalid high price for {symbol}: {yesterday_high}")
            return None
        
        return yesterday_high
    except Exception as exc:
        print(f"[WARN] Could not fetch data for {symbol}: {exc}")
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


def _calculate_levels(entry_price: float) -> tuple[float, float, float]:
    """Calculate stop loss and profit targets based on entry price."""
    stop_price = round(entry_price * (1 - STOP_LOSS_PERCENT / 100.0), 2)
    target1_price = round(entry_price * (1 + TARGET1_PERCENT / 100.0), 2)
    target2_price = round(entry_price * (1 + TARGET2_PERCENT / 100.0), 2)
    return stop_price, target1_price, target2_price


def _start_fomo_trade(symbol: str, entry_price: float, stop_price: float, target1: float, target2: float) -> None:
    """Start fomo_trade.py for the given symbol with calculated levels."""
    script_path = WORKSPACE_ROOT / "strategies" / "fomo_trade.py"
    cmd = [
        sys.executable,
        str(script_path),
        symbol,
        str(NUM_STOCKS),
        str(entry_price),
        str(stop_price),
        str(target1),
        str(target2),
    ]
    
    # Run in same console (no new window), display output in real-time
    try:
        subprocess.Popen(
            cmd,
            text=True,
            creationflags=0,  # No CREATE_NEW_CONSOLE
        )
    except Exception as exc:
        print(f"[WARN] Could not start fomo_trade.py for {symbol}: {exc}")


def _is_gocall_running() -> bool:
    """Check if gocall.py is already running."""
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline') or []
                if any('gocall' in str(arg).lower() for arg in cmdline):
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return False
    except ImportError:
        # psutil not available; assume gocall is not running
        return False
    except Exception as exc:
        print(f"[WARN] Could not determine if gocall.py is running: {exc}", file=sys.stderr)
        return False


def _start_gocall() -> None:
    """Start gocall.py to auto-manage call options (if not already running)."""
    if _is_gocall_running():
        print("[SKIP] gocall.py is already running; skipping duplicate start")
        return
    
    script_path = WORKSPACE_ROOT / "strategies" / "gocall.py"
    cmd = [sys.executable, str(script_path), "1"]
    
    print("[INIT] Starting gocall.py for automated call management...")
    sys.stdout.flush()
    try:
        subprocess.Popen(
            cmd,
            text=True,
            creationflags=0,  # No CREATE_NEW_CONSOLE
        )
    except Exception as exc:
        print(f"[WARN] Could not start gocall.py: {exc}")


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Market open strategy: identify daily trend breakouts and execute trades with calls."
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=CHECK_INTERVAL_SECONDS,
        help=f"Check interval in seconds (default: {CHECK_INTERVAL_SECONDS})",
    )
    args = parser.parse_args()
    
    check_interval = args.interval
    if check_interval < 1:
        print(f"[ERROR] Interval must be >= 1 second")
        return 1
    
    print("=" * 130)
    print("MARKET OPEN STRATEGY - Auto-Trade Daily Breakouts with Call Options")
    print("=" * 130)
    print(f"[CONFIG] Check interval: {check_interval} seconds")
    print("=" * 130)
    
    try:
        credentials, trading_client = bootstrap_trading_auth("mkt-open.py")
    except Exception as exc:
        print(f"Authentication failed: {exc}")
        return 1
    
    data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
    
    # Step 1: Check if breakout file is already updated today
    if _is_breakout_file_today():
        print(f"[SKIP] {BREAKOUT_FILE.name} is already updated today; using existing list")
    else:
        print(f"[UPDATE] {BREAKOUT_FILE.name} needs updating; running find_daily_trend.py...")
        # _run_find_daily_trend()  # DISABLED: user runs separately
    
    # Track which symbols have been traded to avoid duplicates
    traded_symbols: set[str] = set()
    gocall_started = False
    
    print(f"\n[MONITOR] Running continuously every {check_interval} seconds (Ctrl+C to stop)")
    print("=" * 130)
    
    try:
        while True:
            # Read current breakout list
            symbols = _read_breakout_list()
            current_time = datetime.now(ET).strftime('%H:%M:%S')
            
            if not symbols:
                print(f"[{current_time}] No breakout candidates found")
                time.sleep(check_interval)
                continue
            
            # Collect trade data for all symbols (new and existing)
            trades_data = []
            new_trades = 0
            
            for symbol in symbols:
                yesterday_high = _get_yesterday_high(data_client, symbol)
                current_price = _get_current_price(data_client, symbol)
                
                if yesterday_high is None or current_price is None:
                    continue
                
                # Use yesterday's high as limit order entry price
                entry_price = yesterday_high
                stop_price, target1, target2 = _calculate_levels(entry_price)
                
                trades_data.append({
                    'symbol': symbol,
                    'current': current_price,
                    'yesterday_high': yesterday_high,
                    'entry': entry_price,
                    'stop': stop_price,
                    'target1': target1,
                    'target2': target2,
                    'is_new': symbol not in traded_symbols,
                })
                
                if symbol not in traded_symbols:
                    _start_fomo_trade(symbol, entry_price, stop_price, target1, target2)
                    traded_symbols.add(symbol)
                    new_trades += 1
                    time.sleep(0.2)
            
            # Display table
            if trades_data:
                print(f"\n[{current_time}] Trades Summary - Total: {len(trades_data)} | New: {new_trades} | Total Traded: {len(traded_symbols)}")
                print("-" * 130)
                print(
                    f"{'SYMBOL':8} | {'CURRENT':10} | {'YTD_HIGH':10} | {'ENTRY':10} | {'STOP':10} | "
                    f"{'TARGET1':10} | {'TARGET2':10} | {'STATUS':10}"
                )
                print("-" * 130)
                
                for trade in trades_data:
                    status = "NEW" if trade['is_new'] else "MONITORED"
                    print(
                        f"{trade['symbol']:8} | ${trade['current']:9.2f} | ${trade['yesterday_high']:9.2f} | "
                        f"${trade['entry']:9.2f} | ${trade['stop']:9.2f} | "
                        f"${trade['target1']:9.2f} | ${trade['target2']:9.2f} | {status:10}"
                    )
                
                print("-" * 130)
                
                # Start gocall once after first trades
                # if new_trades > 0 and not gocall_started:
                #     time.sleep(1)
                #     _start_gocall()  # DISABLED: user runs separately
                #     gocall_started = True
            else:
                print(f"[{current_time}] No valid trades to display")
            
            time.sleep(check_interval)
    
    except KeyboardInterrupt:
        print(f"\n[EXIT] Stopped by user")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
