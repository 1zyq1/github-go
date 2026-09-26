"""Pytest fixtures for backend tests."""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx2
import pytest
import pytest_asyncio
from app.main import create_app
from fastapi import FastAPI

from tests.support import UNLIMITED_USAGE, RecordingMailer, build_client, sign_in


@pytest.fixture(autouse=True)
def isolate_backend_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every test off the developer's real ``.cache`` directory.

    Tests build ``Settings`` in several places, including through the bare
    ``create_app()`` default, so the account database is redirected through the
    environment to isolate all of them at once. The usage quota is raised
    because functional tests are not about the product limit, which dedicated
    tests cover by building their own settings. Resending a verification mail is
    rate limited in production; the tests that exercise that limit build their
    own settings, so the rest of the suite runs without the cooldown.
    """
    monkeypatch.setenv("GITHUB_ACCELERATOR_ACCOUNTS_DB_PATH", str(tmp_path / "accounts.db"))
    monkeypatch.setenv("GITHUB_ACCELERATOR_USAGE_LIMIT", str(UNLIMITED_USAGE))
    monkeypatch.setenv("GITHUB_ACCELERATOR_VERIFICATION_RESEND_SECONDS", "0")


@pytest.fixture
def app() -> FastAPI:
    """Return a fresh application instance for each test.

    The mailer is injected because no test may reach a real mail server; the
    captures live in :data:`tests.support.VERIFICATION_MAILBOX`.
    """
    return create_app(mailer=RecordingMailer())


@pytest_asyncio.fixture(loop_scope="function")
async def client(app: FastAPI) -> AsyncIterator[httpx2.AsyncClient]:
    """Return a client already signed in as a throwaway account."""
    async with build_client(app) as http_client:
        yield await sign_in(http_client)


@pytest_asyncio.fixture(loop_scope="function")
async def anon_client(app: FastAPI) -> AsyncIterator[httpx2.AsyncClient]:
    """Return a client that carries no session cookie."""
    async with build_client(app) as http_client:
        yield http_client
