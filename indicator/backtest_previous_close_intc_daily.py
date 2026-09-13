from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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


# Check for --help early in module execution
if len(sys.argv) > 1 and sys.argv[1] in ["--help", "-h", "help"]:
    print("""
BACKTEST_PREVIOUS_CLOSE_INTC_DAILY.PY - Intel Daily Backtest Strategy

SYNTAX:
  python indicator/backtest_previous_close_intc_daily.py [--help]

OPTIONS:
  --help, -h    Show this help message

DESCRIPTION:
  Backtests the previous close strategy specifically for Intel (INTC)
  Tests entry at daily open above previous close price
  Simulates 50% take-profit at 1% target, then trails with remaining 50%
  Uses stop loss at -1% from entry price
  Analyzes 100 days of historical data

TEST PARAMETERS:
  - Symbol: INTC (Intel)
  - Lookback: Last 100 trading days
  - Entry: Open > Previous Close
  - Stop Loss: -1% from entry
  - Take Profit 1: +1% (sell 50% of position)
  - Take Profit 2: +3% (sell remaining 50%)
  - Position Size: 100 shares per trade

EXAMPLES:
  python indicator/backtest_previous_close_intc_daily.py
    - Run full backtest on INTC daily data
    - Shows all trade outcomes and P/L summary

  python indicator/backtest_previous_close_intc_daily.py --help
    - Show this help message

OUTPUT:
  - Daily results with entry price and outcome
  - Trade summary: wins, losses, breakeven
  - Total P/L for 100-day test period
  - Win rate and average P/L per trade

NOTES:
  - Specific to INTC; modify SYMBOL variable for other stocks
  - Requires valid Alpaca API credentials
  - Uses historical daily candles (OHLC data)
  - Simulates position entry/exit within each day's price movement
  - Results are historical only; not guarantee of future performance
""")
    sys.exit(0)

SYMBOL = "INTC"
DAYS = 100
QTY = 100


@dataclass
class Result:
    date: str
    entered: bool
    outcome: str
    pnl: float
    entry: float


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


def _first_hit_in_segment(p0: float, p1: float, levels: list[tuple[str, float]]):
    if p1 > p0:
        hits = [(n, v) for n, v in levels if p0 < v <= p1]
        return min(hits, key=lambda x: x[1]) if hits else None
    if p1 < p0:
        hits = [(n, v) for n, v in levels if p1 <= v < p0]
        return max(hits, key=lambda x: x[1]) if hits else None
    return None


def _path(o: float, h: float, l: float, c: float):
    return [o, l, h, c] if c >= o else [o, h, l, c]


def simulate_day(prev_close: float, o: float, h: float, l: float, c: float) -> tuple[bool, str, float, float]:
    entry = float(math.ceil(prev_close))
    stop = round(entry * 0.99, 2)
    t1 = round(entry * 1.01, 2)
    t2 = round(entry * 1.03, 2)

    first_qty = QTY // 2
    second_qty = QTY - first_qty

    if h < entry:
        return False, "no_entry", 0.0, entry

    entered = True
    sold_first = False
    remaining = QTY
    pnl = 0.0

    points = _path(o, h, l, c)
    start = points[0]
    for end in points[1:]:
        while True:
            if remaining == 0:
                break
            levels = [("STOP", stop), ("T1", t1)] if not sold_first else [("BE", entry), ("T2", t2)]
            hit = _first_hit_in_segment(start, end, levels)
            if hit is None:
                break
            name, px = hit
            start = px

            if name == "STOP":
                pnl += remaining * (px - entry)
                return entered, "stopped_before_t1", round(pnl, 2), entry
            if name == "T1":
                pnl += first_qty * (px - entry)
                sold_first = True
                remaining = second_qty
                continue
            if name == "BE":
                pnl += remaining * (px - entry)
                return entered, "t1_then_breakeven", round(pnl, 2), entry
            if name == "T2":
                pnl += remaining * (px - entry)
                return entered, "t1_then_t2", round(pnl, 2), entry
        start = end

    pnl += remaining * (c - entry)
    return entered, ("t1_then_close" if sold_first else "entered_close"), round(pnl, 2), entry


def main() -> int:
    credentials, _ = bootstrap_trading_auth("backtest_previous_close_intc_daily.py")
    client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)

    now = datetime.now(timezone.utc)
    req = StockBarsRequest(
        symbol_or_symbols=SYMBOL,
        timeframe=TimeFrame.Day,
        start=now - timedelta(days=260),
        end=now,
        limit=300,
        feed=DataFeed.IEX,
    )
    bars = _extract_symbol_bars(client.get_stock_bars(req), SYMBOL)
    bars = sorted(bars, key=lambda b: b.timestamp)

    window = bars[-(DAYS + 1):]
    if len(window) < DAYS + 1:
        raise RuntimeError(f"Need {DAYS + 1} bars, got {len(window)}")

    results: list[Result] = []
    for i in range(1, len(window)):
        prev = window[i - 1]
        cur = window[i]
        entered, outcome, pnl, entry = simulate_day(
            float(prev.close), float(cur.open), float(cur.high), float(cur.low), float(cur.close)
        )
        results.append(Result(str(cur.timestamp.date()), entered, outcome, pnl, entry))

    total_pnl = round(sum(r.pnl for r in results), 2)
    traded_notional = round(sum(r.entry * QTY for r in results if r.entered), 2)
    ret_pct = (total_pnl / traded_notional * 100) if traded_notional else 0.0

    wins = sum(1 for r in results if r.pnl > 0)
    losses = sum(1 for r in results if r.pnl < 0)
    flats = sum(1 for r in results if r.pnl == 0)
    entered_days = sum(1 for r in results if r.entered)
    outcomes = Counter(r.outcome for r in results)

    print(f"Symbol: {SYMBOL}")
    print(f"Days tested: {len(results)}")
    print(f"Days with entry: {entered_days}")
    print(f"Wins/Losses/Flat: {wins}/{losses}/{flats}")
    print(f"Total PnL ($): {total_pnl:.2f}")
    print(f"Return on traded capital (%): {ret_pct:.2f}")
    print("Outcomes:")
    for k, v in sorted(outcomes.items()):
        print(f"  {k}: {v}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

