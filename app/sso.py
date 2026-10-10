"""Cross-site single sign-on with the 1zyq1 blog.

The two services exchange a short-lived, HMAC-SHA256 signed JSON assertion built
from a shared secret (``GITHUB_ACCELERATOR_SSO_SHARED_SECRET`` == the blog's
``SSO_SHARED_SECRET``). This module only signs and verifies those tokens; the
routes in :mod:`app.main` drive the redirect flow and the account store persists
the linked account.

Two roles, one protocol:

* **IdP** (a visitor signs into the blog using their accelerator account):
  ``issue_assertion`` vouches for the signed-in user, the route then bounces the
  browser back to the blog callback.
* **RP** (a visitor signs into this service using their blog account):
  ``parse_assertion`` verifies the blog's voucher before an account is opened.

The ``state`` round-trips through the partner untouched and is also signed, so no
cookie is required to bind a callback to the browser that started it.
"""

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from typing import cast

from app.accounts import User
from app.config import Settings
from app.errors import AppError

_USERNAME_MIN_LENGTH = 3
_USERNAME_MAX_LENGTH = 32


class SsoError(AppError):
    """An SSO exchange that should not continue; ``message`` is user-facing."""


class _InvalidTokenError(Exception):
    """A signed token could not be verified; the caller maps it to ``SsoError``."""


_INVALID_ASSERTION = "登录票据无效或已被篡改。请重新点击登录"
_UNKNOWN_ISSUER = "登录票据来自未知来源。请重新点击登录"
_WRONG_AUDIENCE = "登录票据不是发给本站的。请重新点击登录"
_EXPIRED_ASSERTION = "登录票据已过期。请重新点击登录"
_MISSING_EMAIL = "登录票据缺少邮箱。请重新点击登录"
_INVALID_STATE = "登录校验失败。请重新点击登录"
_EXPIRED_STATE = "登录已超时。请重新点击登录"


@dataclass(frozen=True, slots=True)
class SsoIdentity:
    """The identity a partner vouches for inside an assertion."""

    issuer: str
    subject: str
    email: str
    username: str
    name: str
    avatar: str | None
    email_verified: bool


def _assertion_error(message: str) -> SsoError:
    return SsoError("invalid_assertion", message, 400)


def _state_error(message: str) -> SsoError:
    return SsoError("invalid_state", message, 400)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _signature(settings: Settings, raw: str) -> str:
    key = settings.sso_shared_secret.encode("utf-8")
    return hmac.new(key, raw.encode("ascii"), hashlib.sha256).hexdigest()


def _sign(settings: Settings, payload: dict[str, object]) -> str:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    raw = _b64encode(body)
    return f"{raw}.{_signature(settings, raw)}"


def _verify(settings: Settings, token: str) -> dict[str, object]:
    """Verify a signed token and return its payload, or raise ``_InvalidTokenError``."""
    if not token or "." not in token:
        raise _InvalidTokenError
    raw, _, signature = token.partition(".")
    if not hmac.compare_digest(signature, _signature(settings, raw)):
        raise _InvalidTokenError
    try:
        raw_value = cast("object", json.loads(_b64decode(raw).decode("utf-8")))
    except (ValueError, UnicodeDecodeError) as exc:
        raise _InvalidTokenError from exc
    if not isinstance(raw_value, dict):
        raise _InvalidTokenError
    return cast("dict[str, object]", raw_value)


def _text(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def _number(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def issue_assertion(settings: Settings, user: User) -> str:
    """As the IdP, sign an assertion for the currently signed-in user."""
    now = int(time.time())
    payload: dict[str, object] = {
        "iss": settings.sso_issuer,
        "aud": settings.sso_audience,
        "sub": str(user.id),
        "email": user.email or "",
        "username": user.username,
        "name": user.username,
        "avatar": None,
        "email_verified": True,
        "iat": now,
        "exp": now + settings.sso_assertion_ttl_seconds,
        "jti": secrets.token_urlsafe(8),
    }
    return _sign(settings, payload)


def parse_assertion(settings: Settings, token: str) -> SsoIdentity:
    """As the RP, verify an assertion and return the identity it vouches for."""
    try:
        payload = _verify(settings, token)
    except _InvalidTokenError as exc:
        raise _assertion_error(_INVALID_ASSERTION) from exc

    now = int(time.time())
    if payload.get("iss") != settings.sso_audience:
        raise _assertion_error(_UNKNOWN_ISSUER)
    if payload.get("aud") != settings.sso_issuer:
        raise _assertion_error(_WRONG_AUDIENCE)
    if _number(payload, "exp") <= now:
        raise _assertion_error(_EXPIRED_ASSERTION)

    email = _text(payload, "email").strip()
    if not email:
        raise _assertion_error(_MISSING_EMAIL)
    username = _text(payload, "username").strip()
    name = _text(payload, "name").strip() or username

    return SsoIdentity(
        issuer=_text(payload, "iss"),
        subject=_text(payload, "sub"),
        email=email,
        username=username,
        name=name,
        avatar=_text(payload, "avatar") or None,
        email_verified=bool(payload.get("email_verified")),
    )


def sanitize_return_path(path: str) -> str:
    """Allow only same-origin relative paths, otherwise fall back to the root."""
    value = (path or "").strip()
    if not value.startswith("/"):
        return "/"
    if value.startswith(("//", "/\\")):
        return "/"
    if "://" in value or "\n" in value or "\r" in value or "\\" in value:
        return "/"
    return value[:200]


def sign_state(settings: Settings, return_path: str) -> str:
    """Sign a one-shot state token carrying the post-login return path."""
    payload: dict[str, object] = {
        "rp": sanitize_return_path(return_path),
        "ts": int(time.time()),
        "n": secrets.token_urlsafe(8),
    }
    return _sign(settings, payload)


def verify_state(settings: Settings, token: str) -> str:
    """Verify a state token and return its sanitized return path."""
    try:
        payload = _verify(settings, token)
    except _InvalidTokenError as exc:
        raise _state_error(_INVALID_STATE) from exc
    age = int(time.time()) - _number(payload, "ts")
    if age > settings.sso_assertion_ttl_seconds:
        raise _state_error(_EXPIRED_STATE)
    return sanitize_return_path(_text(payload, "rp") or "/")


def sso_username(raw: str) -> str:
    """Derive a username that satisfies this service's account rules.

    The allowed shape is 3..32 characters of letters, digits, ``_`` or ``-``, with
    an alphanumeric first and last character. Any offending character becomes ``_``.
    """
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", (raw or "").strip()).strip("_-")[:_USERNAME_MAX_LENGTH]
    if not cleaned or not cleaned[0].isalnum():
        cleaned = "u" + cleaned.lstrip("_-")[: _USERNAME_MAX_LENGTH - 1]
    if not cleaned[-1].isalnum():
        cleaned = cleaned[: _USERNAME_MAX_LENGTH - 1] + "u"
    if len(cleaned) < _USERNAME_MIN_LENGTH:
        cleaned = f"gh_{cleaned or 'user'}"
    return cleaned[:_USERNAME_MAX_LENGTH]
