"""Find watchlist tickers reporting earnings during the next seven days.

The Nasdaq public earnings calendar is queried once per calendar day. Tickers
are collected from every file below the workspace ``lists`` directory.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LISTS_DIR = WORKSPACE_ROOT / "lists"
MARKET_TIMEZONE = ZoneInfo("America/New_York")
NASDAQ_EARNINGS_URL = "https://api.nasdaq.com/api/calendar/earnings"
TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
LOGGER = logging.getLogger("earnings_finder")


class EarningsCalendarError(RuntimeError):
    """Raised when the earnings calendar cannot be read."""


def load_symbols(lists_dir: Path) -> set[str]:
    """Collect unique ticker-like first tokens from files below ``lists_dir``."""
    symbols: set[str] = set()
    if not lists_dir.exists():
        raise EarningsCalendarError(f"Lists directory not found: {lists_dir}")

    for path in sorted(lists_dir.rglob("*")):
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as error:
            LOGGER.warning("Skipping %s: %s", path, error)
            continue

        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            first_token = line.split()[0].upper()
            if "=" in first_token or not TICKER_PATTERN.fullmatch(first_token):
                continue
            symbols.add(first_token)
    return symbols


def _time_label(raw_time: str | None) -> str:
    """Convert Nasdaq's timing code to a user-facing AM/PM label."""
    normalized = (raw_time or "").strip().lower()
    if normalized in {"time-pre-market", "time-before-open"}:
        return "AM"
    if normalized in {"time-after-hours", "time-after-market"}:
        return "PM"
    return "Not supplied"


def fetch_earnings_for_date(report_date: date) -> tuple[date, list[dict[str, str]]]:
    """Fetch Nasdaq earnings rows for one ET calendar date."""
    query = urllib.parse.urlencode({
        "fromdate": report_date.strftime("%m/%d/%Y"),
        "todate": report_date.strftime("%m/%d/%Y"),
        "limit": 5000,
    })
    request = urllib.request.Request(
        f"{NASDAQ_EARNINGS_URL}?{query}",
        headers={
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.nasdaq.com",
            "Referer": "https://www.nasdaq.com/",
            "User-Agent": "Mozilla/5.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except Exception as error:
        raise EarningsCalendarError(
            f"Could not fetch earnings for {report_date}: {error}"
        ) from error

    data = payload.get("data") or {}
    rows = data.get("rows") or []
    if not isinstance(rows, list):
        raise EarningsCalendarError(f"Unexpected Nasdaq response for {report_date}")

    as_of = str(data.get("asOf") or "").strip()
    try:
        effective_date = datetime.strptime(as_of, "%a, %b %d, %Y").date()
    except ValueError:
        effective_date = report_date
    return effective_date, [row for row in rows if isinstance(row, dict)]


def find_upcoming_earnings(symbols: set[str], start_date: date, days: int) -> list[dict[str, str]]:
    """Return matching earnings sorted by report date and ticker."""
    matches: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for offset in range(days):
        report_date = start_date + timedelta(days=offset)
        try:
            effective_date, rows = fetch_earnings_for_date(report_date)
        except EarningsCalendarError as error:
            LOGGER.error("%s", error)
            continue

        for row in rows:
            symbol = str(row.get("symbol") or "").strip().upper()
            if symbol not in symbols:
                continue
            result_key = (effective_date.isoformat(), symbol)
            if result_key in seen:
                continue
            seen.add(result_key)
            matches.append({
                "ticker": symbol,
                "date": effective_date.isoformat(),
                "timing": _time_label(str(row.get("time") or "")),
                "name": str(row.get("name") or ""),
            })
    return sorted(matches, key=lambda item: (item["date"], item["ticker"]))


def print_results(matches: list[dict[str, str]], symbols: set[str], start_date: date, days: int) -> None:
    end_date = start_date + timedelta(days=days - 1)
    print("=" * 72)
    print("UPCOMING EARNINGS FINDER")
    print("=" * 72)
    print(f"Scanned tickers: {len(symbols)}")
    print(f"Date range: {start_date} through {end_date} ({days} calendar days)")
    print()
    if not matches:
        print("No matching earnings found in the selected date range.")
        return

    print(f"{'TICKER':<10} {'EARNINGS DATE':<16} {'TIMING':<16} COMPANY")
    print("-" * 72)
    for item in matches:
        print(
            f"{item['ticker']:<10} {item['date']:<16} "
            f"{item['timing']:<16} {item['name']}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find watchlist tickers with earnings in the next seven days."
    )
    parser.add_argument(
        "--days", type=int, default=7,
        help="Number of calendar days to scan, starting today (default: 7)",
    )
    parser.add_argument(
        "--lists-dir", type=Path, default=DEFAULT_LISTS_DIR,
        help="Directory containing ticker list files",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    args = parse_args()
    if args.days < 1:
        LOGGER.error("--days must be at least 1")
        return 2

    start_date = datetime.now(MARKET_TIMEZONE).date()
    try:
        symbols = load_symbols(args.lists_dir)
        if not symbols:
            raise EarningsCalendarError(f"No ticker symbols found in {args.lists_dir}")
        matches = find_upcoming_earnings(symbols, start_date, args.days)
    except EarningsCalendarError as error:
        LOGGER.error("%s", error)
        return 1

    print_results(matches, symbols, start_date, args.days)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
