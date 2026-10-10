"""Validated runtime configuration for the accelerator."""

from pathlib import Path
from typing import Annotated, ClassVar, Self

from pydantic import BeforeValidator, Field, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _parse_trusted_hosts(value: object) -> object:
    if not isinstance(value, str):
        return value
    return tuple(host.strip() for host in value.split(",") if host.strip())


TrustedHosts = Annotated[
    tuple[str, ...],
    NoDecode,
    BeforeValidator(_parse_trusted_hosts),
]


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="GITHUB_ACCELERATOR_",
        env_file=".env",
        extra="ignore",
        frozen=True,
    )

    static_dir: Path = Path("frontend/dist")
    trusted_hosts: TrustedHosts = ()
    user_agent: str = "github-accelerator/0.1"
    connect_timeout_seconds: Annotated[int, Field(ge=1, le=120)] = 10
    read_timeout_seconds: Annotated[int, Field(ge=1, le=600)] = 300
    pool_timeout_seconds: Annotated[int, Field(ge=1, le=120)] = 10
    max_concurrent_proxies: Annotated[int, Field(ge=1, le=1000)] = 100
    max_connections: Annotated[int, Field(ge=1, le=1000)] = 100
    max_keepalive_connections: Annotated[int, Field(ge=1, le=1000)] = 20
    archive_cache_dir: Path = Path(".cache/archives")
    archive_cache_max_bytes: Annotated[int, Field(ge=0)] = 20 * 1024 * 1024 * 1024
    # Accounts, sessions, quota, and statistics all live in MySQL. The three
    # empty defaults are not a usable configuration: `validate_mysql_target`
    # refuses every one of them, naming the environment variable that is missing.
    # They are spelled out rather than left required so that `Settings()` keeps
    # working as the ordinary environment-driven constructor.
    mysql_host: str = ""
    mysql_port: Annotated[int, Field(ge=1, le=65_535)] = 3306
    mysql_user: str = ""
    mysql_password: str = ""
    mysql_database: str = ""
    # MySQL grants are per database, and a deployment may not be allowed to
    # create a second one, so the test suite points at the same schema under a
    # prefix instead. It is restricted to what can safely be interpolated into
    # an identifier: every statement in the store is built from it.
    mysql_table_prefix: Annotated[str, Field(pattern=r"\A[a-z0-9_]{0,16}\z")] = ""
    mysql_pool_min_size: Annotated[int, Field(ge=1, le=100)] = 1
    mysql_pool_max_size: Annotated[int, Field(ge=1, le=100)] = 10
    session_cookie_secure: bool = False
    session_ttl_seconds: Annotated[int, Field(ge=300, le=31_536_000)] = 30 * 24 * 60 * 60
    usage_limit: Annotated[int, Field(ge=1, le=1_000_000)] = 10
    usage_window_seconds: Annotated[int, Field(ge=60, le=86_400)] = 3600
    # The absolute origin the service is reachable at, used to build the
    # verification link that is mailed out. It must match the address the user
    # actually browses, because the link has to land back on this application.
    public_base_url: str = "http://127.0.0.1:8000"
    verification_ttl_seconds: Annotated[int, Field(ge=300, le=604_800)] = 24 * 60 * 60
    verification_resend_seconds: Annotated[int, Field(ge=0, le=3600)] = 60
    smtp_host: str = ""
    smtp_port: Annotated[int, Field(ge=1, le=65_535)] = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    # Port 587 negotiates STARTTLS; port 465 is implicitly encrypted from the
    # first byte, so it needs the other branch of the mailer instead of a flag.
    smtp_starttls: bool = True

    # Cross-site single sign-on with the 1zyq1 blog. The assertion is a short-lived
    # HMAC-SHA256 signed JSON token; both services must be configured with the same
    # SSO_SHARED_SECRET. Leaving it empty disables SSO entirely.
    sso_shared_secret: str = ""
    # This service's identity inside a signed assertion.
    sso_issuer: str = "github-accelerator"
    # The only peer whose assertions this service accepts.
    sso_audience: str = "blog"
    # The blog's SSO authorize endpoint (this service redirects here as the RP).
    sso_peer_authorize_url: str = "https://api.blog.1zyq1.com/api/sso/authorize"
    # The blog's SSO callback, the only redirect_uri this authorize accepts (IdP).
    sso_peer_callback_url: str = "https://api.blog.1zyq1.com/api/sso/callback"
    # Optional explicit value for this service's own callback URL; derived from the
    # request when left empty.
    sso_self_callback_url: str = ""
    sso_assertion_ttl_seconds: Annotated[int, Field(ge=30, le=3600)] = 300

    @property
    def sso_enabled(self) -> bool:
        """Whether cross-site SSO is configured."""
        return bool(self.sso_shared_secret)

    @model_validator(mode="after")
    def validate_connection_pool(self) -> Self:
        """Keep the keepalive pool within the total connection capacity."""
        if self.max_keepalive_connections > self.max_connections:
            message = "max_keepalive_connections must not exceed max_connections"
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def validate_mail_sender(self) -> Self:
        """Require a sender address whenever an SMTP server is configured."""
        if self.smtp_host and not (self.smtp_from or self.smtp_username):
            message = "smtp_from or smtp_username must be set when smtp_host is configured"
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def validate_mysql_target(self) -> Self:
        """Refuse to start without a database to keep accounts in."""
        configured = (
            ("GITHUB_ACCELERATOR_MYSQL_HOST", self.mysql_host),
            ("GITHUB_ACCELERATOR_MYSQL_USER", self.mysql_user),
            ("GITHUB_ACCELERATOR_MYSQL_DATABASE", self.mysql_database),
        )
        missing = [name for name, value in configured if not value]
        if missing:
            message = f"the accounts database is not configured; set {', '.join(missing)}"
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def validate_mysql_pool(self) -> Self:
        """Keep the accounts pool floor inside its ceiling."""
        if self.mysql_pool_min_size > self.mysql_pool_max_size:
            message = "mysql_pool_min_size must not exceed mysql_pool_max_size"
            raise ValueError(message)
        return self

    @property
    def resolved_static_dir(self) -> Path:
        """Return the absolute SPA directory path."""
        return self.static_dir.resolve()
