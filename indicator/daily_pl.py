#!/usr/bin/env python3
"""Compute today's realized profit/loss from Alpaca fills.

Usage:
    python daily_pl.py [OUTPUT_FILE]

What it does:
- Reads today's filled orders from Alpaca
- Matches buys and sells FIFO per symbol to estimate realized P/L
- Prints per-symbol trade count, total profit/loss, and average P/L per trade
- Prints the overall daily profit/loss number
- Saves the daily summary to a JSON file for later max-daily-loss checks
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from pathlib import Path
import sys

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth


ET = ZoneInfo("America/New_York")
LOOKBACK_DAYS = 365
DEFAULT_STATE_FILE = Path(__file__).resolve().with_name("daily_pl_state.json")


@dataclass(frozen=True)
class FillRecord:
    symbol: str
    side: str
    qty: float
    price: float
    filled_at: datetime


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_symbol(symbol: Any) -> str:
    return str(symbol).replace("/", "").replace("-", "").upper()


def _get_order_side(order) -> str:
    side = getattr(order, "side", "")
    return (side.value if hasattr(side, "value") else str(side)).upper()


def _get_order_filled_at(order) -> datetime | None:
    filled_at = getattr(order, "filled_at", None)
    if filled_at is None:
        return None
    if getattr(filled_at, "tzinfo", None) is None:
        return filled_at.replace(tzinfo=timezone.utc)
    return filled_at


def _get_order_filled_qty(order) -> float:
    filled_qty = _as_float(getattr(order, "filled_qty", None))
    if filled_qty is not None and filled_qty > 0:
        return filled_qty
    qty = _as_float(getattr(order, "qty", None))
    return qty or 0.0


def _get_order_fill_price(order) -> float | None:
    return _as_float(getattr(order, "filled_avg_price", None))


def _fetch_fills(trading_client) -> list[FillRecord]:
    now_utc = datetime.now(timezone.utc)
    history_start = now_utc - timedelta(days=LOOKBACK_DAYS)
    request = GetOrdersRequest(
        status=QueryOrderStatus.ALL,
        after=history_start,
        until=now_utc,
        limit=1000,
    )
    orders = list(trading_client.get_orders(filter=request))

    fills: list[FillRecord] = []
    for order in orders:
        filled_at = _get_order_filled_at(order)
        price = _get_order_fill_price(order)
        qty = _get_order_filled_qty(order)
        symbol = _normalize_symbol(getattr(order, "symbol", ""))

        if not symbol or filled_at is None or price is None or qty <= 0:
            continue

        fills.append(
            FillRecord(
                symbol=symbol,
                side=_get_order_side(order),
                qty=qty,
                price=price,
                filled_at=filled_at,
            )
        )

    fills.sort(key=lambda item: item.filled_at)
    return fills


def _today_window() -> tuple[datetime, datetime, date]:
    now_et = datetime.now(ET)
    start_of_day = datetime.combine(now_et.date(), time.min, tzinfo=ET)
    return start_of_day, now_et, now_et.date()


def _build_current_position_map(trading_client) -> dict[str, Any]:
    positions = {}
    for position in trading_client.get_all_positions():
        symbol = _normalize_symbol(getattr(position, "symbol", ""))
        if symbol:
            positions[symbol] = position
    return positions


def _match_sell_against_inventory(
    symbol: str,
    sell_qty: float,
    sell_price: float,
    inventory: list[list[float]],
    current_position_map: dict[str, Any],
) -> tuple[float, float]:
    realized = 0.0
    remaining = sell_qty

    while remaining > 0 and inventory:
        lot_qty, lot_price = inventory[0]
        matched_qty = min(remaining, lot_qty)
        realized += matched_qty * (sell_price - lot_price)
        lot_qty -= matched_qty
        remaining -= matched_qty
        if lot_qty <= 1e-9:
            inventory.pop(0)
        else:
            inventory[0][0] = lot_qty

    if remaining > 0:
        fallback_price = _as_float(getattr(current_position_map.get(symbol), "avg_entry_price", None))
        if fallback_price is None:
            fallback_price = sell_price
        realized += remaining * (sell_price - fallback_price)
        remaining = 0.0

    return realized, remaining


def _compute_daily_summary(fills: list[FillRecord], current_position_map: dict[str, Any]) -> dict[str, Any]:
    _, now_et, today_date = _today_window()
    inventory_by_symbol: dict[str, list[list[float]]] = defaultdict(list)
    symbol_summary: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "trade_count": 0,
            "realized_pnl": 0.0,
            "buy_qty": 0.0,
            "sell_qty": 0.0,
        }
    )
    trade_log: list[dict[str, Any]] = []

    for fill in fills:
        is_today = fill.filled_at.astimezone(ET).date() == today_date
        summary = symbol_summary[fill.symbol]

        if fill.side == "BUY":
            inventory_by_symbol[fill.symbol].append([fill.qty, fill.price])
            if is_today:
                summary["trade_count"] += 1
                summary["buy_qty"] += fill.qty
            pnl_delta = 0.0
        elif fill.side == "SELL":
            pnl_delta, _ = _match_sell_against_inventory(
                fill.symbol,
                fill.qty,
                fill.price,
                inventory_by_symbol[fill.symbol],
                current_position_map,
            )
            if is_today:
                summary["trade_count"] += 1
                summary["sell_qty"] += fill.qty
                summary["realized_pnl"] += pnl_delta
        else:
            pnl_delta = 0.0

        if is_today:
            trade_log.append(
                {
                    "symbol": fill.symbol,
                    "side": fill.side,
                    "qty": fill.qty,
                    "price": fill.price,
                    "filled_at": fill.filled_at.astimezone(ET).isoformat(),
                    "realized_pnl_delta": round(pnl_delta, 4),
                }
            )

    for symbol, summary in symbol_summary.items():
        trade_count = summary["trade_count"]
        summary["average_pnl_per_trade"] = summary["realized_pnl"] / trade_count if trade_count else 0.0

    daily_realized_pnl = sum(summary["realized_pnl"] for summary in symbol_summary.values())
    trade_count = sum(summary["trade_count"] for summary in symbol_summary.values())

    return {
        "date": today_date.isoformat(),
        "timezone": "America/New_York",
        "generated_at": now_et.isoformat(),
        "daily_realized_pnl": round(daily_realized_pnl, 4),
        "trade_count": trade_count,
        "symbols": dict(sorted(symbol_summary.items())),
        "trade_log": trade_log,
        "lookback_days": LOOKBACK_DAYS,
        "note": "Realized P/L is estimated from matched fills using available order history.",
    }


def _print_summary(summary: dict[str, Any]) -> None:
    print(f"Daily P/L for {summary['date']} (America/New_York)")
    print(f"Generated at: {summary['generated_at']}")
    print(f"Total trades: {summary['trade_count']}")
    print(f"Daily realized P/L: ${summary['daily_realized_pnl']:+.2f}")

    print("\nPer-symbol summary:")
    if not summary["symbols"]:
        print("  (no filled trades today)")
        return

    for symbol, stats in summary["symbols"].items():
        print(
            f"  {symbol:<8} trades={stats['trade_count']:>3} "
            f"total_pnl=${stats['realized_pnl']:+.2f} "
            f"avg_per_trade=${stats['average_pnl_per_trade']:+.2f}"
        )


def _write_state_file(path: Path, summary: dict[str, Any]) -> None:
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    output_path = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else DEFAULT_STATE_FILE

    try:
        _, trading_client = bootstrap_trading_auth("daily_pl.py")
    except Exception as exc:
        print(f"Authentication failed: {exc}")
        return 1

    try:
        fills = _fetch_fills(trading_client)
        current_position_map = _build_current_position_map(trading_client)
        summary = _compute_daily_summary(fills, current_position_map)
    except Exception as exc:
        print(f"Error computing daily P/L: {exc}")
        return 1

    _print_summary(summary)

    try:
        _write_state_file(output_path, summary)
        print(f"\nSaved daily summary to: {output_path}")
    except Exception as exc:
        print(f"\nWarning: could not save summary to {output_path}: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
