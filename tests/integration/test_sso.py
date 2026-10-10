"""Integration tests for the cross-site SSO endpoints shared with the blog."""

import time
from collections.abc import AsyncGenerator

import httpx2
import pytest
import pytest_asyncio
from app.accounts import AccountStore, User
from app.auth import SESSION_COOKIE_NAME
from app.config import Settings
from app.errors import ErrorEnvelope
from app.main import create_app
from app.sso import issue_assertion, sign_payload, sign_state
from fastapi import FastAPI
from pydantic import TypeAdapter

from tests.support import RecordingMailer, build_client, sign_in

pytestmark = pytest.mark.asyncio

_SECRET = "shared-integration-secret"


def _settings() -> Settings:
    """Build the accelerator's Settings carrying the SSO fields."""
    return Settings(
        mysql_host="localhost",
        mysql_user="test",
        mysql_database="test",
        sso_shared_secret=_SECRET,
        sso_issuer="github-accelerator",
        sso_audience="blog",
        sso_peer_authorize_url="https://api.blog.1zyq1.com/api/sso/authorize",
        sso_peer_callback_url="https://api.blog.1zyq1.com/api/sso/callback",
    )


def _blog_settings() -> Settings:
    """The settings as the blog sees them: the peer that vouches for users."""
    return Settings(
        mysql_host="localhost",
        mysql_user="test",
        mysql_database="test",
        sso_shared_secret=_SECRET,
        sso_issuer="blog",
        sso_audience="github-accelerator",
        sso_peer_authorize_url="https://api.blog.1zyq1.com/api/sso/authorize",
        sso_peer_callback_url="https://api.blog.1zyq1.com/api/sso/callback",
    )


@pytest_asyncio.fixture(loop_scope="function")
async def sso_app(account_store: AccountStore) -> AsyncGenerator[FastAPI]:
    """Return an application instance with SSO enabled on the shared test store."""
    app = create_app(
        settings=_settings(),
        mailer=RecordingMailer(),
        account_store=account_store,
    )
    yield app


async def test_providers_reports_sss_disabled_by_default(app: FastAPI) -> None:
    async with build_client(app) as client:
        response = await client.get("/api/sso/providers")

    assert response.status_code == 200
    assert response.json()["blog"] is False
    assert response.json()["redeem_enabled"] is False


async def test_providers_reports_sss_enabled_when_configured(sso_app: FastAPI) -> None:
    async with build_client(sso_app) as client:
        response = await client.get("/api/sso/providers")

    assert response.status_code == 200
    assert response.json()["blog"] is True
    assert response.json()["redeem_enabled"] is True


async def test_start_redirects_to_the_blog_authorize(sso_app: FastAPI) -> None:
    async with build_client(sso_app) as client:
        response = await client.get("/api/sso/start", follow_redirects=False)

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://api.blog.1zyq1.com/api/sso/authorize?")
    assert "redirect_uri=" in location
    assert "state=" in location


async def test_authorize_signs_an_assertion_for_a_signed_in_user(
    sso_app: FastAPI,
) -> None:
    async with build_client(sso_app) as client:
        _ = await sign_in(client, username="accelerator-member")
        response = await client.get(
            "/api/sso/authorize",
            params={
                "redirect_uri": "https://api.blog.1zyq1.com/api/sso/callback",
                "state": "opaque",
            },
            follow_redirects=False,
        )

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://api.blog.1zyq1.com/api/sso/callback?")
    assert "assertion=" in location
    assert "state=opaque" in location


