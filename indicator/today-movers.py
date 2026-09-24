#!/usr/bin/env python3
"""Report the top stock gainers for the current market day.

Usage:
    python indicator/today-movers.py
    python indicator/today-movers.py --top 10
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth

MOVERS_URL = "https://data.alpaca.markets/v1beta1/screener/stocks/movers"


def _get_movers(api_key: str, secret_key: str, top: int) -> dict:
    url = f"{MOVERS_URL}?{urlencode({'top': top})}"
    request = Request(
        url,
        headers={
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Mover request failed ({exc.code}): {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Mover request could not reach Alpaca: {exc.reason}") from exc


def _print_report(payload: dict) -> None:
    gainers = payload.get("gainers") or []
    updated = payload.get("last_updated", "unknown")

    print(f"Top {len(gainers)} stock gainers today")
    print(f"Market data updated: {updated}")
    print(f"{'#':>2}  {'Symbol':<8} {'Price':>12} {'Change':>12} {'Percent':>10}")
    print("-" * 49)

    for rank, mover in enumerate(gainers, start=1):
        symbol = str(mover.get("symbol", "?"))
        price = float(mover.get("price", 0))
        change = float(mover.get("change", 0))
        percent_change = float(mover.get("percent_change", 0))
        print(
            f"{rank:>2}  {symbol:<8} ${price:>10,.2f} "
            f"${change:>10,.2f} {percent_change:>9.2f}%"
        )

    if not gainers:
        print("No gainers were returned by Alpaca.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Report today's top stock gainers from Alpaca.")
    parser.add_argument("--top", type=int, default=10, help="Number of gainers to report (1-50).")
    args = parser.parse_args()

    if not 1 <= args.top <= 50:
        parser.error("--top must be between 1 and 50")

    try:
        credentials, _ = bootstrap_trading_auth("today-movers.py")
        if not credentials.paper:
            raise RuntimeError("ALPACA_PAPER is not enabled; refusing to use a non-paper account")
        payload = _get_movers(credentials.api_key, credentials.secret_key, args.top)
        _print_report(payload)
    except Exception as exc:
        print(f"Today movers failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())