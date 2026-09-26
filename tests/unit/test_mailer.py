"""Unit tests for verification mail composition and delivery."""

import logging
import smtplib
from collections.abc import Iterator
from email.message import EmailMessage
from typing import Self, cast, override

import pytest
from app.config import Settings
from app.mailer import (
    ConsoleMailer,
    MailDeliveryError,
    SmtpMailer,
    build_mailer,
    build_message,
    verification_link,
)

pytestmark = pytest.mark.asyncio

_LINK = "https://example.test/api/auth/verify?token=abc123"
_NO_STARTTLS = "no STARTTLS today"
_NO_PLAIN_TRANSPORT = "the plain SMTP transport must not be used"


def _settings(
    *,
    host: str = "smtp.example.test",
    starttls: bool = True,
    username: str = "mailer@example.test",
    ttl: int = 86_400,
) -> Settings:
    """Build settings that describe a reachable SMTP server."""
    return Settings(
        smtp_host=host,
        smtp_port=587,
        smtp_username=username,
        smtp_password="secret",
        smtp_from="GitHub Accelerator <mailer@example.test>",
        smtp_starttls=starttls,
        verification_ttl_seconds=ttl,
    )


class _Conversation:
    """Record one SMTP session so a test can assert what the mailer did."""

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host: str = host
        self.port: int = port
        self.timeout: float = timeout
        self.started_tls: bool = False
        self.credentials: tuple[str, str] | None = None
        self.message: EmailMessage | None = None


_SESSIONS: list[_Conversation] = []


class _FakeSmtp:
    """Stand in for ``smtplib.SMTP`` and complete the session instantly."""

    def __init__(self, host: str, port: int, *, timeout: float) -> None:
        self._conversation: _Conversation = _Conversation(host, port, timeout)

    def __enter__(self) -> Self:
        _SESSIONS.append(self._conversation)
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def starttls(self) -> tuple[int, bytes]:
        self._conversation.started_tls = True
        return (220, b"ready")

    def login(self, username: str, password: str) -> tuple[int, bytes]:
        self._conversation.credentials = (username, password)
        return (235, b"ok")

    def send_message(self, message: EmailMessage) -> dict[str, tuple[int, bytes]]:
        self._conversation.message = message
        return {}


class _RefusingSmtp(_FakeSmtp):
    """Reject the handshake, the way a server with no STARTTLS would."""

    @override
    def starttls(self) -> tuple[int, bytes]:
        raise smtplib.SMTPException(_NO_STARTTLS)


class _UnexpectedSmtp:
    """Fail the test if the plain transport is reached at all."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError(_NO_PLAIN_TRANSPORT)


@pytest.fixture(autouse=True)
def _clear_sessions() -> Iterator[None]:
    _SESSIONS.clear()
    yield
    _SESSIONS.clear()


async def test_starttls_connection_sends_the_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.mailer.smtplib.SMTP", _FakeSmtp)
    mailer = SmtpMailer(_settings())

    await mailer.send_verification("person@example.test", _LINK)

    assert len(_SESSIONS) == 1
    session = _SESSIONS[0]
    assert (session.host, session.port) == ("smtp.example.test", 587)
    assert session.started_tls is True
    assert session.credentials == ("mailer@example.test", "secret")
    assert session.message is not None
    assert cast("str", session.message["To"]) == "person@example.test"
    assert cast("str", session.message["From"]) == "GitHub Accelerator <mailer@example.test>"
    payload = session.message.get_payload(decode=True)
    assert isinstance(payload, bytes)
    assert _LINK in payload.decode("utf-8")


async def test_implicit_tls_is_used_when_starttls_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.mailer.smtplib.SMTP_SSL", _FakeSmtp)
    monkeypatch.setattr("app.mailer.smtplib.SMTP", _UnexpectedSmtp)
    mailer = SmtpMailer(_settings(starttls=False))

    await mailer.send_verification("person@example.test", _LINK)

    # The wrapped transport never negotiates STARTTLS.
    assert [session.started_tls for session in _SESSIONS] == [False]
    assert _SESSIONS[0].credentials == ("mailer@example.test", "secret")


async def test_an_anonymous_relay_skips_the_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.mailer.smtplib.SMTP", _FakeSmtp)
    mailer = SmtpMailer(_settings(username=""))

    await mailer.send_verification("person@example.test", _LINK)

    assert _SESSIONS[0].credentials is None
    assert _SESSIONS[0].message is not None


async def test_a_transport_failure_is_reported_as_a_delivery_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.mailer.smtplib.SMTP", _RefusingSmtp)
    mailer = SmtpMailer(_settings())

    with pytest.raises(MailDeliveryError, match="no STARTTLS today"):
        await mailer.send_verification("person@example.test", _LINK)


async def test_the_console_mailer_logs_the_link(caplog: pytest.LogCaptureFixture) -> None:
    mailer = ConsoleMailer(_settings(ttl=7200))

    with caplog.at_level(logging.WARNING):
        await mailer.send_verification("person@example.test", _LINK)

    assert _LINK in caplog.text
    assert "person@example.test" in caplog.text
    assert "2 小时后失效" in caplog.text


async def test_build_mailer_chooses_the_configured_transport() -> None:
    assert isinstance(build_mailer(_settings()), SmtpMailer)
    assert isinstance(build_mailer(Settings(smtp_host="")), ConsoleMailer)


async def test_the_message_is_plain_text_with_a_chinese_subject() -> None:
    message = build_message(
        sender="mailer@example.test",
        recipient="person@example.test",
        link=_LINK,
        ttl_seconds=3600,
    )

    assert cast("str", message["Subject"]) == "验证你的邮箱"
    assert message.get_content_type() == "text/plain"


async def test_the_verification_link_is_absolute() -> None:
    settings = Settings(public_base_url="https://mirror.example.test/")

    assert (
        verification_link(settings, "raw-token")
        == "https://mirror.example.test/api/auth/verify?token=raw-token"
    )
