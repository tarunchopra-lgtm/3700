from __future__ import annotations

import math
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from pathlib import Path
import sys

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth


CHECK_INTERVAL_SECONDS = 30
DAILY_REFRESH_HOUR_PT = 14
PT_TZ = ZoneInfo("America/Los_Angeles")


def _ensure_symbol(symbol: str) -> str:
    sanitized = symbol.strip().upper()
    if not sanitized:
        raise ValueError("Ticker symbol cannot be empty")
    return sanitized


def _extract_symbol_bars(bars, symbol: str):
    data = getattr(bars, "data", None)
    if data is None and isinstance(bars, dict):
        data = bars.get("data")

    if isinstance(data, dict):
        symbol_bars = data.get(symbol) or data.get(symbol.upper())
        if symbol_bars is None:
            symbol_bars = next(iter(data.values()), None)
        return list(symbol_bars) if symbol_bars is not None else []

    return list(data) if data is not None else []


def _get_previous_day_close(data_client: StockHistoricalDataClient, symbol: str) -> float:
    now = datetime.now(timezone.utc)
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=now - timedelta(days=20),
        end=now,
        limit=20,
        feed=DataFeed.IEX,
    )
    bars = data_client.get_stock_bars(request)
    bar_list = _extract_symbol_bars(bars, symbol)
    if not bar_list:
        raise RuntimeError(f"No daily bars returned for {symbol}")

    today = now.date()
    previous_day_bar = None

    for bar in reversed(bar_list):
        ts = getattr(bar, "timestamp", None)
        bar_date = ts.date() if ts is not None and hasattr(ts, "date") else None
        if bar_date is not None and bar_date < today:
            previous_day_bar = bar
            break

    if previous_day_bar is None:
        if len(bar_list) >= 2:
            previous_day_bar = bar_list[-2]
        else:
            previous_day_bar = bar_list[-1]

    close = getattr(previous_day_bar, "close", None)
    if close is None and isinstance(previous_day_bar, dict):
        close = previous_day_bar.get("close")

    if close is None:
        raise RuntimeError(f"Could not read previous-day close for {symbol}")

    return float(close)


def _get_today_open(data_client: StockHistoricalDataClient, symbol: str) -> float | None:
    now = datetime.now(timezone.utc)
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=now - timedelta(days=5),
        end=now,
        limit=5,
        feed=DataFeed.IEX,
    )
    bars = data_client.get_stock_bars(request)
    bar_list = _extract_symbol_bars(bars, symbol)
    if not bar_list:
        return None

    today = now.date()
    for bar in reversed(bar_list):
        ts = getattr(bar, "timestamp", None)
        bar_date = ts.date() if ts is not None and hasattr(ts, "date") else None
        if bar_date == today:
            open_price = getattr(bar, "open", None)
            if open_price is None and isinstance(bar, dict):
                open_price = bar.get("open")
            return float(open_price) if open_price is not None else None

    return None


def _calculate_levels(previous_close: float) -> tuple[float, float, float, float]:
    entry_price = float(math.ceil(previous_close))
    stop_loss = round(entry_price * 0.99, 2)
    target1 = round(entry_price * 1.01, 2)
    target2 = round(entry_price * 1.03, 2)

    if stop_loss >= entry_price:
        stop_loss = round(entry_price - 0.01, 2)
    if target1 <= entry_price:
        target1 = round(entry_price + 0.01, 2)
    if target2 <= target1:
        target2 = round(target1 + 0.01, 2)

    return previous_close, entry_price, stop_loss, target1, target2


def _build_fomo_trade_command(symbol: str, num_stocks: int, entry: float, stop: float, target1: float, target2: float) -> list[str]:
    script_path = Path(__file__).resolve().parent / "fomo_trade.py"
    return [
        sys.executable,
        str(script_path),
        symbol,
        str(num_stocks),
        f"{entry:.2f}",
        f"{stop:.2f}",
        f"{target1:.2f}",
        f"{target2:.2f}",
    ]


def _compute_levels(credentials, symbol: str) -> tuple[float, float, float, float, float]:
    data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
    previous_close = _get_previous_day_close(data_client, symbol)
    return _calculate_levels(previous_close)


def _is_entry_allowed_today(credentials, symbol: str, previous_close: float) -> tuple[bool, float | None]:
    data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
    today_open = _get_today_open(data_client, symbol)
    if today_open is None:
        return False, None
    return today_open > previous_close, today_open


