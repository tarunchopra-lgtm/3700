#!/usr/bin/env python3
"""Market open strategy: identify daily trend breakouts and execute trades with calls.

Behavior:
1) Run strategies/find_daily_trend.py to identify breaking symbols (creates lists/today-breakout).
2) Read lists/today-breakout to get today's breakout candidates.
3) For each ticker:
   - Fetch yesterday's high price as entry price.
   - Calculate stop loss (2% below entry) and profit targets (3% and 5% above entry).
   - Buy 10 shares using fomo_trade.py with the calculated levels.
4) Simultaneously, start strategies/gocall.py to auto-manage call options on these positions.

Usage:
    python strategies/mkt-open.py

Environment:
    MIN_OPEN_INTEREST (default 1000): Configurable in gocall.py
"""

from __future__ import annotations

import subprocess
import sys
import time
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

# Configuration
NUM_STOCKS = 10  # Number of shares to buy
STOP_LOSS_PERCENT = 2.0  # Stop loss 2% below yesterday's high
TARGET1_PERCENT = 3.0  # First target 3% above yesterday's high
TARGET2_PERCENT = 5.0  # Second target 5% above yesterday's high

BREAKOUT_FILE = WORKSPACE_ROOT / "lists" / "today-breakout"
ET = ZoneInfo("America/New_York")


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
        print(f"[WARN] {BREAKOUT_FILE} does not exist; skipping trades")
        return []

    symbols = []
    for line in BREAKOUT_FILE.read_text(encoding="utf-8").splitlines():
        symbol = line.strip().upper()
        if symbol and not symbol.startswith("#"):
            symbols.append(symbol)
    
    print(f"[INIT] Read {len(symbols)} breakout candidates: {', '.join(symbols)}")
    return symbols


def _get_yesterday_high(
    data_client: StockHistoricalDataClient,
    symbol: str,
) -> float | None:
    """Fetch yesterday's high price for the given symbol."""
    try:
        now = datetime.now(timezone.utc)
        # Fetch last 2 days to ensure we get yesterday's data
        response = data_client.get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                start=now - timedelta(days=3),
                end=now,
                feed=DataFeed.IEX,
            )
        )
        
        bars = getattr(response, "data", {}).get(symbol)
        if not bars or len(bars) < 2:
            print(f"[WARN] Insufficient data for {symbol}; skipping")
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
    
    print(
        f"[TRADE] {symbol}: entry={entry_price:.2f} stop={stop_price:.2f} "
        f"target1={target1:.2f} target2={target2:.2f}"
    )
    
    # Run in background (subprocess detached mode on Windows)
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
        )
    except Exception as exc:
        print(f"[WARN] Could not start fomo_trade.py for {symbol}: {exc}")


def _start_gocall() -> None:
    """Start gocall.py to auto-manage call options."""
    script_path = WORKSPACE_ROOT / "strategies" / "gocall.py"
    cmd = [sys.executable, str(script_path), "1"]
    
    print("[INIT] Starting gocall.py for automated call management...")
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
        )
    except Exception as exc:
        print(f"[WARN] Could not start gocall.py: {exc}")


def main() -> int:
    """Main entry point."""
    print("=" * 70)
    print("MARKET OPEN STRATEGY - Auto-Trade Daily Breakouts with Call Options")
    print("=" * 70)
    
    try:
        credentials, trading_client = bootstrap_trading_auth("mkt-open.py")
    except Exception as exc:
        print(f"Authentication failed: {exc}")
        return 1
    
    data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
    
    # Step 1: Generate breakout list
    _run_find_daily_trend()
    
    # Step 2: Read breakout candidates
    symbols = _read_breakout_list()
    if not symbols:
        print("[EXIT] No breakout candidates found")
        return 0
    
    # Step 3: Execute trades for each breakout
    trades_started = 0
    for symbol in symbols:
        yesterday_high = _get_yesterday_high(data_client, symbol)
        if yesterday_high is None:
            continue
        
        stop_price, target1, target2 = _calculate_levels(yesterday_high)
        _start_fomo_trade(symbol, yesterday_high, stop_price, target1, target2)
        trades_started += 1
        time.sleep(0.5)  # Brief delay between starting trades
    
    print(f"\n[SUMMARY] Started {trades_started} trades")
    
    # Step 4: Start gocall for call management
    if trades_started > 0:
        time.sleep(2)  # Let trades initialize before starting gocall
        _start_gocall()
    
    print("[DONE] Market open strategy execution complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
