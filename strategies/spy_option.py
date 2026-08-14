#!/usr/bin/env python3
"""SPY option range and trigger runner.

What it does:
- Reads current stock price
- Checks this week's call and put expiration contracts via helper functions
- Calculates ATR(14)
- Uses current stock price as midpoint for expected low/high range
- Computes call/put trigger levels from range + today's open
- Uses whole-number triggers and launches trade runners when hit

Usage:
    python spy_option.py
    python spy_option.py SPY
"""

from __future__ import annotations

import re
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

from pathlib import Path
import sys

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth
from strategies.current_week_option_function_call import get_current_week_call_option_symbol
from strategies.current_week_option_function_put import get_current_week_put_option_symbol


ATR_PERIOD = 14
CHECK_INTERVAL_SECONDS = 2
ORDER_QTY = 2
STOP_LOSS_OFFSET = 0.25
TARGET1_OFFSET = 0.25
TARGET2_OFFSET = 1.00
ET = ZoneInfo("America/New_York")
CURRENT_PRICE_PATTERN = re.compile(r"Current:\s*\$(\d+(?:\.\d+)?)")


def _extract_bars(bars_response, symbol: str):
    data = getattr(bars_response, "data", None)
    if data is None and isinstance(bars_response, dict):
        data = bars_response.get("data")

    if isinstance(data, dict):
        symbol_bars = data.get(symbol) or data.get(symbol.upper())
        if symbol_bars is None:
            symbol_bars = next(iter(data.values()), None)
        return list(symbol_bars) if symbol_bars is not None else []

    return list(data) if data is not None else []


def _get_bar_date_et(bar) -> date | None:
    ts = getattr(bar, "timestamp", None)
    if ts is None or getattr(ts, "tzinfo", None) is None:
        return None
    return ts.astimezone(ET).date()


def _get_recent_daily_bars(client: StockHistoricalDataClient, symbol: str, days: int = 20):
    now = datetime.now(timezone.utc)
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=now - timedelta(days=days),
        end=now,
        limit=days,
        feed=DataFeed.IEX,
    )
    bars_response = client.get_stock_bars(request)
    bars = _extract_bars(bars_response, symbol)
    if not bars:
        raise RuntimeError(f"No daily bars returned for {symbol}")
    return bars


def _get_latest_trading_day_bar(client: StockHistoricalDataClient, symbol: str):
    bars = _get_recent_daily_bars(client, symbol, days=20)
    return bars[-1]


def _get_weekly_expiration(today: date | None = None) -> date:
    if today is None:
        today = date.today()
    days_until_friday = (4 - today.weekday()) % 7
    return today + timedelta(days=days_until_friday)


def _get_current_price(client: StockHistoricalDataClient, symbol: str, fallback_price: float | None = None) -> float:
    try:
        request = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
        trade_map = client.get_stock_latest_trade(request)
        trade = trade_map[symbol]
        return float(trade.price)
    except Exception:
        if fallback_price is not None:
            return float(fallback_price)
        raise


def _get_reference_open_price(client: StockHistoricalDataClient, symbol: str) -> tuple[float, date]:
    latest_bar = _get_latest_trading_day_bar(client, symbol)
    bar_open = getattr(latest_bar, "open", None)
    if bar_open is None:
        raise RuntimeError(f"Could not read open price from latest trading-day bar for {symbol}")
    bar_date = _get_bar_date_et(latest_bar)
    if bar_date is None:
        raise RuntimeError(f"Could not read trading-day date for {symbol}")
    return float(bar_open), bar_date


def _true_range(current_bar, previous_close: float | None) -> float:
    high = float(getattr(current_bar, "high"))
    low = float(getattr(current_bar, "low"))

    if previous_close is None:
        return high - low

    return max(
        high - low,
        abs(high - previous_close),
        abs(low - previous_close),
    )


def _calculate_atr(client: StockHistoricalDataClient, symbol: str) -> float:
    now = datetime.now(timezone.utc)
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=now - timedelta(days=ATR_PERIOD * 4),
        end=now,
        limit=ATR_PERIOD + 1,
        feed=DataFeed.IEX,
    )
    bars_response = client.get_stock_bars(request)
    bars = _extract_bars(bars_response, symbol)
    if len(bars) < ATR_PERIOD + 1:
        raise RuntimeError(f"Not enough daily bars to calculate {ATR_PERIOD}-day ATR for {symbol}")

    tr_values: list[float] = []
    previous_close = None
    for bar in bars[-(ATR_PERIOD + 1):]:
        tr_values.append(_true_range(bar, previous_close))
        previous_close = float(getattr(bar, "close"))

    return sum(tr_values[-ATR_PERIOD:]) / ATR_PERIOD


def _get_price_line_for_symbol(symbol: str) -> str:
    stock_price_script = Path(__file__).resolve().parent.parent / "indicator" / "stock_price.py"
    result = subprocess.run(
        [sys.executable, str(stock_price_script), symbol],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"stock_price.py failed for {symbol}: {err}")

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"No output from stock_price.py for {symbol}")
    return lines[-1]


