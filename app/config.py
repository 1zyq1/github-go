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
    accounts_db_path: Path = Path(".cache/accounts.db")
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

    @property
    def resolved_static_dir(self) -> Path:
        """Return the absolute SPA directory path."""
        return self.static_dir.resolve()
