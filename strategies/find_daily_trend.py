#!/usr/bin/env python3
"""Find symbols breaking descending daily resistance today across lists/*.txt.

Usage:
    python strategies/find_daily_trend.py
    python strategies/find_daily_trend.py nasdaq.txt spy.txt
    python strategies/find_daily_trend.py --email

With no list arguments, every .txt file in lists/ is scanned. The top 10 matches
are always written to lists/today-breakout.
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
    resistance = _fit_trend([float(getattr(bar, "high")) for bar in prior])
    if resistance.slope >= 0:
        return None

    trigger = _first_cent_above(resistance.projected)
    yesterday_close = float(getattr(prior[-1], "close"))

    # Use provided current_price, or fall back to today's bar if available
    if current_price is None:
        if not today_bars:
            return None
        today_bar = today_bars[-1]
        current_price = float(getattr(today_bar, "close"))
        today_high = float(getattr(today_bar, "high"))
    else:
        # Use pre-market/current price, and today's high if available
        if today_bars:
            today_high = float(getattr(today_bars[-1], "high"))
        else:
            today_high = current_price  # Pre-market: current price is also the high so far

    if yesterday_close >= trigger or current_price < trigger:
        return None

    return {
        "current": current_price,
        "high": today_high,
        "trigger": trigger,
        "slope": resistance.slope,
        "above_percent": (current_price - trigger) / trigger * 100.0,
    }


def main() -> int:
    arguments = sys.argv[1:]
    email_requested = "--email" in arguments
    list_arguments = [argument for argument in arguments if argument != "--email"]

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
