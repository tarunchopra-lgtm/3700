"""Shared Gmail sender for report and alert programs."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

DEFAULT_EMAIL = "tarun.chopra@gmail.com"


def load_email_config() -> tuple[str, str, str]:
    from_email = (os.getenv("EMAIL_FROM") or DEFAULT_EMAIL).strip() or DEFAULT_EMAIL
    to_email = (os.getenv("ALERT_TO_EMAIL") or DEFAULT_EMAIL).strip() or DEFAULT_EMAIL
    # Gmail app passwords are often stored with spaces for readability.
    app_password = (os.getenv("GMAIL_APP_PASSWORD") or "").replace(" ", "").strip()

    if not app_password:
        raise RuntimeError("Missing GMAIL_APP_PASSWORD in env/credentials")

    return from_email, to_email, app_password


def send_email(subject: str, body: str) -> str:
    from_email, to_email, app_password = load_email_config()

    message = EmailMessage()
    message["From"] = from_email
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(from_email, app_password)
        smtp.send_message(message)

    return to_email
