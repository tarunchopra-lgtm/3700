from __future__ import annotations

import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient, OptionHistoricalDataClient
from alpaca.data.requests import CryptoLatestTradeRequest, StockLatestTradeRequest, OptionLatestTradeRequest

from pathlib import Path
import sys

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth


CHECK_INTERVAL_SECONDS = 30
DAILY_REFRESH_HOUR_PT = 14
PT_TZ = ZoneInfo("America/Los_Angeles")
OPTION_SYMBOL_PATTERN = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")


def _is_option_symbol(symbol: str) -> bool:
    return bool(OPTION_SYMBOL_PATTERN.match(symbol.upper()))


def _ensure_symbol(symbol: str) -> str:
    sanitized = symbol.strip().upper()
    if not sanitized:
        raise ValueError("Ticker symbol cannot be empty")
    return sanitized


def _get_current_price(symbol: str, credentials) -> float:
    if "/" in symbol:
        data_client = CryptoHistoricalDataClient(credentials.api_key, credentials.secret_key)
        request = CryptoLatestTradeRequest(symbol_or_symbols=symbol)
        trade_map = data_client.get_crypto_latest_trade(request)
        trade = trade_map[symbol]
        return float(trade.price)

    if _is_option_symbol(symbol):
        data_client = OptionHistoricalDataClient(credentials.api_key, credentials.secret_key)
        request = OptionLatestTradeRequest(symbol_or_symbols=symbol, feed=OptionsFeed.INDICATIVE)
        trade_map = data_client.get_option_latest_trade(request)
        trade = trade_map[symbol]
        return float(trade.price)

    data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
    request = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
    trade_map = data_client.get_stock_latest_trade(request)
    trade = trade_map[symbol]
    return float(trade.price)


def _calculate_levels(current_price: float) -> tuple[float, float, float, float]:
    entry_price = round(current_price, 2)
    stop_loss = round(entry_price * 0.99, 2)
    target1 = round(entry_price * 1.01, 2)
    target2 = round(entry_price * 1.03, 2)

    if stop_loss >= entry_price:
        stop_loss = round(entry_price - 0.01, 2)
    if target1 <= entry_price:
        target1 = round(entry_price + 0.01, 2)
    if target2 <= target1:
        target2 = round(target1 + 0.01, 2)

    return entry_price, stop_loss, target1, target2


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
    current_price = _get_current_price(symbol, credentials)
    entry, stop, target1, target2 = _calculate_levels(current_price)
    return current_price, entry, stop, target1, target2


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
        print("Usage: python fomo_market.py <TICKER> <NUM_STOCKS>")
        print("Example: python fomo_market.py MU 2")
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

    if num_stocks % 2 != 0:
        print(f"NUM_STOCKS must be an even number, got {num_stocks}")
        return 1

    try:
        credentials, _ = bootstrap_trading_auth("fomo_market.py")
    except Exception as exc:
        print(f"Authentication failed: {exc}")
        return 1

    try:
        current_price, entry, stop, target1, target2 = _compute_levels(credentials, symbol)
    except Exception as exc:
        print(f"Failed to calculate levels from current price: {exc}")
        return 1

    print("\nStrategy levels from current price")
    print(f"  Ticker:          {symbol}")
    print(f"  Current price:   {current_price:.4f}")
    print(f"  Entry (market):  {entry:.2f}")
    print(f"  Stop loss (1%):  {stop:.2f}")
    print(f"  Target 1 (1%):   {target1:.2f}")
    print(f"  Target 2 (3%):   {target2:.2f}")

    process = _launch_fomo_trade(symbol, num_stocks, entry, stop, target1, target2)

    now_pt = datetime.now(PT_TZ)
    last_daily_refresh_date = now_pt.date() if _is_after_refresh_cutoff(now_pt) else None

    try:
        while True:
            now_pt = datetime.now(PT_TZ)
            print(
                f"[CHECK {now_pt.strftime('%Y-%m-%d %H:%M:%S %Z')}] "
                f"Ticker={symbol} | CurrentPrice={current_price:.4f} | BuyLimit={entry:.2f}"
            )

            if _is_after_refresh_cutoff(now_pt) and last_daily_refresh_date != now_pt.date():
                print("\n[INFO] 2:00 PM Pacific cutoff reached. Refreshing strategy levels from current price...")
                try:
                    new_current, new_entry, new_stop, new_target1, new_target2 = _compute_levels(credentials, symbol)
                except Exception as exc:
                    print(f"[WARN] Refresh failed: {exc}. Will retry on next check.")
                else:
                    current_price, entry, stop, target1, target2 = (
                        new_current,
                        new_entry,
                        new_stop,
                        new_target1,
                        new_target2,
                    )
                    print("[INFO] Updated strategy levels")
                    print(f"  Current price:   {current_price:.4f}")
                    print(f"  Entry (market):  {entry:.2f}")
                    print(f"  Stop loss (1%):  {stop:.2f}")
                    print(f"  Target 1 (1%):   {target1:.2f}")
                    print(f"  Target 2 (3%):   {target2:.2f}")

                    _terminate_process(process)
                    process = _launch_fomo_trade(symbol, num_stocks, entry, stop, target1, target2)
                    last_daily_refresh_date = now_pt.date()

            exit_code = process.poll()
            if exit_code is not None:
                print(f"\n[WARN] fomo_trade.py exited with code {exit_code}. Restarting with current levels...")
                process = _launch_fomo_trade(symbol, num_stocks, entry, stop, target1, target2)

            sys.stdout.flush()
            time.sleep(CHECK_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped by user")
        _terminate_process(process)
        return 0
    except Exception as exc:
        print(f"Failed to run wrapper: {exc}")
        _terminate_process(process)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

