#!/usr/bin/env python3
from __future__ import annotations

import os
import smtplib
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage

from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from pathlib import Path
import sys

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth


POLL_SECONDS = 60
DEFAULT_TO_EMAIL = "tarun.chopra@gmail.com"


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    qty: float
    avg_entry_price: float
    market_value: float


@dataclass(frozen=True)
class OrderSnapshot:
    order_id: str
    symbol: str
    side: str
    qty: float
    status: str
    order_type: str
    limit_price: float | None
    submitted_at: str


def _as_float(value, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_symbol(value: str) -> str:
    return str(value or "").replace("/", "").replace("-", "").upper()


def _snapshot_positions(trading_client) -> dict[str, PositionSnapshot]:
    out: dict[str, PositionSnapshot] = {}
    for pos in trading_client.get_all_positions():
        symbol = _normalize_symbol(getattr(pos, "symbol", ""))
        if not symbol:
            continue
        out[symbol] = PositionSnapshot(
            symbol=symbol,
            qty=abs(_as_float(getattr(pos, "qty", None))),
            avg_entry_price=_as_float(getattr(pos, "avg_entry_price", None)),
            market_value=_as_float(getattr(pos, "market_value", None)),
        )
    return out


def _order_side(order) -> str:
    side = getattr(order, "side", "")
    return (side.value if hasattr(side, "value") else str(side)).upper()


def _order_status(order) -> str:
    status = getattr(order, "status", "")
    return (status.value if hasattr(status, "value") else str(status)).upper()


def _order_type(order) -> str:
    order_type = getattr(order, "type", "")
    return (order_type.value if hasattr(order_type, "value") else str(order_type)).upper()


def _snapshot_orders(trading_client) -> dict[str, OrderSnapshot]:
    # Capture both open and recently closed orders to detect new submissions and status changes.
    open_orders = list(
        trading_client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=200)
        )
    )
    closed_orders = list(
        trading_client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=200)
        )
    )

    all_orders_by_id = {}
    for order in open_orders + closed_orders:
        order_id = str(getattr(order, "id", ""))
        if not order_id:
            continue

        submitted_at = getattr(order, "submitted_at", None)
        submitted_text = ""
        if submitted_at is not None:
            submitted_text = submitted_at.isoformat() if hasattr(submitted_at, "isoformat") else str(submitted_at)

        all_orders_by_id[order_id] = OrderSnapshot(
            order_id=order_id,
            symbol=_normalize_symbol(getattr(order, "symbol", "")),
            side=_order_side(order),
            qty=_as_float(getattr(order, "qty", None)),
            status=_order_status(order),
            order_type=_order_type(order),
            limit_price=(None if getattr(order, "limit_price", None) in (None, "") else _as_float(getattr(order, "limit_price", None))),
            submitted_at=submitted_text,
        )
    return all_orders_by_id


def _format_money(value: float) -> str:
    return f"${value:.2f}"


def _build_change_lines(
    prev_positions: dict[str, PositionSnapshot],
    curr_positions: dict[str, PositionSnapshot],
    prev_orders: dict[str, OrderSnapshot],
    curr_orders: dict[str, OrderSnapshot],
) -> list[str]:
    lines: list[str] = []

    prev_pos_symbols = set(prev_positions)
    curr_pos_symbols = set(curr_positions)

    new_positions = sorted(curr_pos_symbols - prev_pos_symbols)
    closed_positions = sorted(prev_pos_symbols - curr_pos_symbols)
    common_positions = sorted(prev_pos_symbols & curr_pos_symbols)

    for symbol in new_positions:
        p = curr_positions[symbol]
        lines.append(
            f"NEW POSITION: {symbol} qty={p.qty:g} avg={_format_money(p.avg_entry_price)} mv={_format_money(p.market_value)}"
        )

    for symbol in closed_positions:
        p = prev_positions[symbol]
        lines.append(f"POSITION CLOSED: {symbol} previous_qty={p.qty:g}")

    for symbol in common_positions:
        prev = prev_positions[symbol]
        curr = curr_positions[symbol]
        if abs(prev.qty - curr.qty) > 1e-9:
            if curr.qty < prev.qty:
                lines.append(f"POSITION REDUCED: {symbol} qty {prev.qty:g} -> {curr.qty:g}")
            else:
                lines.append(f"POSITION INCREASED: {symbol} qty {prev.qty:g} -> {curr.qty:g}")

    prev_order_ids = set(prev_orders)
    curr_order_ids = set(curr_orders)

    new_order_ids = sorted(curr_order_ids - prev_order_ids)
    common_order_ids = sorted(prev_order_ids & curr_order_ids)

    for order_id in new_order_ids:
        o = curr_orders[order_id]
        price_text = "MKT" if o.limit_price is None else _format_money(o.limit_price)
        lines.append(
            f"NEW ORDER: {o.symbol} {o.side} qty={o.qty:g} type={o.order_type} price={price_text} status={o.status} id={order_id}"
        )

    for order_id in common_order_ids:
        prev = prev_orders[order_id]
        curr = curr_orders[order_id]
        if prev.status != curr.status:
            lines.append(
                f"ORDER STATUS CHANGE: {curr.symbol} {curr.side} id={order_id} {prev.status} -> {curr.status}"
            )

    return lines


def _load_email_config() -> tuple[str, str, str]:
    from_email = (os.getenv("EMAIL_FROM") or DEFAULT_TO_EMAIL).strip()
    to_email = (os.getenv("ALERT_TO_EMAIL") or DEFAULT_TO_EMAIL).strip() or DEFAULT_TO_EMAIL
    # Gmail app passwords are often stored with spaces for readability.
    app_password = (os.getenv("GMAIL_APP_PASSWORD") or "").replace(" ", "").strip()

    if not app_password:
        raise RuntimeError("Missing GMAIL_APP_PASSWORD in env/credentials")

    return from_email, to_email, app_password


def _send_email(from_email: str, to_email: str, app_password: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(from_email, app_password)
        smtp.send_message(msg)


def main() -> int:
    if len(sys.argv) > 1:
        print("Usage: python email_status.py")
        return 1

    try:
        credentials, trading_client = bootstrap_trading_auth("email_status.py")
        from_email, to_email, app_password = _load_email_config()
    except Exception as exc:
        print(f"Startup failed: {exc}")
        return 1

    print(f"Monitoring account changes every {POLL_SECONDS} seconds...")
    print(f"Email alerts will be sent to: {to_email}")

    try:
        prev_positions = _snapshot_positions(trading_client)
        prev_orders = _snapshot_orders(trading_client)
    except Exception as exc:
        print(f"Initial snapshot failed: {exc}")
        return 1

    print("Initial baseline captured. Waiting for changes...")

    try:
        while True:
            time.sleep(POLL_SECONDS)
            try:
                curr_positions = _snapshot_positions(trading_client)
                curr_orders = _snapshot_orders(trading_client)
                change_lines = _build_change_lines(prev_positions, curr_positions, prev_orders, curr_orders)
            except Exception as exc:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Poll failed: {exc}")
                continue

            if change_lines:
                now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
                subject = "Alpaca Status Change Alert"
                body = "\n".join(
                    [
                        f"Detected account changes at {now}.",
                        "",
                        *change_lines,
                    ]
                )
                try:
                    _send_email(from_email, to_email, app_password, subject, body)
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Alert email sent ({len(change_lines)} change(s)).")
                except Exception as exc:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Failed to send email: {exc}")

            prev_positions = curr_positions
            prev_orders = curr_orders
    except KeyboardInterrupt:
        print("Stopped by user.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
