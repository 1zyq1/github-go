"""Pytest fixtures for backend tests."""

import os
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path

import httpx2
import pytest
import pytest_asyncio
from app.accounts import AccountStore, build_account_store
from app.config import Settings
from app.main import create_app
from fastapi import FastAPI

from tests.support import (
    UNLIMITED_USAGE,
    RecordingMailer,
    build_client,
    opened_store,
    sign_in,
)

# A ``.env`` beside the repository is a deployment file: it carries a real SMTP
# password, a ``Secure``-only cookie, and the public base URL. Nothing under
# test may inherit it, because those values silently rewrite the behaviour being
# verified -- a ``Secure`` cookie is never sent back over the plain HTTP the
# tests speak, which failed every signed-in test at once. Dropping the file
# source leaves real environment variables and explicit ``Settings(...)``
# arguments working, which is how the fixtures below configure each test.
Settings.model_config["env_file"] = None

# Accounts live in MySQL, and the deployment account may not create a schema of
# its own, so the suite shares the product database and isolates itself with a
# table prefix instead. Only prefixed tables are ever created, counted, or
# emptied -- see :meth:`app.accounts.AccountStore.purge`.
TEST_TABLE_PREFIX = "test_"
_MYSQL_KEYS = (
    "GITHUB_ACCELERATOR_MYSQL_HOST",
    "GITHUB_ACCELERATOR_MYSQL_PORT",
    "GITHUB_ACCELERATOR_MYSQL_USER",
    "GITHUB_ACCELERATOR_MYSQL_PASSWORD",
    "GITHUB_ACCELERATOR_MYSQL_DATABASE",
)
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _configure_environment() -> None:
    """Point the suite at the test tables without inheriting the deployment.

    The MySQL keys come from the local ``.env``, and only those keys are read:
    the same file also carries the deployment's SMTP password, which no test may
    pick up. A real environment variable wins, so a CI can point the suite at its
    own server, while the table prefix is always replaced rather than defaulted,
    so a test run can never clear a production table. The usage quota is raised
    because functional tests are not about the product limit, which dedicated
    tests cover by building their own settings, and resending a verification mail
    is rate limited in production, so the rest of the suite runs without the
    cooldown.
    """
    if _ENV_FILE.is_file():
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            name, separator, value = line.partition("=")
            if not separator or name.strip() not in _MYSQL_KEYS:
                continue
            if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            _ = os.environ.setdefault(name.strip(), value.strip())
    os.environ["GITHUB_ACCELERATOR_MYSQL_TABLE_PREFIX"] = TEST_TABLE_PREFIX
    os.environ["GITHUB_ACCELERATOR_USAGE_LIMIT"] = str(UNLIMITED_USAGE)
    os.environ["GITHUB_ACCELERATOR_VERIFICATION_RESEND_SECONDS"] = "0"


_configure_environment()


@pytest_asyncio.fixture(loop_scope="function")
async def account_store() -> AsyncGenerator[AccountStore]:
    """Return an empty store on the prefixed test tables, closed afterwards.

    The pool is bound to the event loop that created it, so it cannot be shared
    between tests: this one is built and closed inside a single test, and the
    tables are emptied first so counts, quotas, and statistics all start from a
    known state.
    """
    async with opened_store(build_account_store(Settings())) as store:
        yield store


@pytest.fixture
def app(account_store: AccountStore) -> FastAPI:
    """Return a fresh application instance for each test.

    The mailer is injected because no test may reach a real mail server; the
    captures live in :data:`tests.support.VERIFICATION_MAILBOX`.
    """
    return create_app(mailer=RecordingMailer(), account_store=account_store)


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