def _extract_current_price_from_line(line: str) -> float:
    match = CURRENT_PRICE_PATTERN.search(line)
    if not match:
        raise RuntimeError(f"Unable to parse current price from line: {line}")
    return float(match.group(1))


def _build_fomo_trade_command(option_symbol: str, entry_price: float) -> list[str]:
    script_path = Path(__file__).resolve().parent / "fomo_trade.py"
    stop_price = max(round(entry_price - STOP_LOSS_OFFSET, 2), 0.01)
    target1_price = round(entry_price + TARGET1_OFFSET, 2)
    target2_price = round(entry_price + TARGET2_OFFSET, 2)
    return [
        sys.executable,
        str(script_path),
        option_symbol,
        str(ORDER_QTY),
        f"{entry_price:.2f}",
        f"{stop_price:.2f}",
        f"{target1_price:.2f}",
        f"{target2_price:.2f}",
    ]


def _launch_trade(option_symbol: str, side_label: str) -> subprocess.Popen:
    price_line = _get_price_line_for_symbol(option_symbol)
    option_price = _extract_current_price_from_line(price_line)
    cmd = _build_fomo_trade_command(option_symbol, option_price)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {side_label} trigger hit. Launching:")
    print("  " + " ".join(cmd))
    return subprocess.Popen(cmd)


def main() -> int:
    symbol = sys.argv[1].strip().upper() if len(sys.argv) > 1 else "SPY"

    try:
        credentials, _ = bootstrap_trading_auth("spy_option.py")
    except Exception as exc:
        print(f"Authentication failed: {exc}")
        return 1

    client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)

    try:
        latest_bar = _get_latest_trading_day_bar(client, symbol)
        latest_bar_close = float(getattr(latest_bar, "close"))
        current_price = _get_current_price(client, symbol, fallback_price=latest_bar_close)
        atr = _calculate_atr(client, symbol)
        open_price, reference_open_date = _get_reference_open_price(client, symbol)
    except Exception as exc:
        print(f"Error computing range for {symbol}: {exc}")
        return 1

    expected_low = current_price - (atr / 2.0)
    expected_high = current_price + (atr / 2.0)

    call_reference = round((expected_low + open_price) / 2.0)
    put_reference = round((expected_high + open_price) / 2.0)

    try:
        call_symbol = get_current_week_call_option_symbol(symbol, float(call_reference))
        put_symbol = get_current_week_put_option_symbol(symbol, float(put_reference))
    except Exception as exc:
        print(f"Error resolving option symbols for {symbol}: {exc}")
        return 1

    try:
        call_price_line = _get_price_line_for_symbol(call_symbol)
        put_price_line = _get_price_line_for_symbol(put_symbol)
    except Exception as exc:
        print(f"Error fetching option price lines: {exc}")
        return 1

    print(f"Symbol: {symbol}")
    print(f"Current Price: {current_price:.2f}")
    print(f"ATR({ATR_PERIOD}): {atr:.4f}")
    print(f"Reference Open ({reference_open_date.isoformat()}): {open_price:.2f}")
    print(f"This Week Expiration: {_get_weekly_expiration().isoformat()}")
    print(f"Expected Low (current-ATR/2): {expected_low:.2f}")
    print(f"Expected High (current+ATR/2): {expected_high:.2f}")
    print(f"Call Trigger (whole): {call_reference}")
    print(f"Put Trigger (whole): {put_reference}")
    print(f"CALL Symbol: {call_symbol}")
    print(f"PUT Symbol: {put_symbol}")
    print(f"CALL Price: {call_price_line}")
    print(f"PUT Price: {put_price_line}")

    print("\nMonitoring SPY for trigger hits...")
    print(f"- CALL trigger when SPY <= {call_reference}")
    print(f"- PUT trigger when SPY >= {put_reference}")

    last_spy_price = current_price
    call_launched = False
    put_launched = False
    processes: list[subprocess.Popen] = []

    try:
        while True:
            spy_now = _get_current_price(client, symbol)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] SPY={spy_now:.2f}")

            call_hit = last_spy_price > call_reference >= spy_now or spy_now <= call_reference
            put_hit = last_spy_price < put_reference <= spy_now or spy_now >= put_reference

            if not call_launched and call_hit:
                processes.append(_launch_trade(call_symbol, "CALL"))
                call_launched = True

            if not put_launched and put_hit:
                processes.append(_launch_trade(put_symbol, "PUT"))
                put_launched = True

            if call_launched and put_launched:
                print("Both CALL and PUT trade runners launched.")
                break

            last_spy_price = spy_now
            time.sleep(CHECK_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("Stopped by user.")

    # Keep references alive for child processes and show status.
    for process in processes:
        print(f"Launched PID: {process.pid}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
