"""Shared helpers for the backend test suite."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlparse

import httpx2
from app.auth import SESSION_COOKIE_NAME
from fastapi import FastAPI

TEST_USERNAME = "test-account"
TEST_PASSWORD = "test-password-1234"
# Functional tests are not about the product quota, so they run with a limit no
# test can trip by accident. Quota behaviour has its own dedicated tests.
UNLIMITED_USAGE = 1_000_000

# Every verification mail the application under test produced, in send order.
# A module-level mailbox is enough because the suite runs in one process and
# every signed-in test account goes through :func:`sign_in`.
VERIFICATION_MAILBOX: list[tuple[str, str]] = []


def address_for(username: str) -> str:
    """Return a unique, valid address for a test account."""
    return f"{username}@example.com"


class RecordingMailer:
    """Capture verification mail so a test can consume the link itself."""

    async def send_verification(self, recipient: str, link: str) -> None:
        """Record the message instead of delivering it."""
        VERIFICATION_MAILBOX.append((recipient, link))


def verification_token(index: int = -1) -> str:
    """Return the token carried by a captured verification link."""
    if not VERIFICATION_MAILBOX:
        message = "no verification mail was captured"
        raise RuntimeError(message)
    link = VERIFICATION_MAILBOX[index][1]
    found = parse_qs(urlparse(link).query).get("token") or [""]
    return found[0]


async def _consume_link(client: httpx2.AsyncClient) -> None:
    """Open the newest verification link, failing loudly when it is refused."""
    response = await client.get("/api/auth/verify", params={"token": verification_token()})
    if response.status_code != 200:
        message = f"test verification failed: {response.status_code} {response.text}"
        raise RuntimeError(message)


async def sign_in(client: httpx2.AsyncClient, username: str = TEST_USERNAME) -> httpx2.AsyncClient:
    """Sign a throwaway account in, registering and verifying it when needed."""
    credentials = {"username": username, "password": TEST_PASSWORD}
    VERIFICATION_MAILBOX.clear()
    response = await client.post(
        "/api/auth/register",
        json={**credentials, "email": address_for(username)},
    )
    if response.status_code == 409:
        # A test that builds several applications still shares one account
        # database, so the name may already be taken by an earlier sign-in.
        # That account may still be unconfirmed, so it gets a fresh link.
        response = await client.post("/api/auth/login", json=credentials)
        if response.status_code == 200 and SESSION_COOKIE_NAME not in client.cookies:
            requested = await client.post(
                "/api/auth/verify/request",
                json={**credentials, "email": address_for(username)},
            )
            if requested.status_code != 200:
                detail = f"{requested.status_code} {requested.text}"
                message = f"test verification request failed: {detail}"
                raise RuntimeError(message)
            await _consume_link(client)
            response = await client.post("/api/auth/login", json=credentials)
    else:
        await _consume_link(client)
        response = await client.post("/api/auth/login", json=credentials)
    if response.status_code != 200:
        message = f"test sign-in failed: {response.status_code} {response.text}"
        raise RuntimeError(message)
    return client


@asynccontextmanager
async def build_client(app: FastAPI) -> AsyncGenerator[httpx2.AsyncClient]:
    """Open a cookie-aware HTTPX2 client bound to the ASGI application."""
    transport = httpx2.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx2.AsyncClient(base_url="http://test", transport=transport) as client:
        yield client
