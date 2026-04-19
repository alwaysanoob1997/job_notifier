from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import (
    smtp_force_ssl,
    smtp_from_address,
    smtp_host,
    smtp_password,
    smtp_port,
    smtp_user,
    smtp_use_starttls,
)

logger = logging.getLogger(__name__)


def send_plaintext_email(to: str, subject: str, body: str) -> None:
    """Send one plaintext message. No-op when LINKEDIN_SMTP_HOST is unset; logs on failure."""
    host = smtp_host()
    if not host:
        logger.info(
            "email skipped: LINKEDIN_SMTP_HOST not set (to=%s subject=%r)",
            to,
            subject[:120],
        )
        return
    from_addr = smtp_from_address()
    if not from_addr:
        logger.warning("email skipped: set LINKEDIN_SMTP_FROM or LINKEDIN_SMTP_USER")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to
    msg.set_content(body)

    port = smtp_port()
    user = smtp_user()
    password = smtp_password()

    try:
        if smtp_force_ssl():
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=context, timeout=60) as smtp:
                if user and password:
                    smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=60) as smtp:
                smtp.ehlo()
                if smtp_use_starttls():
                    context = ssl.create_default_context()
                    smtp.starttls(context=context)
                    smtp.ehlo()
                if user and password:
                    smtp.login(user, password)
                smtp.send_message(msg)
    except OSError as e:
        logger.warning("SMTP send failed (network): %s", e, exc_info=True)
    except smtplib.SMTPException as e:
        logger.warning("SMTP send failed: %s", e, exc_info=True)
