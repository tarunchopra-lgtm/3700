#!/usr/bin/env python3
"""Compare long_trend results across 0.1%-1.0% daily-volume candles.

Usage:
    python strategies/optimize_long_trend.py <TICKER>

The report uses 14 completed trading days' average IEX volume. P/L is shown per
share so it can be multiplied by any desired position size.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from indicator.vol_finder import calculate_average_daily_volume
from roles.credentials import bootstrap_trading_auth
from strategies.long_trend import ENTRY_LOOKBACK, EXIT_LOOKBACK, _first_cent_above, _fit_trend


BACKTEST_CANDLES = 500
HISTORY_DAYS = 45
PERCENTAGES = tuple(step / 10.0 for step in range(1, 11))


@dataclass(frozen=True)
class BacktestResult:
    percent: float
    volume_per_candle: int
    candle_count: int
    trades: int
    wins: int
    losses: int
    gross_profit: float
    gross_loss: float
    net_profit: float
    win_rate: float
    profit_factor: float | None


def _fetch_minute_bars(
    data_client: StockHistoricalDataClient,
    symbol: str,
) -> pd.DataFrame:
    end = datetime.now(timezone.utc)
    response = data_client.get_stock_bars(
        StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Minute,
            start=end - timedelta(days=HISTORY_DAYS),
            end=end,
            feed=DataFeed.IEX,
        )
    )
    frame = response.df
    if frame.empty:
        raise RuntimeError(f"No minute bars returned for {symbol}")
    if "symbol" in frame.index.names:
        try:
            frame = frame.xs(symbol, level="symbol")
        except KeyError as exc:
            raise RuntimeError(f"No minute bars returned for {symbol}") from exc
    return frame.sort_index().reset_index()


def _build_volume_candles(frame: pd.DataFrame, volume_target: int) -> pd.DataFrame:
    candles: list[dict] = []
    current = None

    for row in frame.itertuples(index=False):
        row_volume = float(row.volume)
        if row_volume <= 0:
            continue

        if current is None:
            current = {
                "timestamp": row.timestamp,
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "volume": 0.0,
            }

        current["high"] = max(current["high"], float(row.high))
        current["low"] = min(current["low"], float(row.low))
        current["close"] = float(row.close)
        current["volume"] += row_volume

        if current["volume"] >= volume_target:
            candles.append(current)
            current = None

    return pd.DataFrame(candles).tail(BACKTEST_CANDLES).reset_index(drop=True)


def _exit_fill(open_price: float, exit_level: float) -> float:
    return min(open_price, exit_level)


def _backtest(candles: pd.DataFrame, percent: float, volume_target: int) -> BacktestResult:
    profits: list[float] = []
    in_position = False
    entry_price = 0.0
    hard_stop = 0.0

    for index in range(ENTRY_LOOKBACK, len(candles)):
        candle = candles.iloc[index]

        if in_position:
            prior_exit = candles.iloc[index - EXIT_LOOKBACK:index]
            exit_trend = _fit_trend([float(value) for value in prior_exit["low"]])
            exit_level = max(hard_stop, exit_trend.projected)
            if float(candle["low"]) < exit_level:
                sell_price = _exit_fill(float(candle["open"]), exit_level)
                profits.append(sell_price - entry_price)
                in_position = False
                continue

        prior_entry = candles.iloc[index - ENTRY_LOOKBACK:index]
        resistance = _fit_trend([float(value) for value in prior_entry["high"]])
        if resistance.slope >= 0:
            continue

        breakout_price = _first_cent_above(resistance.projected)
        if float(candle["high"]) >= breakout_price:
            entry_price = max(float(candle["open"]), breakout_price)
            hard_stop = float(prior_entry["low"].min())
            in_position = True

    if in_position:
        profits.append(float(candles.iloc[-1]["close"]) - entry_price)

    wins = sum(profit > 0 for profit in profits)
    losses = sum(profit <= 0 for profit in profits)
    gross_profit = sum(profit for profit in profits if profit > 0)
    gross_loss = abs(sum(profit for profit in profits if profit < 0))
    net_profit = sum(profits)
    win_rate = wins / len(profits) * 100.0 if profits else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None

    return BacktestResult(
        percent=percent,
        volume_per_candle=volume_target,
        candle_count=len(candles),
        trades=len(profits),
        wins=wins,
        losses=losses,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        net_profit=net_profit,
        win_rate=win_rate,
        profit_factor=profit_factor,
    )


def _format_profit_factor(value: float | None, trades: int) -> str:
    if trades == 0:
        return "N/A"
    if value is None:
        return "INF"
    return f"{value:.2f}"


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print("Usage: python strategies/optimize_long_trend.py <TICKER>")
        return 1

    symbol = sys.argv[1].strip().upper()

    try:
        credentials, _ = bootstrap_trading_auth("optimize_long_trend.py")
        data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
        average_daily_volume, _ = calculate_average_daily_volume(data_client, symbol)
        minute_bars = _fetch_minute_bars(data_client, symbol)
    except Exception as exc:
        print(f"Could not load optimization data for {symbol}: {exc}")
        return 1

    print(f"\n{symbol} LONG TREND OPTIMIZER")
    print(f"14-day average IEX volume: {average_daily_volume:,.0f} shares/day")
    print(f"Backtest window: most recent {BACKTEST_CANDLES} completed candles per setting")
    print("P/L values are price dollars per share, before slippage, fees, and taxes.\n")
    print(
        f"{'VOL%':>5} {'SHARES/CANDLE':>14} {'CANDLES':>8} {'TRADES':>7} "
        f"{'W/L':>9} {'WIN%':>7} {'GROSS+':>10} {'GROSS-':>10} {'NET/SH':>10} {'PF':>7}"
    )
    print("-" * 105)

    results: list[BacktestResult] = []
    for percent in PERCENTAGES:
        volume_target = max(1, round(average_daily_volume * percent / 100.0))
        candles = _build_volume_candles(minute_bars, volume_target)
        result = _backtest(candles, percent, volume_target)
        results.append(result)
        print(
            f"{percent:>4.1f}% {volume_target:>14,} {result.candle_count:>8} "
            f"{result.trades:>7} {f'{result.wins}/{result.losses}':>9} "
            f"{result.win_rate:>6.1f}% ${result.gross_profit:>8.2f} "
            f"${result.gross_loss:>8.2f} ${result.net_profit:>8.2f} "
            f"{_format_profit_factor(result.profit_factor, result.trades):>7}"
        )

    eligible = [result for result in results if result.trades > 0]
    if not eligible:
        print("\nNo completed trades were found for any volume setting.")
        return 0

    best = max(eligible, key=lambda result: (result.net_profit, result.profit_factor or float("inf")))
    print(
        f"\nBest net result: {best.percent:.1f}% = {best.volume_per_candle:,} shares/candle | "
        f"trades={best.trades} | win={best.win_rate:.1f}% | "
        f"net=${best.net_profit:.2f}/share | "
        f"profit factor={_format_profit_factor(best.profit_factor, best.trades)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
