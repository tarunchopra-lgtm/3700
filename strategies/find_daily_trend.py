#!/usr/bin/env python3
"""Find symbols breaking descending daily resistance today across lists/*.txt.

Usage:
    python strategies/find_daily_trend.py                    # Scan all lists
    python strategies/find_daily_trend.py nasdaq.txt spy.txt # Scan specific lists
    python strategies/find_daily_trend.py --email            # Scan & email results
    python strategies/find_daily_trend.py INTC               # Detailed analysis for INTC
    python strategies/find_daily_trend.py BKR                # Show chart & trend for BKR

With no arguments, every .txt file in lists/ is scanned and the top 10 matches
are written to lists/today-breakout. When a ticker symbol is provided, detailed
analysis with ASCII chart is shown instead.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
LISTS_DIR = WORKSPACE_ROOT / "lists"
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth
from roles.email_notify import send_email
from strategies.long_trend import ENTRY_LOOKBACK, _first_cent_above, _fit_trend


ET = ZoneInfo("America/New_York")
BATCH_SIZE = 100
HISTORY_DAYS = 50
TOP_COUNT = 10
# No .txt suffix keeps this output from being re-read as an input universe.
BREAKOUT_FILE = LISTS_DIR / "today-breakout"


def _resolve_list_files(arguments: list[str]) -> list[Path]:
    if not arguments:
        files = sorted(LISTS_DIR.glob("*.txt"))
    else:
        files = []
        for argument in arguments:
            path = Path(argument)
            if not path.is_absolute():
                path = LISTS_DIR / path
            if path.suffix.lower() != ".txt":
                path = path.with_suffix(".txt")
            files.append(path)

    missing = [str(path) for path in files if not path.exists()]
    if missing:
        raise RuntimeError(f"List file(s) not found: {', '.join(missing)}")
    if not files:
        raise RuntimeError(f"No .txt list files found in {LISTS_DIR}")
    return files


def _load_universe(files: list[Path]) -> tuple[list[str], dict[str, set[str]]]:
    memberships: dict[str, set[str]] = defaultdict(set)
    for path in files:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            symbol = raw_line.split("#", 1)[0].strip().upper()
            if symbol:
                memberships[symbol].add(path.stem)
    return sorted(memberships), memberships


def _chunks(values: list[str], size: int):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _extract_bar_map(response) -> dict[str, list]:
    data = getattr(response, "data", None)
    if data is None and isinstance(response, dict):
        data = response.get("data")
    if not isinstance(data, dict):
        return {}
    return {str(symbol).upper(): list(bars) for symbol, bars in data.items()}


def _bar_date_et(bar) -> date | None:
    timestamp = getattr(bar, "timestamp", None)
    if timestamp is None:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(ET).date()


def _fetch_daily_bars(
    data_client: StockHistoricalDataClient,
    symbols: list[str],
) -> dict[str, list]:
    now = datetime.now(timezone.utc)
    all_bars: dict[str, list] = {}
    for number, batch in enumerate(_chunks(symbols, BATCH_SIZE), start=1):
        print(
            f"Loading daily bars batch {number}/{(len(symbols) + BATCH_SIZE - 1) // BATCH_SIZE}...",
            file=sys.stderr,
        )
        response = data_client.get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=batch,
                timeframe=TimeFrame.Day,
                start=now - timedelta(days=HISTORY_DAYS),
                end=now,
                feed=DataFeed.IEX,
            )
        )
        all_bars.update(_extract_bar_map(response))
    return all_bars


def _fetch_current_prices(
    data_client: StockHistoricalDataClient,
    symbols: list[str],
) -> dict[str, float]:
    """Fetch latest trade prices (works pre-market and post-market)."""
    current_prices: dict[str, float] = {}
    for number, batch in enumerate(_chunks(symbols, BATCH_SIZE), start=1):
        try:
            response = data_client.get_stock_latest_trade(
                StockLatestTradeRequest(symbol_or_symbols=batch)
            )
            if isinstance(response, dict):
                for symbol, trade in response.items():
                    if trade:
                        price = float(getattr(trade, "price", 0))
                        if price > 0:
                            current_prices[str(symbol).upper()] = price
        except Exception as exc:
            print(f"Warning: Could not fetch current prices for batch: {exc}", file=sys.stderr)
    return current_prices


def _today_breakout(bars: list, today_et: date, current_price: float | None = None):
    completed = [bar for bar in bars if _bar_date_et(bar) and _bar_date_et(bar) < today_et]
    today_bars = [bar for bar in bars if _bar_date_et(bar) == today_et]
    if len(completed) < ENTRY_LOOKBACK:
        return None

    prior = completed[-ENTRY_LOOKBACK:]
    highs = [float(getattr(bar, "high")) for bar in prior]
    resistance = _fit_trend(highs)
    if resistance.slope >= 0:
        return None

    yesterday_close = float(getattr(prior[-1], "close"))
    
    # Calculate trend line value at yesterday's position (x = ENTRY_LOOKBACK - 1)
    yesterday_trend_value = resistance.intercept + resistance.slope * (ENTRY_LOOKBACK - 1)
    # Calculate trend line value at today's position (x = ENTRY_LOOKBACK)
    today_trend_value = resistance.intercept + resistance.slope * ENTRY_LOOKBACK
    
    trigger = _first_cent_above(today_trend_value)

    # Check today's open as confirmation of breakout
    if not today_bars:
        return None
    today_bar = today_bars[-1]
    today_open = float(getattr(today_bar, "open"))
    today_high = float(getattr(today_bar, "high"))

    # Use provided current_price for display, or fall back to today's close
    if current_price is None:
        current_price = float(getattr(today_bar, "close"))

    # Verify the breakout: 
    # 1. Yesterday must close BELOW the trend line at yesterday's position
    # 2. Today must open ABOVE the trend line at today's position
    if yesterday_close >= yesterday_trend_value or today_open < trigger:
        return None

    return {
        "current": current_price,
        "high": today_high,
        "trigger": trigger,
        "today_open": today_open,
        "slope": resistance.slope,
        "above_percent": (today_open - trigger) / trigger * 100.0,
        "prior_bars": prior,
        "resistance": resistance,
    }


def _draw_ascii_chart(symbol: str, prior_bars: list, resistance, today_open: float) -> None:
    """Draw ASCII chart showing downtrend line and today's open."""
    highs = [float(getattr(bar, "high")) for bar in prior_bars]
    lows = [float(getattr(bar, "low")) for bar in prior_bars]
    
    if not highs:
        return
    
    # Find price range
    min_price = min(min(highs), min(lows), today_open)
    max_price = max(max(highs), max(lows), today_open)
    price_range = max_price - min_price
    if price_range == 0:
        price_range = 1
    
    # Chart dimensions
    chart_height = 15
    chart_width = len(highs) + 2
    
    # Build the chart
    lines = []
    for row in range(chart_height, 0, -1):
        price_at_row = min_price + (row / chart_height) * price_range
        line = f"${price_at_row:7.2f} |"
        
        for col, (high, low) in enumerate(zip(highs, lows)):
            trend_val = resistance.intercept + resistance.slope * col
            
            # Place markers
            if low <= price_at_row <= high:
                line += "█"  # In range
            elif abs(trend_val - price_at_row) < (price_range / chart_height / 2):
                line += "─"  # Trend line
            else:
                line += " "
        
        # Today's open at the end
        if abs(today_open - price_at_row) < (price_range / chart_height / 2):
            line += "O"  # Today's open marker
        else:
            line += " "
        
        lines.append(line)
    
    # Bottom axis
    axis_line = "        +"
    for i in range(len(highs)):
        axis_line += "-"
    axis_line += "+"
    lines.append(axis_line)
    
    # Day numbers
    day_line = "        "
    for i in range(len(highs)):
        if i % 5 == 0:
            day_line += str(i % 10)
        else:
            day_line += " "
    day_line += "T"  # Today
    lines.append(day_line)
    
    # Print chart
    print(f"\n{symbol} - 15-Day Downtrend with Today's Open")
    print("=" * (chart_width + 10))
    for line in lines:
        print(line)
    print(f"\nLegend: █=Daily range | ─=Trend line | O=Today's open")


