"""Unit tests for the cross-site SSO token signing and verification."""

import base64
import json
from typing import cast

import pytest
from app.accounts import User
from app.config import Settings
from app.sso import (
    SsoError,
    issue_assertion,
    issue_spend_token,
    parse_assertion,
    sanitize_return_path,
    sign_state,
    sso_username,
    verify_state,
)

_SECRET = "shared-test-secret"


class _FakeTime:
    """A hand-cranked stand-in for the ``time`` module ``sso`` reads."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now: float = now

    def time(self) -> float:
        return self.now


def _settings(
    *,
    issuer: str = "github-accelerator",
    audience: str = "blog",
    secret: str = _SECRET,
    ttl: int = 300,
) -> Settings:
    """Build a Settings carrying only what the SSO functions read."""
    return Settings(
        mysql_host="localhost",
        mysql_user="test",
        mysql_database="test",
        sso_shared_secret=secret,
        sso_issuer=issuer,
        sso_audience=audience,
        sso_assertion_ttl_seconds=ttl,
    )


def _blog_settings() -> Settings:
    """The settings as the blog sees them: the peer that vouches for users."""
    return _settings(issuer="blog", audience="github-accelerator")


def test_assertion_round_trips_between_the_two_services() -> None:
    user = User(7, "alice", "alice@example.com", email_verified=True)

    assertion = issue_assertion(_blog_settings(), user)
    identity = parse_assertion(_settings(), assertion)

    assert identity.issuer == "blog"
    assert identity.subject == "7"
    assert identity.email == "alice@example.com"
    assert identity.username == "alice"
    assert identity.name == "alice"
    assert identity.avatar is None
    assert identity.email_verified is True


def test_a_tampered_assertion_is_rejected() -> None:
    user = User(7, "alice", "alice@example.com", email_verified=True)
    assertion = issue_assertion(_blog_settings(), user)

    raw, _, signature = assertion.partition(".")
    flipped = raw[:-1] + ("A" if raw[-1] != "A" else "B")

    with pytest.raises(SsoError):
        _ = parse_assertion(_settings(), f"{flipped}.{signature}")


def test_an_assertion_from_an_unknown_issuer_is_rejected() -> None:
    user = User(7, "alice", "alice@example.com", email_verified=True)

    # Signed by a service the accelerator does not accept.
    foreign = _settings(issuer="evil", audience="github-accelerator")
    assertion = issue_assertion(foreign, user)

    with pytest.raises(SsoError):
        _ = parse_assertion(_settings(), assertion)


def test_an_assertion_meant_for_another_audience_is_rejected() -> None:
    user = User(7, "alice", "alice@example.com", email_verified=True)

    wrong_audience = _settings(issuer="blog", audience="somebody-else")
    assertion = issue_assertion(wrong_audience, user)

    with pytest.raises(SsoError):
        _ = parse_assertion(_settings(), assertion)


def test_an_assertion_without_an_email_is_rejected() -> None:
    user = User(7, "alice", None, email_verified=True)

    assertion = issue_assertion(_blog_settings(), user)

    with pytest.raises(SsoError):
        _ = parse_assertion(_settings(), assertion)


def test_an_expired_assertion_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    user = User(7, "alice", "alice@example.com", email_verified=True)
    monkeypatch.setattr("app.sso.time", _FakeTime(1_000.0))
    assertion = issue_assertion(_blog_settings(), user)

    monkeypatch.setattr("app.sso.time", _FakeTime(1_000.0 + 301.0))

    with pytest.raises(SsoError):
        _ = parse_assertion(_settings(), assertion)


def test_state_round_trips_and_rejects_tampering() -> None:
    state = sign_state(_settings(), "/stats")

    assert verify_state(_settings(), state) == "/stats"

    raw, _, signature = state.partition(".")
    flipped = raw[:-1] + ("A" if raw[-1] != "A" else "B")

    with pytest.raises(SsoError):
        _ = verify_state(_settings(), f"{flipped}.{signature}")


def test_an_expired_state_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.sso.time", _FakeTime(1_000.0))
    state = sign_state(_settings(), "/")

    monkeypatch.setattr("app.sso.time", _FakeTime(1_000.0 + 301.0))

    with pytest.raises(SsoError):
        _ = verify_state(_settings(), state)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/", "/"),
        ("/stats", "/stats"),
        ("https://evil.com", "/"),
        ("//evil.com", "/"),
        ("/\\evil.com", "/"),
        ("/a\nb", "/"),
        ("/x\\y", "/"),
    ],
)
def test_sanitize_return_path(raw: str, expected: str) -> None:
    assert sanitize_return_path(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("alice", "alice"),
        ("ALICE_2", "ALICE_2"),
        ("with-dash", "with-dash"),
        ("有中文名", "gh_u"),
        ("a-b-c", "a-b-c"),
        ("ab", "gh_ab"),
        ("", "gh_u"),
    ],
)
def test_sso_username(raw: str, expected: str) -> None:
    assert sso_username(raw) == expected


def test_issue_spend_token_carries_the_deduction_claims() -> None:
    token = issue_spend_token(_settings(), "alice@example.com", 10)

    raw, _, signature = token.partition(".")
    assert signature
    payload = cast(
        "dict[str, object]",
        json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))),
    )
    assert payload["iss"] == "github-accelerator"
    assert payload["aud"] == "blog"
    assert payload["action"] == "spend"
    assert payload["email"] == "alice@example.com"
    assert payload["credits"] == 10
