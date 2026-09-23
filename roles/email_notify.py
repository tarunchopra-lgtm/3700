"""Shared Gmail sender for report and alert programs."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from dotenv import load_dotenv

DEFAULT_EMAIL = "tarun.chopra@gmail.com"


def load_email_config() -> tuple[str, str, str]:
    workspace_root = Path(__file__).resolve().parent.parent
    credentials_file = workspace_root / "env" / "credentials"
    if credentials_file.exists():
        load_dotenv(dotenv_path=credentials_file)
    else:
        load_dotenv(dotenv_path=workspace_root / ".env")

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


def send_email_with_attachments(subject: str, body: str, attachments: list[Path] | list[str]) -> str:
    """
    Send email with file attachments.
    
    Args:
        subject: Email subject
        body: Email body text
        attachments: List of file paths to attach (Path or str)
    
    Returns:
        Recipient email address
    """
    from_email, to_email, app_password = load_email_config()

    message = EmailMessage()
    message["From"] = from_email
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

    # Add attachments
    for attachment in attachments:
        file_path = Path(attachment)
        if not file_path.exists():
            print(f"[WARNING] Attachment not found: {file_path}")
            continue
        
        # Read file and add as attachment
        with open(file_path, 'rb') as f:
            file_data = f.read()
            file_name = file_path.name
            
            # Guess the subtype based on file extension
            maintype = 'text'
            subtype = 'plain'
            if file_name.endswith('.txt'):
                maintype = 'text'
                subtype = 'plain'
            elif file_name.endswith('.output'):
                maintype = 'text'
                subtype = 'plain'
            elif file_name.endswith('.html'):
                maintype = 'text'
                subtype = 'html'
            elif file_name.endswith('.pdf'):
                maintype = 'application'
                subtype = 'pdf'
            
            message.add_attachment(file_data, maintype=maintype, subtype=subtype, filename=file_name)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(from_email, app_password)
        smtp.send_message(message)

    return to_email