def _show_ticker_details(symbol: str, bars: list, current_price: float) -> None:
    """Show detailed analysis for a specific ticker."""
    today_et = datetime.now(ET).date()
    breakout = _today_breakout(bars, today_et, current_price)
    
    if breakout is None:
        print(f"\n{symbol}: No breakout detected")
        return
    
    print(f"\n{'='*70}")
    print(f"DETAILED ANALYSIS: {symbol}")
    print(f"{'='*70}")
    
    prior = breakout["prior_bars"]
    resistance = breakout["resistance"]
    
    # Show last 15 days
    print(f"\nLast 15 days of highs and lows:")
    print(f"{'Day':<6} {'Date':<12} {'High':<10} {'Low':<10} {'Trend Val':<12} {'Above?':<8}")
    print("-" * 60)
    
    for i, bar in enumerate(prior):
        bar_date = _bar_date_et(bar)
        high = float(getattr(bar, "high"))
        low = float(getattr(bar, "low"))
        trend_val = resistance.intercept + resistance.slope * i
        above = "✓ Yes" if high >= trend_val else "Below"
        print(f"{i:<6} {str(bar_date):<12} ${high:<9.2f} ${low:<9.2f} ${trend_val:<11.2f} {above:<8}")
    
    # Today's values
    print(f"\n{'Today (Day 15)':<6} {'NOW':<12} ${breakout['high']:<9.2f} {'N/A':<9} ${breakout['trigger']:<11.2f}")
    
    # Summary
    print(f"\n{'='*70}")
    print(f"BREAKOUT SUMMARY")
    print(f"{'='*70}")
    print(f"Trend Line Slope:          {resistance.slope:.6f}")
    print(f"Yesterday's Close:         ${float(getattr(prior[-1], 'close')):.2f}")
    print(f"Yesterday's Trend Value:   ${resistance.intercept + resistance.slope * (ENTRY_LOOKBACK - 1):.2f}")
    print(f"Today's Trend Line Value:  ${breakout['trigger']:.2f}")
    print(f"Today's Open:              ${breakout['today_open']:.2f}")
    print(f"Current Price:             ${breakout['current']:.2f}")
    print(f"Today's High:              ${breakout['high']:.2f}")
    print(f"Above Trend Line:          {breakout['above_percent']:.2f}%")
    print(f"{'='*70}\n")
    
    # Draw ASCII chart
    _draw_ascii_chart(symbol, prior, resistance, breakout['today_open'])


