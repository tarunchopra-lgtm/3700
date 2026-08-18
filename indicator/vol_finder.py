#!/usr/bin/env python3
"""Calculate a stock's 14-day average volume and 1% volume-candle size.

Usage:
    python indicator/vol_finder.py <TICKER>

Example:
    python indicator/vol_finder.py MU
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
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


AVERAGE_DAYS = 14
VOLUME_CANDLE_PERCENT = 0.01
ET = ZoneInfo("America/New_York")


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


def _bar_date_et(bar):
    timestamp = getattr(bar, "timestamp", None)
    if timestamp is None:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(ET).date()


def calculate_average_daily_volume(
    data_client: StockHistoricalDataClient,
    symbol: str,
    days: int = AVERAGE_DAYS,
) -> tuple[float, list]:
    now = datetime.now(timezone.utc)
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=now - timedelta(days=days * 4),
        end=now,
        limit=days * 3,
        feed=DataFeed.IEX,
    )
    bars = _extract_bars(data_client.get_stock_bars(request), symbol)
    today_et = now.astimezone(ET).date()
    completed_bars = [
        bar
        for bar in bars
        if _bar_date_et(bar) is not None
        and _bar_date_et(bar) < today_et
        and float(getattr(bar, "volume", 0)) > 0
    ][-days:]

    if len(completed_bars) < days:
        raise RuntimeError(
            f"Only {len(completed_bars)} completed daily bars returned for {symbol}; {days} required"
        )

    average_volume = sum(float(getattr(bar, "volume")) for bar in completed_bars) / days
    return average_volume, completed_bars


def calculate_volume_per_candle(average_daily_volume: float) -> int:
    return max(1, round(average_daily_volume * VOLUME_CANDLE_PERCENT))


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print("Usage: python indicator/vol_finder.py <TICKER>")
        return 1

    symbol = sys.argv[1].strip().upper()

    try:
        credentials, _ = bootstrap_trading_auth("vol_finder.py")
        data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
        average_volume, bars = calculate_average_daily_volume(data_client, symbol)
    except Exception as exc:
        print(f"Could not calculate volume for {symbol}: {exc}")
        return 1

    volume_per_candle = calculate_volume_per_candle(average_volume)
    first_date = _bar_date_et(bars[0])
    last_date = _bar_date_et(bars[-1])

    print(f"\nSymbol: {symbol}")
    print(f"Completed trading days: {len(bars)} ({first_date} through {last_date})")
    print(f"14-day average IEX volume: {average_volume:,.0f} shares/day")
    print(f"1% volume per candle: {volume_per_candle:,} shares")
    print("\nUse with plot_graph.py:")
    print(
        f"python indicator\\plot_graph.py --ticker {symbol} "
        f"--volume {volume_per_candle} --candles 500"
    )
    print(f"\nReusable value: {volume_per_candle}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
