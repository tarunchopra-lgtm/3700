#!/usr/bin/env python3
"""Watch tickers from alert_data.txt and email when price reaches a target level.

Reads <TICKER> <TARGET_PRICE> pairs from alert_data.txt, polls the latest trade
price every minute, and emails an alert when the price is at or within 0.5% of
the target.

Usage:
    python indicator/stock_alert.py
"""

from __future__ import annotations

import os
import smtplib
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from roles.credentials import bootstrap_trading_auth

ALERT_FILE = WORKSPACE_ROOT / "alert_data.txt"
POLL_SECONDS = 60
TRIGGER_TOLERANCE = 0.005  # 0.5%
REARM_TOLERANCE = 0.01  # price must leave this wider band before re-alerting
DEFAULT_TO_EMAIL = "tarun.chopra@gmail.com"


@dataclass(frozen=True)
class AlertRule:
    symbol: str
    target: float


def _load_alert_rules(path: Path) -> list[AlertRule]:
    if not path.exists():
        raise RuntimeError(f"Alert file not found: {path}")

    rules: list[AlertRule] = []
    seen: set[str] = set()

    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue

        parts = line.split()
        if len(parts) != 2:
            print(f"[WARN] {path.name}:{line_no} expected '<TICKER> <PRICE>', got: {raw_line.strip()!r}")
            continue

        symbol = parts[0].upper()
        try:
            target = float(parts[1])
        except ValueError:
            print(f"[WARN] {path.name}:{line_no} target price is not numeric: {parts[1]!r}")
            continue

        if target <= 0:
            print(f"[WARN] {path.name}:{line_no} target price must be positive: {target}")
            continue

        if symbol in seen:
            print(f"[WARN] {path.name}:{line_no} duplicate ticker {symbol}; keeping the first entry")
            continue

        seen.add(symbol)
        rules.append(AlertRule(symbol=symbol, target=target))

    if not rules:
        raise RuntimeError(f"No valid alert rules found in {path}")

    return rules


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


def _get_prices(data_client: StockHistoricalDataClient, symbols: list[str]) -> dict[str, float]:
    trades = data_client.get_stock_latest_trade(
        StockLatestTradeRequest(symbol_or_symbols=symbols, feed=DataFeed.IEX)
    )

    prices: dict[str, float] = {}
    for symbol in symbols:
        trade = trades.get(symbol)
        price = getattr(trade, "price", None)
        if price is not None:
            prices[symbol] = float(price)
    return prices


def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def main() -> int:
    try:
        credentials, _ = bootstrap_trading_auth("stock_alert.py")
        from_email, to_email, app_password = _load_email_config()
        rules = _load_alert_rules(ALERT_FILE)
    except Exception as exc:
        print(f"Startup failed: {exc}")
        return 1

    data_client = StockHistoricalDataClient(credentials.api_key, credentials.secret_key)
    symbols = [rule.symbol for rule in rules]
    triggered: set[str] = set()

    print(f"Watching {len(rules)} ticker(s) from {ALERT_FILE.name}; alerts go to {to_email}")
    for rule in rules:
        band = rule.target * TRIGGER_TOLERANCE
        print(f"  {rule.symbol:<8} target ${rule.target:,.2f} (alert band ${rule.target - band:,.2f} - ${rule.target + band:,.2f})")
    print(f"Polling every {POLL_SECONDS}s. Press Ctrl+C to exit.\n", flush=True)

    try:
        while True:
            try:
                prices = _get_prices(data_client, symbols)
            except Exception as exc:
                print(f"[{_timestamp()}] Price lookup failed: {exc}", flush=True)
                prices = {}

            for rule in rules:
                price = prices.get(rule.symbol)
                if price is None:
                    print(f"[{_timestamp()}] {rule.symbol}: no quote available", flush=True)
                    continue

                distance = abs(price - rule.target) / rule.target

                if distance > REARM_TOLERANCE:
                    triggered.discard(rule.symbol)

                if distance > TRIGGER_TOLERANCE:
                    print(
                        f"[{_timestamp()}] {rule.symbol}: ${price:,.2f} "
                        f"(target ${rule.target:,.2f}, {distance * 100:.2f}% away)",
                        flush=True,
                    )
                    continue

                if rule.symbol in triggered:
                    print(f"[{_timestamp()}] {rule.symbol}: ${price:,.2f} still in band, already alerted", flush=True)
                    continue

                subject = f"{rule.symbol} reached ${rule.target:,.2f} - current price ${price:,.2f}"
                body = (
                    f"Ticker: {rule.symbol}\n"
                    f"Target price: ${rule.target:,.2f}\n"
                    f"Current price: ${price:,.2f}\n"
                    f"Distance from target: {distance * 100:.2f}%\n"
                    f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                )

                try:
                    _send_email(from_email, to_email, app_password, subject, body)
                    triggered.add(rule.symbol)
                    print(f"[{_timestamp()}] ALERT SENT: {subject}", flush=True)
                except Exception as exc:
                    print(f"[{_timestamp()}] Failed to send alert for {rule.symbol}: {exc}", flush=True)

            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("Stopped by user")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