async def test_authorize_rejects_an_unknown_redirect_uri(sso_app: FastAPI) -> None:
    async with build_client(sso_app) as client:
        _ = await sign_in(client, username="redirect-member")
        response = await client.get(
            "/api/sso/authorize",
            params={"redirect_uri": "https://evil.example.com/callback", "state": "opaque"},
            follow_redirects=False,
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_redirect"


async def test_authorize_sends_an_anonymous_visitor_to_login(sso_app: FastAPI) -> None:
    async with build_client(sso_app) as client:
        response = await client.get(
            "/api/sso/authorize",
            params={
                "redirect_uri": "https://api.blog.1zyq1.com/api/sso/callback",
                "state": "opaque",
            },
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"].startswith("/?sso_return=")


async def test_callback_creates_an_account_and_signs_in(
    sso_app: FastAPI, account_store: AccountStore
) -> None:
    assertion = issue_assertion(
        _blog_settings(), User(1, "alice", "alice@example.com", email_verified=True)
    )
    state = sign_state(_settings(), "/")

    async with build_client(sso_app) as client:
        response = await client.get(
            "/api/sso/callback",
            params={"assertion": assertion, "state": state},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/?sso=ok"
    assert SESSION_COOKIE_NAME in client.cookies
    created = await account_store.find_by_email("alice@example.com")
    assert created is not None
    assert created.email_verified is True


async def test_callback_links_an_existing_unverified_email(
    sso_app: FastAPI, account_store: AccountStore
) -> None:
    existing = await account_store.register("alice", "password-1234", "alice@example.com")
    assertion = issue_assertion(
        _blog_settings(), User(9, "alice", "alice@example.com", email_verified=True)
    )
    state = sign_state(_settings(), "/")

    async with build_client(sso_app) as client:
        response = await client.get(
            "/api/sso/callback",
            params={"assertion": assertion, "state": state},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/?sso=ok"
    linked = await account_store.find_by_email("alice@example.com")
    assert linked is not None
    assert linked.id == existing.id
    assert linked.email_verified is True


async def test_callback_rejects_a_tampered_assertion(sso_app: FastAPI) -> None:
    assertion = issue_assertion(
        _blog_settings(), User(1, "alice", "alice@example.com", email_verified=True)
    )
    raw, _, signature = assertion.partition(".")
    flipped = raw[:-1] + ("A" if raw[-1] != "A" else "B")
    state = sign_state(_settings(), "/")

    async with build_client(sso_app) as client:
        response = await client.get(
            "/api/sso/callback",
            params={"assertion": f"{flipped}.{signature}", "state": state},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/?sso=error")
    assert SESSION_COOKIE_NAME not in client.cookies


async def test_callback_rejects_a_forged_state(sso_app: FastAPI) -> None:
    assertion = issue_assertion(
        _blog_settings(), User(1, "alice", "alice@example.com", email_verified=True)
    )

    async with build_client(sso_app) as client:
        response = await client.get(
            "/api/sso/callback",
            params={"assertion": assertion, "state": "forged-state"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/?sso=error")
    assert SESSION_COOKIE_NAME not in client.cookies


async def test_grant_tops_up_the_bonus_from_a_blog_ticket(
    sso_app: FastAPI, account_store: AccountStore
) -> None:
    async with build_client(sso_app) as client:
        _ = await sign_in(client, username="grant-member")
        user = await account_store.find_by_email("grant-member@example.com")
        assert user is not None
        before = (await account_store.snapshot(user.id)).remaining

        now = int(time.time())
        ticket = sign_payload(
            _settings(),
            {
                "iss": "blog",
                "aud": "github-accelerator",
                "action": "grant",
                "email": "grant-member@example.com",
                "attempts": 10,
                "iat": now,
                "exp": now + 300,
            },
        )
        response = await client.post("/api/sso/grant", json={"ticket": ticket})

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["attempts_granted"] == 10
    assert response.json()["bonus"] == 10
    assert (await account_store.snapshot(user.id)).remaining == before + 10


async def test_grant_is_unavailable_when_sso_is_disabled(
    client: httpx2.AsyncClient,
) -> None:
    response = await client.post("/api/sso/grant", json={"ticket": "x" * 20})

    assert response.status_code == 503
    assert TypeAdapter(ErrorEnvelope).validate_json(response.content)["error"]["code"] == (
        "sso_unavailable"
    )
