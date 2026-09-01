#!/usr/bin/env python3
"""Record account equity once per day and compare it with the prior record.

Usage:
    python strategies/daily-stat.py
    python strategies/daily-stat.py --schedule

The one-time command records immediately. Schedule mode waits and records at
12:59 PM Mountain (MST) on weekdays. Repeated runs on the same date never overwrite the
stored value; they show live profit/loss versus the previous recorded date.
Every run emails the result.
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth
from roles.email_notify import send_email


BALANCE_FILE = WORKSPACE_ROOT / "balance.txt"
MST = ZoneInfo("America/Denver")
SNAPSHOT_TIME = clock_time(12, 59)
BALANCE_LINE_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*:\s*([-+]?\d+(?:\.\d+)?)$")


@dataclass(frozen=True)
class AccountSnapshot:
    equity: Decimal
    cash: Decimal
    position_market_value: Decimal
    position_count: int
    positions: tuple[tuple[str, Decimal, Decimal], ...]


def _decimal(value, default: Decimal = Decimal("0")) -> Decimal:
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return default


def _get_account_snapshot(trading_client) -> AccountSnapshot:
    account = trading_client.get_account()
    cash = _decimal(getattr(account, "cash", None))
    positions = []
    for position in trading_client.get_all_positions():
        symbol = str(getattr(position, "symbol", "")).upper()
        quantity = _decimal(getattr(position, "qty", None))
        market_value = _decimal(getattr(position, "market_value", None))
        positions.append((symbol, quantity, market_value))

    position_market_value = sum((item[2] for item in positions), Decimal("0"))
    equity_value = getattr(account, "equity", None)
    if equity_value in (None, ""):
        equity_value = getattr(account, "portfolio_value", None)
    equity = _decimal(equity_value, cash + position_market_value)
    return AccountSnapshot(
        equity=equity,
        cash=cash,
        position_market_value=position_market_value,
        position_count=len(positions),
        positions=tuple(positions),
    )


def _load_balances(path: Path = BALANCE_FILE) -> dict[date, Decimal]:
    if not path.exists():
        return {}

    balances: dict[date, Decimal] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        match = BALANCE_LINE_PATTERN.fullmatch(line)
        if not match:
            raise RuntimeError(f"Invalid {path.name} line {line_number}: {raw_line!r}")
        balances[date.fromisoformat(match.group(1))] = Decimal(match.group(2))
    return balances


def _write_balances(balances: dict[date, Decimal], path: Path = BALANCE_FILE) -> None:
    lines = [f"{day.isoformat()} : {balances[day]:.2f}" for day in sorted(balances)]
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(path)


def _previous_balance(
    balances: dict[date, Decimal],
    today: date,
) -> tuple[date, Decimal] | None:
    prior_dates = [recorded_date for recorded_date in balances if recorded_date < today]
    if not prior_dates:
        return None
    previous_date = max(prior_dates)
    return previous_date, balances[previous_date]


def _money(value: Decimal) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _signed_money(value: Decimal) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}${abs(value):,.2f}"


def _snapshot_lines(snapshot: AccountSnapshot) -> list[str]:
    lines = [
        f"Account equity: {_money(snapshot.equity)}",
        f"Cash: {_money(snapshot.cash)}",
        (
            f"Position market value: {_money(snapshot.position_market_value)} "
            f"across {snapshot.position_count} position(s)"
        ),
    ]
    for symbol, quantity, market_value in snapshot.positions:
        lines.append(f"  {symbol}: qty={quantity:g} market={_money(market_value)}")
    return lines


def record_and_report(
    trading_client,
    now_pt: datetime | None = None,
    balance_file: Path = BALANCE_FILE,
) -> None:
    if now_pt is None:
        now_pt = datetime.now(MST)
    today = now_pt.date()
    balances = _load_balances(balance_file)
    snapshot = _get_account_snapshot(trading_client)

    lines = [f"DAILY ACCOUNT STAT - {today} {now_pt.strftime('%H:%M:%S %Z')}"]
    lines.extend(_snapshot_lines(snapshot))

    if today in balances:
        lines.append(
            f"Stored balance already exists for {today}: {_money(balances[today])}. "
            f"{balance_file.name} was not changed."
        )
    else:
        balances[today] = snapshot.equity
        _write_balances(balances, balance_file)
        lines.append(f"Recorded {today} : {snapshot.equity:.2f} in {balance_file}")

    previous = _previous_balance(balances, today)
    if previous is None:
        subject = f"Daily account stat {today}: {_money(snapshot.equity)} (no prior record)"
        lines.append("Today's P/L: N/A (no previous recorded date)")
    else:
        previous_date, previous_equity = previous
        change = snapshot.equity - previous_equity
        percent = change / previous_equity * Decimal("100") if previous_equity else Decimal("0")
        subject = (
            f"Daily account stat {today}: {_signed_money(change)} "
            f"({percent:+.2f}%) | equity {_money(snapshot.equity)}"
        )
        lines.append(f"Previous record: {previous_date} : {_money(previous_equity)}")
        lines.append(
            f"Today's P/L versus {previous_date}: {_signed_money(change)} ({percent:+.2f}%)"
        )

    print()
    for line in lines:
        print(line)

    try:
        recipient = send_email(subject, "\n".join(lines) + "\n")
        print(f"Emailed report to {recipient}")
    except Exception as exc:
        print(f"Could not email report: {exc}")


def _next_schedule_time(now_pt: datetime, balances: dict[date, Decimal]) -> datetime:
    candidate_date = now_pt.date()
    while True:
        candidate = datetime.combine(candidate_date, SNAPSHOT_TIME, tzinfo=MST)
        if candidate_date.weekday() < 5 and candidate_date not in balances:
            return now_pt if candidate <= now_pt else candidate
        candidate_date += timedelta(days=1)


def _run_schedule(trading_client) -> None:
    print("Schedule mode active. Daily snapshot time: 12:59 PM Mountain (MST), weekdays.")
    print("Press Ctrl+C to stop.")
    while True:
        balances = _load_balances()
        now_pt = datetime.now(MST)
        run_at = _next_schedule_time(now_pt, balances)
        print(f"Next snapshot: {run_at.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        while True:
            remaining = (run_at - datetime.now(MST)).total_seconds()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 60))
        record_and_report(trading_client, datetime.now(MST))


def main() -> int:
    if len(sys.argv) > 2 or (len(sys.argv) == 2 and sys.argv[1] != "--schedule"):
        print("Usage: python strategies/daily-stat.py [--schedule]")
        return 1

    try:
        _, trading_client = bootstrap_trading_auth("daily-stat.py")
    except Exception as exc:
        print(f"Authentication failed: {exc}")
        return 1

    try:
        if len(sys.argv) == 2:
            _run_schedule(trading_client)
        else:
            record_and_report(trading_client)
    except KeyboardInterrupt:
        print("\nStopped by user")
    except Exception as exc:
        print(f"Daily account stat failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
