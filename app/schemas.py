"""Typed request and response models for the resolve and account APIs."""

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.accounts import StatKey
from app.url_policy import MAX_SOURCE_URL_LENGTH, PageKind, ResourceKind

_USERNAME_PATTERN = r"\A[A-Za-z0-9](?:[A-Za-z0-9_-]{1,30}[A-Za-z0-9])?\z"
Username = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=32,
        pattern=_USERNAME_PATTERN,
    ),
]
LoginUsername = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=32),
]
# Passwords are deliberately not stripped: leading and trailing spaces are
# legitimate password characters, and silently trimming them would make a
# password be rejected on one screen and accepted on another.
NewPassword = Annotated[str, StringConstraints(min_length=8, max_length=128)]
PasswordInput = Annotated[str, StringConstraints(min_length=1, max_length=128)]
# Deliberately shallow: one local part and a dotted domain. Real deliverability
# is proven by the verification mail itself, so a stricter pattern here would
# only reject addresses that work.
_EMAIL_PATTERN = r"\A[^@\s]{1,64}@[^@\s.]+(?:\.[^@\s.]+)+\z"
Email = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=3, max_length=254, pattern=_EMAIL_PATTERN),
]


class ResolveRequest(BaseModel):
    """Validate one user-supplied GitHub download URL."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    url: str = Field(min_length=1, max_length=MAX_SOURCE_URL_LENGTH)


class ResolveResponse(BaseModel):
    """Return a canonical source and a relative same-origin proxy path."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    kind: ResourceKind | PageKind
    source_url: str
    proxy_url: str


class HealthResponse(BaseModel):
    """Expose a local readiness response without contacting GitHub."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    service: Literal["github-accelerator"] = "github-accelerator"
    status: Literal["ok"] = "ok"


class RegisterRequest(BaseModel):
    """Create a new account and start its email verification."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)

    username: Username
    password: NewPassword
    email: Email


class LoginRequest(BaseModel):
    """Authenticate an existing account."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)

    username: LoginUsername
    password: PasswordInput


class VerificationRequest(BaseModel):
    """Send a verification link, attaching an address when the account has none.

    ``email`` is optional because a fresh registrant already has one on file and
    is only asking for the message again, while an account created before this
    feature existed has no address yet and must supply one.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)

    username: LoginUsername
    password: PasswordInput
    email: Email | None = None


class QuotaView(BaseModel):
    """Current access quota standing for the signed-in account."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    limit: int
    remaining: int
    reset_after_seconds: int


class SessionResponse(BaseModel):
    """Report who is signed in and what quota they have left.

    ``email`` and ``email_verified`` describe the account behind the request:
    ``None`` for an anonymous visitor, ``False`` for an account that still has
    to confirm its address, and ``True`` once it has.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    authenticated: bool
    username: str | None
    quota: QuotaView | None
    email: str | None = None
    email_verified: bool | None = None


class StatsWindowView(BaseModel):
    """Aggregate usage inside one published time window."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    key: StatKey
    downloads: int
    pages: int
    users: int


class StatsResponse(BaseModel):
    """Report aggregate usage across every window, shortest first."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    windows: list[StatsWindowView]


class RedeemBonusOut(BaseModel):
    """Result of redeeming blog credits for extra attempts."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    ok: bool
    credits_spent: int
    attempts_granted: int
    bonus: int
    quota: QuotaView
