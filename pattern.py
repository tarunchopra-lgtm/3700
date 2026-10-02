#!/usr/bin/env python3
"""Report the latest daily occurrence of every TA-Lib candlestick pattern."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import talib
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

WORKSPACE_ROOT = Path(__file__).resolve().parent
RESULTS_DIR = WORKSPACE_ROOT / "results"
EASTERN_TZ = ZoneInfo("America/New_York")
DEFAULT_LOOKBACK_DAYS = 365


def fetch_daily_bars(symbol: str, lookback_days: int) -> list:
    """Fetch daily bars from Alpaca and return them oldest first."""
    from roles.credentials import CredentialsRole

    credentials = CredentialsRole()
    client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
    end = datetime.now(timezone.utc)
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=end - timedelta(days=lookback_days),
        end=end,
        feed=DataFeed.IEX,
    )
    response = client.get_stock_bars(request)
    data = getattr(response, "data", response)
    if not isinstance(data, dict):
        return []
    return list(data.get(symbol.upper(), data.get(symbol, [])))


def _pattern_functions() -> list[tuple[str, object]]:
    """Return the complete TA-Lib candlestick pattern function set."""
    return [
        (name, getattr(talib, name))
        for name in talib.get_functions()
        if name.startswith("CDL")
    ]


def find_latest_patterns(bars: list) -> list[dict[str, object]]:
    """Find the newest occurrence of each candlestick pattern."""
    if not bars:
        return []

    opens = np.asarray([float(bar.open) for bar in bars], dtype=float)
    highs = np.asarray([float(bar.high) for bar in bars], dtype=float)
    lows = np.asarray([float(bar.low) for bar in bars], dtype=float)
    closes = np.asarray([float(bar.close) for bar in bars], dtype=float)
    findings = []

    for name, detector in _pattern_functions():
        values = np.asarray(detector(opens, highs, lows, closes))
        occurrence_indexes = np.flatnonzero(values != 0)
        if not len(occurrence_indexes):
            continue

        index = int(occurrence_indexes[-1])
        bar = bars[index]
        value = int(values[index])
        timestamp = bar.timestamp
        if timestamp.tzinfo is not None:
            timestamp = timestamp.astimezone(EASTERN_TZ)
        findings.append({
            "name": name,
            "date": timestamp.date().isoformat(),
            "direction": "Bullish" if value > 0 else "Bearish",
            "signal": value,
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "range": float(bar.high) - float(bar.low),
        })

    return sorted(findings, key=lambda finding: (str(finding["date"]), str(finding["name"])), reverse=True)


def write_report(symbol: str, bars: list, findings: list[dict[str, object]], lookback_days: int) -> Path:
    """Write the ticker pattern report and return its path."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = RESULTS_DIR / f"{symbol}-pattern.output"
    generated = datetime.now(EASTERN_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")

    with report_path.open("w", encoding="utf-8") as report:
        report.write("=" * 100 + "\n")
        report.write(f"CANDLESTICK PATTERNS - {symbol}\n")
        report.write(f"Generated: {generated}\n")
        report.write(f"Lookback: {lookback_days} calendar days\n")
        report.write("Each pattern shows only its most recent occurrence.\n")
        report.write("=" * 100 + "\n\n")

        if not bars:
            report.write("No daily bars returned by Alpaca.\n")
        elif not findings:
            report.write("No candlestick patterns found in the requested lookback.\n")
        else:
            report.write(f"Latest pattern occurrences: {len(findings)}\n\n")
            for finding in findings:
                report.write(
                    f"{finding['name']:<22} | {finding['direction']:<8} | "
                    f"{finding['date']} | Signal: {finding['signal']:>4} | "
                    f"OHLC: {finding['open']:.2f}/{finding['high']:.2f}/"
                    f"{finding['low']:.2f}/{finding['close']:.2f} | "
                    f"Candle range: ${finding['range']:.2f}\n"
                )

    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find the latest daily TA-Lib candlestick pattern occurrence for one ticker."
    )
    parser.add_argument("ticker", help="Stock ticker, for example AAPL")
    parser.add_argument(
        "--lookback",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help=f"Calendar days of daily bars to inspect (default: {DEFAULT_LOOKBACK_DAYS})",
    )
    args = parser.parse_args()
    symbol = args.ticker.strip().upper()
    if not symbol or args.lookback <= 0:
        parser.error("ticker must be non-empty and --lookback must be positive")

    try:
        bars = fetch_daily_bars(symbol, args.lookback)
        findings = find_latest_patterns(bars)
        report_path = write_report(symbol, bars, findings, args.lookback)
    except Exception as error:
        print(f"Pattern scan failed for {symbol}: {error}", file=sys.stderr)
        return 1

    print(f"[OK] {symbol}: found {len(findings)} latest pattern occurrences")
    print(f"[OK] Report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
