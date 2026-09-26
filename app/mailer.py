"""Delivery of the account verification mail.

Two transports ship. :class:`SmtpMailer` talks to a real mail server with the
standard library, so no new dependency is introduced. :class:`ConsoleMailer` is
the fallback used when no server is configured: it writes the verification link
to the log, which keeps the registration flow usable on a developer machine
without pretending that a message was delivered.
"""

import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

import anyio

from app.config import Settings

logger = logging.getLogger(__name__)

_SMTP_TIMEOUT_SECONDS = 20.0


class MailDeliveryError(Exception):
    """Signal that the transport could not hand the message to a server."""


class Mailer(Protocol):
    """Deliver one verification message."""

    async def send_verification(self, recipient: str, link: str) -> None:
        """Send the verification link, raising on any delivery failure."""
        ...


def _body(link: str, ttl_seconds: int) -> str:
    hours = max(1, ttl_seconds // 3600)
    return (
        "请打开下面的链接完成邮箱验证。\n\n"
        f"{link}\n\n"
        f"链接会在 {hours} 小时后失效。非本人操作请忽略本邮件。"
    )


def build_message(*, sender: str, recipient: str, link: str, ttl_seconds: int) -> EmailMessage:
    """Assemble the verification message as plain text."""
    message = EmailMessage()
    message["Subject"] = "验证你的邮箱"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(_body(link, ttl_seconds))
    return message


class SmtpMailer:
    """Deliver verification mail through a configured SMTP server."""

    def __init__(self, settings: Settings) -> None:
        """Read the server, the credentials, and the sender from the settings."""
        self._host: str = settings.smtp_host
        self._port: int = settings.smtp_port
        self._username: str = settings.smtp_username
        self._password: str = settings.smtp_password
        self._sender: str = settings.smtp_from or settings.smtp_username
        self._starttls: bool = settings.smtp_starttls
        self._ttl: int = settings.verification_ttl_seconds

    async def send_verification(self, recipient: str, link: str) -> None:
        """Send the verification link, off the event loop."""
        await anyio.to_thread.run_sync(self._send, recipient, link)

    def _send(self, recipient: str, link: str) -> None:
        message = build_message(
            sender=self._sender,
            recipient=recipient,
            link=link,
            ttl_seconds=self._ttl,
        )
        try:
            if self._starttls:
                with smtplib.SMTP(self._host, self._port, timeout=_SMTP_TIMEOUT_SECONDS) as server:
                    _ = server.starttls()
                    if self._username:
                        _ = server.login(self._username, self._password)
                    _ = server.send_message(message)
            else:
                with smtplib.SMTP_SSL(
                    self._host,
                    self._port,
                    timeout=_SMTP_TIMEOUT_SECONDS,
                ) as server:
                    if self._username:
                        _ = server.login(self._username, self._password)
                    _ = server.send_message(message)
        except (OSError, smtplib.SMTPException) as error:
            raise MailDeliveryError(str(error)) from error


class ConsoleMailer:
    """Log the verification link when no SMTP server is configured."""

    def __init__(self, settings: Settings) -> None:
        """Keep the expiry so the logged message carries the same wording."""
        self._ttl: int = settings.verification_ttl_seconds

    async def send_verification(self, recipient: str, link: str) -> None:
        """Write the message to the log instead of sending it."""
        logger.warning(
            "SMTP is not configured, so this verification mail was not sent.\nTo: %s\n%s",
            recipient,
            _body(link, self._ttl),
        )


def build_mailer(settings: Settings) -> Mailer:
    """Choose the transport the configured settings describe."""
    if not settings.smtp_host:
        return ConsoleMailer(settings)
    return SmtpMailer(settings)


def verification_link(settings: Settings, raw_token: str) -> str:
    """Build the absolute link the recipient must open to verify."""
    return f"{settings.public_base_url.rstrip('/')}/api/auth/verify?token={raw_token}"


__all__ = [
    "ConsoleMailer",
    "MailDeliveryError",
    "Mailer",
    "SmtpMailer",
    "build_mailer",
    "build_message",
    "verification_link",
]
