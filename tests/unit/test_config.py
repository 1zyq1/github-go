import os
from pathlib import Path

import pytest
from app.config import Settings
from app.errors import AppError, error_envelope
from pydantic import ValidationError


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # "Default" means nothing is configured, so this test must not observe the
    # developer's own .env (the file name resolves against the working
    # directory, so moving away from the repository hides it) nor any prefixed
    # variable already exported in the shell. Asserting the code defaults while
    # a local .env is present would silently pin whatever that file happens to
    # say instead.
    monkeypatch.chdir(tmp_path)
    for name in tuple(os.environ):
        if name.startswith("GITHUB_ACCELERATOR_"):
            monkeypatch.delenv(name)

    settings = Settings()

    assert settings.static_dir == Path("frontend/dist")
    assert settings.trusted_hosts == ()
    assert settings.user_agent == "github-accelerator/0.1"
    assert settings.connect_timeout_seconds == 10
    assert settings.read_timeout_seconds == 300
    assert settings.pool_timeout_seconds == 10
    assert settings.max_concurrent_proxies == 100
    assert settings.max_connections == 100
    assert settings.max_keepalive_connections == 20


def test_settings_reads_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_ACCELERATOR_USER_AGENT", "accelerator/test")
    monkeypatch.setenv("GITHUB_ACCELERATOR_READ_TIMEOUT_SECONDS", "90")

    settings = Settings()

    assert settings.user_agent == "accelerator/test"
    assert settings.read_timeout_seconds == 90


def test_settings_parses_comma_separated_trusted_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_ACCELERATOR_TRUSTED_HOSTS", "example.com, *.internal")

    settings = Settings()

    assert settings.trusted_hosts == ("example.com", "*.internal")


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("connect_timeout_seconds", 0),
        ("read_timeout_seconds", 0),
        ("pool_timeout_seconds", 0),
        ("max_concurrent_proxies", 0),
        ("max_connections", 0),
        ("max_keepalive_connections", 0),
        ("connect_timeout_seconds", 121),
        ("read_timeout_seconds", 601),
        ("pool_timeout_seconds", 121),
        ("max_concurrent_proxies", 1001),
        ("max_connections", 1001),
        ("max_keepalive_connections", 1001),
    ],
)
def test_settings_rejects_out_of_range_values(field_name: str, value: int) -> None:
    with pytest.raises(ValidationError):
        _ = Settings.model_validate({field_name: value})


def test_settings_rejects_keepalive_above_connection_capacity() -> None:
    with pytest.raises(ValidationError):
        _ = Settings.model_validate(
            {
                "max_connections": 20,
                "max_keepalive_connections": 21,
            }
        )


def test_settings_resolves_static_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)

    settings = Settings(static_dir=Path("frontend/dist"))

    assert settings.resolved_static_dir == (tmp_path / "frontend" / "dist").resolve()


def test_app_error_exposes_only_safe_envelope_and_headers() -> None:
    error = AppError(
        code="request_too_large",
        message="请求内容过大。",
        status_code=413,
        headers={"Connection": "close"},
    )

    assert error_envelope(error) == {
        "error": {
            "code": "request_too_large",
            "message": "请求内容过大。",
        }
    }
    assert error.headers == {"Connection": "close"}
    assert str(error) == "请求内容过大。"
