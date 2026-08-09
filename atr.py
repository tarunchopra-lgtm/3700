#!/usr/bin/env python3
"""Print the daily Average True Range (ATR) for a stock.

Usage:
    python atr.py <TICKER>

The script prints a single ATR value computed from daily bars.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from dotenv import load_dotenv


ATR_PERIOD = 14


def _usage() -> None:
    print("Usage: python atr.py <TICKER>")


def _load_credentials() -> tuple[str, str]:
    base_dir = Path(__file__).resolve().parent
    env_path = base_dir / "env" / "credentials"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv(dotenv_path=base_dir / ".env")

    api_key = (
        os.getenv("ALPACA_API_KEY")
        or os.getenv("APCA_API_KEY_ID")
        or ""
    ).strip()
    secret_key = (
        os.getenv("ALPACA_API_SECRET")
        or os.getenv("ALPACA_SECRET_KEY")
        or os.getenv("APCA_API_SECRET_KEY")
        or ""
    ).strip()

    if not api_key or not secret_key:
        raise ValueError("Set Alpaca credentials in env/credentials or .env")

    return api_key, secret_key


def _extract_bars(bars_response, symbol: str):
    data = getattr(bars_response, "data", None)
    if data is None and isinstance(bars_response, dict):
        data = bars_response.get("data")

    if isinstance(data, dict):
        return list(data.get(symbol) or data.get(symbol.upper()) or next(iter(data.values()), []))

    return list(data) if data is not None else []


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


def _calculate_atr(data_client: StockHistoricalDataClient, symbol: str) -> float:
    now = datetime.now(timezone.utc)
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=now - timedelta(days=ATR_PERIOD * 4),
        end=now,
        limit=ATR_PERIOD + 1,
        feed=DataFeed.IEX,
    )
    bars_response = data_client.get_stock_bars(request)
    bars = _extract_bars(bars_response, symbol)

    if len(bars) < ATR_PERIOD + 1:
        raise RuntimeError(f"Not enough daily bars to calculate {ATR_PERIOD}-day ATR for {symbol}")

    tr_values: list[float] = []
    previous_close = None
    for bar in bars[-(ATR_PERIOD + 1):]:
        tr_values.append(_true_range(bar, previous_close))
        previous_close = float(getattr(bar, "close"))

    if len(tr_values) < ATR_PERIOD:
        raise RuntimeError(f"Unable to calculate ATR for {symbol}")

    return sum(tr_values[-ATR_PERIOD:]) / ATR_PERIOD


def main() -> int:
    if len(sys.argv) != 2:
        _usage()
        return 1

    symbol = sys.argv[1].strip().upper()
    if not symbol:
        _usage()
        return 1

    try:
        api_key, secret_key = _load_credentials()
    except Exception as exc:
        print(f"Authentication failed: {exc}")
        return 1

    data_client = StockHistoricalDataClient(api_key, secret_key)

    try:
        atr_value = _calculate_atr(data_client, symbol)
    except Exception as exc:
        print(f"Error calculating ATR for {symbol}: {exc}")
        return 1

    print(f"{atr_value:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())