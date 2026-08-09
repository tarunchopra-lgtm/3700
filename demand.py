#!/usr/bin/env python3
"""Demand pattern scanner and trade launcher.

Usage:
    python demand.py <TICKER>

Logic:
1) Pull last 200 hourly candles.
2) Compute average candle range (high - low).
3) Find pattern where:
   - Current candle range >= 2x average range
   - Previous candle range < average range
4) Pick the smaller candle of the two.
5) Set levels from the smaller candle:
   - buy_point: highest point of smaller candle
   - stop_loss: lowest point of smaller candle
6) Targets:
   - target_1_price = buy_point + (buy_point - stop_loss)
   - target_2_price = buy_point + 3 * (buy_point - stop_loss)
7) Launch fomo_trade.py with computed values.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from alpaca.data.enums import DataFeed
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, CryptoLatestTradeRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from roles.credentials import bootstrap_trading_auth


LOOKBACK_CANDLES = 200
ENTRY_QTY = 2
ET = ZoneInfo("America/New_York")


def _usage() -> None:
    print("Usage: python demand.py <TICKER>")
    print("Example: python demand.py SPY")
    print("Example: python demand.py BTC/USD")


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


def _get_hourly_bars(symbol: str, credentials, lookback_candles: int):
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=45)
    is_crypto = "/" in symbol

    if is_crypto:
        client = CryptoHistoricalDataClient(credentials.api_key, credentials.secret_key)
        request = CryptoBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Hour,
            start=start,
            end=now,
            limit=lookback_candles + 50,
        )
        bars_response = client.get_crypto_bars(request)
        bars = _extract_bars(bars_response, symbol)

        if not bars:
            # Some symbols parse correctly but return no market data in the account's feed.
            latest_trade = client.get_crypto_latest_trade(
                CryptoLatestTradeRequest(symbol_or_symbols=symbol)
            )
            latest_item = latest_trade.get(symbol) if isinstance(latest_trade, dict) else None
            if latest_item is None:
                raise RuntimeError(
                    f"No crypto market data available for {symbol} in this Alpaca account/feed"
                )
    else:
        client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Hour,
            start=start,
            end=now,
            limit=lookback_candles + 50,
            feed=DataFeed.IEX,
        )
        bars_response = client.get_stock_bars(request)

    bars = _extract_bars(bars_response, symbol)
    if len(bars) < lookback_candles:
        raise RuntimeError(f"Need at least {lookback_candles} hourly candles, got {len(bars)}")
    return bars[-lookback_candles:]


def _candle_range(bar) -> float:
    high = float(getattr(bar, "high"))
    low = float(getattr(bar, "low"))
    return max(high - low, 0.0)


def _format_bar_time_utc_et(bar) -> str:
    ts = getattr(bar, "timestamp", None)
    if ts is None:
        return "N/A"
    if getattr(ts, "tzinfo", None) is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ts_utc = ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    ts_et = ts.astimezone(ET).strftime("%Y-%m-%d %H:%M:%S %Z")
    return f"{ts_utc} | {ts_et}"


def _find_demand_pattern(bars: list):
    ranges = [_candle_range(bar) for bar in bars]
    avg_range = sum(ranges) / len(ranges)

    # Use most recent valid signal by scanning from newest toward oldest.
    for idx in range(len(bars) - 1, 0, -1):
        current_range = ranges[idx]
        previous_range = ranges[idx - 1]
        if current_range >= 2.0 * avg_range and previous_range < avg_range:
            current_bar = bars[idx]
            previous_bar = bars[idx - 1]

            if previous_range <= current_range:
                smaller_bar = previous_bar
                smaller_range = previous_range
                smaller_label = "previous"
            else:
                smaller_bar = current_bar
                smaller_range = current_range
                smaller_label = "current"

            buy_point = float(getattr(smaller_bar, "high"))
            stop_loss = float(getattr(smaller_bar, "low"))

            return {
                "avg_range": avg_range,
                "signal_index": idx,
                "current_range": current_range,
                "previous_range": previous_range,
                "smaller_label": smaller_label,
                "smaller_range": smaller_range,
                "current_time": _format_bar_time_utc_et(current_bar),
                "previous_time": _format_bar_time_utc_et(previous_bar),
                "smaller_time": _format_bar_time_utc_et(smaller_bar),
                "buy_point": buy_point,
                "stop_loss": stop_loss,
            }

    return None


def _launch_fomo_trade(symbol: str, buy_point: float, stop_loss: float, target1_price: float, target2_price: float) -> subprocess.Popen:
    script_path = Path(__file__).resolve().parent / "fomo_trade.py"
    command = [
        sys.executable,
        str(script_path),
        symbol,
        str(ENTRY_QTY),
        f"{buy_point:.2f}",
        f"{stop_loss:.2f}",
        f"{target1_price:.2f}",
        f"{target2_price:.2f}",
    ]

    print("\nLaunching fomo_trade.py with:")
    print("  " + " ".join(command))
    return subprocess.Popen(command)


def main() -> int:
    if len(sys.argv) != 2:
        _usage()
        return 1

    symbol = sys.argv[1].strip().upper()
    if not symbol:
        _usage()
        return 1

    try:
        credentials, _ = bootstrap_trading_auth("demand.py")
    except Exception as exc:
        print(f"Authentication failed: {exc}")
        return 1

    try:
        bars = _get_hourly_bars(symbol, credentials, LOOKBACK_CANDLES)
    except Exception as exc:
        print(f"Error fetching hourly bars for {symbol}: {exc}")
        return 1

    pattern = _find_demand_pattern(bars)
    if pattern is None:
        print("No valid demand pattern found in the last 200 hourly candles.")
        return 1

    buy_point = pattern["buy_point"]
    stop_loss = pattern["stop_loss"]
    risk = buy_point - stop_loss
    if risk <= 0:
        print(
            "Invalid computed levels from detected pattern: "
            f"buy_point={buy_point:.4f}, stop_loss={stop_loss:.4f}"
        )
        return 1

    target1_price = buy_point + risk
    target2_price = buy_point + (3.0 * risk)

    print(f"Symbol: {symbol}")
    print(f"Hourly candles analyzed: {len(bars)}")
    print(f"Average candle range: {pattern['avg_range']:.4f}")
    print(f"Signal candle pair index: prev={pattern['signal_index']-1}, curr={pattern['signal_index']}")
    print(f"Signal previous candle time: {pattern['previous_time']}")
    print(f"Signal current candle time:  {pattern['current_time']}")
    print(f"Current candle range: {pattern['current_range']:.4f}")
    print(f"Previous candle range: {pattern['previous_range']:.4f}")
    print(f"Smaller candle used: {pattern['smaller_label']} (range={pattern['smaller_range']:.4f})")
    print(f"Smaller candle time: {pattern['smaller_time']}")
    print(f"Buy point: {buy_point:.4f}")
    print(f"Stop loss: {stop_loss:.4f}")
    print(f"Target 1 price: {target1_price:.4f}")
    print(f"Target 2 price: {target2_price:.4f}")

    try:
        process = _launch_fomo_trade(symbol, buy_point, stop_loss, target1_price, target2_price)
    except Exception as exc:
        print(f"Error launching fomo_trade.py: {exc}")
        return 1

    print(f"Started fomo_trade.py with PID {process.pid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())