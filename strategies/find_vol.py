#!/usr/bin/env python3
"""Recommend a volume-candle size for the long_trend strategy.

Usage:
    python strategies/find_vol.py <TICKER>
"""

from __future__ import annotations

import sys
from pathlib import Path

from alpaca.data.historical import StockHistoricalDataClient

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from indicator.vol_finder import calculate_average_daily_volume
from roles.credentials import bootstrap_trading_auth
from strategies.optimize_long_trend import (
    BACKTEST_CANDLES,
    PERCENTAGES,
    BacktestResult,
    _backtest,
    _build_volume_candles,
    _fetch_minute_bars,
    _format_profit_factor,
)


MINIMUM_TRADES = 10


def _rank_key(result: BacktestResult) -> tuple[float, float, float]:
    profit_factor = result.profit_factor if result.profit_factor is not None else float("inf")
    return result.net_profit, profit_factor, result.win_rate


def _is_recommended(result: BacktestResult) -> bool:
    return (
        result.trades >= MINIMUM_TRADES
        and result.net_profit > 0
        and result.profit_factor is not None
        and result.profit_factor > 1.0
    )


def main() -> int:
    # Handle help flag
    if len(sys.argv) > 1 and sys.argv[1] in ["--help", "-h", "help"]:
        print("""
FIND_VOL.PY - Volume Candle Size Optimizer for long_trend.py

SYNTAX:
  python strategies/find_vol.py <TICKER> [--help]

REQUIRED:
  <TICKER>    Stock symbol to optimize (e.g., AAPL, SPY, INTC)

OPTIONS:
  --help, -h  Show this help message

DESCRIPTION:
  Analyzes historical minute bars to find optimal volume candle size
  Backtests multiple candle sizes (5% - 50% of average daily volume)
  Scores each size by profitability and trade frequency
  Recommends best size for use with long_trend.py strategy
  Uses trailing profit factor and win rate for scoring

OPTIMIZATION CRITERIA:
  - Minimum 10 completed trades for qualification
  - Positive net P&L (profit > 0)
  - Profit factor > 1.0 (wins > losses)
  - Tests recent 15 completed candles
  - Ranks by net profit, then profit factor, then win rate

VOLUME CANDLE SIZING:
  Testing percentages: 5%, 10%, 15%, 20%, 30%, 40%, 50% of ADV
  Smaller volumes:
  - More candles formed = more signals
  - Faster pattern confirmation
  - More trades tested
  - Higher noise/whipsaw risk

  Larger volumes:
  - Fewer candles = cleaner signals
  - Better trend confirmation
  - Fewer trades but higher quality
  - Less noise

EXAMPLES:
  python strategies/find_vol.py AAPL
    - Test Apple with various candle sizes
    - Recommend optimal size for long_trend.py

  python strategies/find_vol.py SPY
    - Analyze S&P 500 ETF
    - Find best volume candle for trend trading

OUTPUT:
  Console table:
  - VOL%: Percentage of average daily volume
  - CANDLE SIZE: Actual share volume per candle
  - TRADES: Number of completed trades tested
  - W/L: Wins/Losses ratio
  - WIN%: Percentage of winning trades
  - NET/SH: Net profit per share across all trades
  - PF: Profit Factor (gross profit / gross loss)
  - STATUS: QUALIFIES or REJECT

  Results:
  - 14-day average IEX volume (baseline)
  - Best tested size (most profitable)
  - Recommended size (passes all criteria)
  - Command to use in long_trend.py

RECOMMENDATION OUTPUT:
  If qualified:
  - Specific volume candle size recommended
  - Profit factor and win rate shown
  - Ready-to-run command for long_trend.py
  - Example: python long_trend.py AAPL 5 2500

  If rejected:
  - No size passed the criteria
  - Best size shown for reference
  - Suggests retesting after strategy changes

NOTES:
  - Requires valid Alpaca API credentials
  - Uses 14-day average volume from IEX data
  - Tests recent minute bars (lookback period)
  - Backtests on trend-break pattern detection
  - Use recommended size with long_trend.py
  - Re-run quarterly or after market changes
  - Paper account recommended for testing
  - Volume candle approach good for choppy markets
  - Recommendation is data-driven, not guaranteed
""")
        return 0
    
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print("Usage: python strategies/find_vol.py <TICKER> [--help]")
        return 1

    symbol = sys.argv[1].strip().upper()

    try:
        credentials, _ = bootstrap_trading_auth("find_vol.py")
        data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
        average_daily_volume, _ = calculate_average_daily_volume(data_client, symbol)
        minute_bars = _fetch_minute_bars(data_client, symbol)
    except Exception as exc:
        print(f"Could not load optimization data for {symbol}: {exc}")
        return 1

    print(f"\n{symbol} VOLUME CANDLE FINDER")
    print(f"14-day average IEX volume: {average_daily_volume:,.0f} shares/day")
    print(f"Testing the most recent {BACKTEST_CANDLES} completed candles per size")
    print("Recommendation requires: positive net P/L, profit factor > 1, and at least 10 trades.\n")
    print(
        f"{'VOL%':>5} {'CANDLE SIZE':>13} {'TRADES':>7} {'W/L':>9} "
        f"{'WIN%':>7} {'NET/SH':>10} {'PF':>7} {'STATUS':>12}"
    )
    print("-" * 82)

    results: list[BacktestResult] = []
    for percent in PERCENTAGES:
        volume_target = max(1, round(average_daily_volume * percent / 100.0))
        candles = _build_volume_candles(minute_bars, volume_target)
        result = _backtest(candles, percent, volume_target)
        results.append(result)
        status = "QUALIFIES" if _is_recommended(result) else "REJECT"
        print(
            f"{percent:>4.1f}% {volume_target:>13,} {result.trades:>7} "
            f"{f'{result.wins}/{result.losses}':>9} {result.win_rate:>6.1f}% "
            f"${result.net_profit:>8.2f} "
            f"{_format_profit_factor(result.profit_factor, result.trades):>7} {status:>12}"
        )

    traded = [result for result in results if result.trades > 0]
    if not traded:
        print("\nNO CANDLE SIZE FOUND: no completed trades occurred in the tested windows.")
        return 0

    best_tested = max(traded, key=_rank_key)
    qualified = [result for result in traded if _is_recommended(result)]

    print(
        f"\nBest tested size: {best_tested.volume_per_candle:,} shares "
        f"({best_tested.percent:.1f}% of average daily volume) | "
        f"net=${best_tested.net_profit:.2f}/share | "
        f"PF={_format_profit_factor(best_tested.profit_factor, best_tested.trades)}"
    )

    if not qualified:
        print("RECOMMENDATION: NONE. No tested candle size demonstrated a profitable trend-break edge.")
        print(
            f"Do not select {best_tested.volume_per_candle:,} merely because it lost the least; "
            "retest after changing the entry or exit rules."
        )
        return 0

    recommended = max(qualified, key=_rank_key)
    print(
        f"RECOMMENDED VOLUME CANDLE SIZE: {recommended.volume_per_candle:,} shares "
        f"({recommended.percent:.1f}%)"
    )
    print("Use with long_trend.py:")
    print(
        f"python strategies\\long_trend.py {symbol} <QUANTITY> "
        f"{recommended.volume_per_candle}"
    )
    return 0
        f"PF={_format_profit_factor(best_tested.profit_factor, best_tested.trades)}"
    )

    if not qualified:
        print("RECOMMENDATION: NONE. No tested candle size demonstrated a profitable trend-break edge.")
        print(
            f"Do not select {best_tested.volume_per_candle:,} merely because it lost the least; "
            "retest after changing the entry or exit rules."
        )
        return 0

    recommended = max(qualified, key=_rank_key)
    print(
        f"RECOMMENDED VOLUME CANDLE SIZE: {recommended.volume_per_candle:,} shares "
        f"({recommended.percent:.1f}%)"
    )
    print("Use with long_trend.py:")
    print(
        f"python strategies\\long_trend.py {symbol} <QUANTITY> "
        f"{recommended.volume_per_candle}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
