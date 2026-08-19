#!/usr/bin/env python3
"""Report the latest daily downtrend break and trailing-low exit per ticker.

Usage:
    python strategies/uptrend.py <TICKER> [TICKER ...]

Example:
    python strategies/uptrend.py AAPL MU INTC
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
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
from strategies.long_trend import ENTRY_LOOKBACK, EXIT_LOOKBACK, _first_cent_above, _fit_trend


LOOKBACK_CANDLES = 200
ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class DailyCandle:
    day: date
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class TrendTrade:
    entry_date: date
    entry_price: float
    entry_trend: float
    hard_stop: float
    peak_price: float
    exit_date: date | None
    exit_price: float | None
    final_price: float

    @property
    def is_open(self) -> bool:
        return self.exit_date is None

    @property
    def profit_per_share(self) -> float:
        final = self.final_price if self.is_open else float(self.exit_price)
        return final - self.entry_price


def _extract_bars(response, symbol: str):
    data = getattr(response, "data", None)
    if data is None and isinstance(response, dict):
        data = response.get("data")
    if isinstance(data, dict):
        symbol_bars = data.get(symbol) or data.get(symbol.upper())
        return list(symbol_bars or [])
    return list(data or [])


def _bar_date_et(bar) -> date | None:
    timestamp = getattr(bar, "timestamp", None)
    if timestamp is None:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(ET).date()


def _fetch_completed_daily_candles(
    data_client: StockHistoricalDataClient,
    symbol: str,
) -> list[DailyCandle]:
    now = datetime.now(timezone.utc)
    response = data_client.get_stock_bars(
        StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=now - timedelta(days=LOOKBACK_CANDLES * 2),
            end=now,
            limit=LOOKBACK_CANDLES + 100,
            feed=DataFeed.IEX,
        )
    )
    today_et = now.astimezone(ET).date()
    candles: list[DailyCandle] = []
    for bar in _extract_bars(response, symbol):
        bar_date = _bar_date_et(bar)
        if bar_date is None or bar_date >= today_et:
            continue
        candles.append(
            DailyCandle(
                day=bar_date,
                open=float(getattr(bar, "open")),
                high=float(getattr(bar, "high")),
                low=float(getattr(bar, "low")),
                close=float(getattr(bar, "close")),
            )
        )

    candles = candles[-LOOKBACK_CANDLES:]
    if len(candles) < LOOKBACK_CANDLES:
        raise RuntimeError(
            f"only {len(candles)} completed daily candles returned; {LOOKBACK_CANDLES} required"
        )
    return candles


def _entry_fill(candle: DailyCandle, breakout_price: float) -> float:
    return max(candle.open, breakout_price)


def _exit_fill(candle: DailyCandle, exit_level: float) -> float:
    return min(candle.open, exit_level)


def find_trend_trades(candles: list[DailyCandle]) -> list[TrendTrade]:
    trades: list[TrendTrade] = []
    entry_date: date | None = None
    entry_price = 0.0
    entry_trend = 0.0
    hard_stop = 0.0
    peak_price = 0.0

    for index in range(ENTRY_LOOKBACK, len(candles)):
        candle = candles[index]

        if entry_date is not None:
            prior_exit = candles[index - EXIT_LOOKBACK:index]
            exit_trend = _fit_trend([item.low for item in prior_exit])
            exit_level = max(hard_stop, exit_trend.projected)
            if candle.low < exit_level:
                exit_price = _exit_fill(candle, exit_level)
                trades.append(
                    TrendTrade(
                        entry_date=entry_date,
                        entry_price=entry_price,
                        entry_trend=entry_trend,
                        hard_stop=hard_stop,
                        peak_price=peak_price,
                        exit_date=candle.day,
                        exit_price=exit_price,
                        final_price=exit_price,
                    )
                )
                entry_date = None
                continue
            peak_price = max(peak_price, candle.high)

        prior_entry = candles[index - ENTRY_LOOKBACK:index]
        resistance = _fit_trend([item.high for item in prior_entry])
        if entry_date is not None or resistance.slope >= 0:
            continue

        breakout_price = _first_cent_above(resistance.projected)
        if candle.high >= breakout_price:
            entry_date = candle.day
            entry_price = _entry_fill(candle, breakout_price)
            entry_trend = resistance.projected
            hard_stop = min(item.low for item in prior_entry)
            peak_price = candle.high

    if entry_date is not None:
        latest = candles[-1]
        trades.append(
            TrendTrade(
                entry_date=entry_date,
                entry_price=entry_price,
                entry_trend=entry_trend,
                hard_stop=hard_stop,
                peak_price=peak_price,
                exit_date=None,
                exit_price=None,
                final_price=latest.close,
            )
        )

    return trades


def _parse_symbols(arguments: list[str]) -> list[str]:
    symbols: list[str] = []
    for argument in arguments:
        for value in argument.split(","):
            symbol = value.strip().upper()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
    return symbols


def _format_trade(symbol: str, trade: TrendTrade) -> str:
    if trade.is_open:
        return (
            f"{symbol}: BREAK {trade.entry_date} buy ${trade.entry_price:.2f} "
            f"(trend ${trade.entry_trend:.2f}, stop ${trade.hard_stop:.2f}) -> "
            f"OPEN ${trade.final_price:.2f}, peak ${trade.peak_price:.2f}, "
            f"P/L ${trade.profit_per_share:+.2f}/share"
        )
    return (
        f"{symbol}: BREAK {trade.entry_date} buy ${trade.entry_price:.2f} "
        f"(trend ${trade.entry_trend:.2f}) -> peak ${trade.peak_price:.2f} -> "
        f"EXIT {trade.exit_date} sell ${trade.exit_price:.2f}, "
        f"P/L ${trade.profit_per_share:+.2f}/share"
    )


def main() -> int:
    symbols = _parse_symbols(sys.argv[1:])
    if not symbols:
        print("Usage: python strategies/uptrend.py <TICKER> [TICKER ...]")
        return 1

    try:
        credentials, _ = bootstrap_trading_auth("uptrend.py")
    except Exception as exc:
        print(f"Authentication failed: {exc}")
        return 1

    data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
    for symbol in symbols:
        try:
            candles = _fetch_completed_daily_candles(data_client, symbol)
            trades = find_trend_trades(candles)
            if trades:
                print(_format_trade(symbol, trades[-1]))
            else:
                print(f"{symbol}: no descending 15-day high trend break in the last {LOOKBACK_CANDLES} completed daily candles")
        except Exception as exc:
            print(f"{symbol}: ERROR {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