def _is_after_refresh_cutoff(now_pt: datetime) -> bool:
    return (now_pt.hour, now_pt.minute) >= (DAILY_REFRESH_HOUR_PT, 0)


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _launch_fomo_trade(symbol: str, num_stocks: int, entry: float, stop: float, target1: float, target2: float) -> subprocess.Popen:
    command = _build_fomo_trade_command(symbol, num_stocks, entry, stop, target1, target2)
    print("\nLaunching fomo_trade.py with:")
    print("  " + " ".join(command))
    return subprocess.Popen(command)


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: python previous_close.py <TICKER> <NUM_STOCKS>")
        print("Example: python previous_close.py MU 2")
        return 1

    try:
        symbol = _ensure_symbol(sys.argv[1])
        num_stocks = int(sys.argv[2])
    except ValueError as exc:
        print(exc)
        return 1

    if num_stocks <= 0:
        print("NUM_STOCKS must be a positive integer")
        return 1

    # This prints auth diagnostics and verifies account access.
    try:
        credentials, _ = bootstrap_trading_auth("previous_close.py")
    except Exception as exc:
        print(f"Authentication failed: {exc}")
        return 1

    try:
        prev_close, entry, stop, target1, target2 = _compute_levels(credentials, symbol)
    except Exception as exc:
        print(f"Failed to calculate levels from previous close: {exc}")
        return 1

    print("\nStrategy levels from previous close")
    print(f"  Ticker:          {symbol}")
    print(f"  Previous close:  {prev_close:.4f}")
    print(f"  Entry (ceil):    {entry:.2f}")
    print(f"  Stop loss (1%):  {stop:.2f}")
    print(f"  Target 1 (1%):   {target1:.2f}")
    print(f"  Target 2 (3%):   {target2:.2f}")

    process = None
    gate_decision_date = None

    allowed, today_open = _is_entry_allowed_today(credentials, symbol, prev_close)
    gate_decision_date = datetime.now(PT_TZ).date()
    if today_open is None:
        print("[INFO] Today's open is not available yet. Waiting before launching fomo_trade.py...")
    elif not allowed:
        print(
            f"[INFO] No entry today: today's open ({today_open:.4f}) is not above previous close ({prev_close:.4f})."
        )
    else:
        print(
            f"[INFO] Entry gate passed: today's open ({today_open:.4f}) is above previous close ({prev_close:.4f})."
        )
        process = _launch_fomo_trade(symbol, num_stocks, entry, stop, target1, target2)

    now_pt = datetime.now(PT_TZ)
    last_daily_refresh_date = now_pt.date() if _is_after_refresh_cutoff(now_pt) else None

    try:
        while True:
            now_pt = datetime.now(PT_TZ)
            print(
                f"[CHECK {now_pt.strftime('%Y-%m-%d %H:%M:%S %Z')}] "
                f"Ticker={symbol} | PreviousClose={prev_close:.4f} | BuyLimit={entry:.2f}"
            )

            if gate_decision_date != now_pt.date():
                gate_decision_date = now_pt.date()

            if process is None:
                allowed, today_open = _is_entry_allowed_today(credentials, symbol, prev_close)
                if today_open is None:
                    print("[INFO] Waiting for today's open data before entry check...")
                elif allowed:
                    print(
                        f"[INFO] Entry gate passed: today's open ({today_open:.4f}) is above previous close ({prev_close:.4f})."
                    )
                    process = _launch_fomo_trade(symbol, num_stocks, entry, stop, target1, target2)
                else:
                    print(
                        f"[INFO] Entry blocked today: open ({today_open:.4f}) <= previous close ({prev_close:.4f})."
                    )

            if _is_after_refresh_cutoff(now_pt) and last_daily_refresh_date != now_pt.date():
                print("\n[INFO] 2:00 PM Pacific cutoff reached. Refreshing strategy levels from latest previous close...")
                try:
                    new_prev_close, new_entry, new_stop, new_target1, new_target2 = _compute_levels(credentials, symbol)
                except Exception as exc:
                    print(f"[WARN] Refresh failed: {exc}. Will retry on next check.")
                else:
                    prev_close, entry, stop, target1, target2 = (
                        new_prev_close,
                        new_entry,
                        new_stop,
                        new_target1,
                        new_target2,
                    )
                    print("[INFO] Updated strategy levels")
                    print(f"  Previous close:  {prev_close:.4f}")
                    print(f"  Entry (ceil):    {entry:.2f}")
                    print(f"  Stop loss (1%):  {stop:.2f}")
                    print(f"  Target 1 (1%):   {target1:.2f}")
                    print(f"  Target 2 (3%):   {target2:.2f}")

                    if process is not None:
                        _terminate_process(process)
                        process = None

                    allowed, today_open = _is_entry_allowed_today(credentials, symbol, prev_close)
                    if today_open is None:
                        print("[INFO] Today's open is not available yet after refresh; waiting before launch.")
                    elif not allowed:
                        print(
                            f"[INFO] Refresh gate blocked: open ({today_open:.4f}) <= previous close ({prev_close:.4f})."
                        )
                    else:
                        print(
                            f"[INFO] Refresh gate passed: open ({today_open:.4f}) > previous close ({prev_close:.4f})."
                        )
                        process = _launch_fomo_trade(symbol, num_stocks, entry, stop, target1, target2)
                    last_daily_refresh_date = now_pt.date()

            if process is not None:
                exit_code = process.poll()
                if exit_code is not None:
                    print(f"\n[WARN] fomo_trade.py exited with code {exit_code}. Restarting with current levels...")
                    process = _launch_fomo_trade(symbol, num_stocks, entry, stop, target1, target2)

            sys.stdout.flush()
            time.sleep(CHECK_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped by user")
        if process is not None:
            _terminate_process(process)
        return 0
    except Exception as exc:
        print(f"Failed to start fomo_trade.py: {exc}")
        if process is not None:
            _terminate_process(process)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