def main() -> int:
    arguments = sys.argv[1:]
    email_requested = "--email" in arguments
    list_arguments = [argument for argument in arguments if argument != "--email"]
    
    # Check if a specific ticker symbol is provided
    ticker_symbol = None
    if list_arguments and len(list_arguments) == 1 and list_arguments[0].upper().replace("/", "").replace("-", "").isalpha():
        ticker_symbol = list_arguments[0].upper()
        # Fetch data for this specific ticker
        try:
            credentials, _ = bootstrap_trading_auth("find_daily_trend.py")
            data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
            
            now = datetime.now(timezone.utc)
            response = data_client.get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=[ticker_symbol],
                    timeframe=TimeFrame.Day,
                    start=now - timedelta(days=HISTORY_DAYS),
                    end=now,
                    feed=DataFeed.IEX,
                )
            )
            
            bars_data = _extract_bar_map(response)
            bars = bars_data.get(ticker_symbol, [])
            
            if not bars:
                print(f"No data available for {ticker_symbol}")
                return 1
            
            # Get current price
            current_prices = _fetch_current_prices(data_client, [ticker_symbol])
            current_price = current_prices.get(ticker_symbol)
            
            # Show detailed analysis
            _show_ticker_details(ticker_symbol, bars, current_price)
            return 0
        
        except Exception as exc:
            print(f"Could not analyze {ticker_symbol}: {exc}")
            return 1
    
    # Normal mode: scan all lists
    try:
        files = _resolve_list_files(list_arguments)
        symbols, memberships = _load_universe(files)
    except Exception as exc:
        print(f"Could not load ticker lists: {exc}")
        return 1

    try:
        credentials, _ = bootstrap_trading_auth("find_daily_trend.py")
        data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
        bars_by_symbol = _fetch_daily_bars(data_client, symbols)
        current_prices = _fetch_current_prices(data_client, symbols)
    except Exception as exc:
        print(f"Could not load market data: {exc}")
        return 1

    today_et = datetime.now(ET).date()
    matches = []
    unavailable = []
    for symbol in symbols:
        bars = bars_by_symbol.get(symbol, [])
        if not bars:
            unavailable.append(symbol)
            continue
        current_price = current_prices.get(symbol)
        breakout = _today_breakout(bars, today_et, current_price)
        if breakout is not None:
            matches.append((symbol, breakout))

    ranked = sorted(matches, key=lambda item: item[1]["above_percent"], reverse=True)
    top_matches = ranked[:TOP_COUNT]

    lines = [
        f"DAILY DOWNTREND BREAKS - {today_et}",
        (
            f"Lists: {', '.join(path.name for path in files)} | "
            f"unique symbols: {len(symbols)} | matches: {len(ranked)}"
        ),
    ]
    if not ranked:
        lines.append("No symbols are currently above a newly broken descending 15-day high trend today.")
    else:
        for symbol, breakout in ranked:
            sources = ",".join(sorted(memberships[symbol]))
            lines.append(
                f"{symbol} [{sources}]: TODAY BREAK ${breakout['trigger']:.2f} | "
                f"TODAY OPEN ${breakout['today_open']:.2f} | "
                f"current ${breakout['current']:.2f} | high ${breakout['high']:.2f} | "
                f"above {breakout['above_percent']:.2f}% | slope {breakout['slope']:.4f}"
            )

    top_symbols = [symbol for symbol, _ in top_matches]
    BREAKOUT_FILE.write_text(
        "\n".join(top_symbols) + ("\n" if top_symbols else ""), encoding="utf-8"
    )
    lines.append(
        f"Top {len(top_symbols)} written to {BREAKOUT_FILE.name}: "
        f"{', '.join(top_symbols) if top_symbols else 'none'}"
    )

    if unavailable:
        lines.append(f"Unavailable/no daily data: {len(unavailable)} ({', '.join(unavailable)})")

    print()
    for line in lines:
        print(line)

    if email_requested:
        subject = f"Daily breakouts {today_et}: {len(ranked)} match(es)"
        if top_symbols:
            subject += f" | top: {', '.join(top_symbols[:5])}"
        try:
            recipient = send_email(subject, "\n".join(lines) + "\n")
            print(f"Emailed report to {recipient}")
        except Exception as exc:
            print(f"Could not email report: {exc}")
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
