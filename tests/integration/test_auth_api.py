"""Integration tests for the account API and the per-user access quota."""

import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import override

import anyio
import httpx2
import pytest
from app.accounts import AccountStore, build_account_store, hash_password
from app.auth import SESSION_COOKIE_NAME
from app.config import Settings
from app.errors import ErrorEnvelope
from app.mailer import MailDeliveryError
from app.main import create_app
from app.schemas import SessionResponse
from pydantic import TypeAdapter

from tests.support import (
    VERIFICATION_MAILBOX,
    RecordingMailer,
    address_for,
    build_client,
    execute_test_sql,
    opened_store,
    prefixed_table,
    sign_in,
    verification_token,
)

pytestmark = pytest.mark.asyncio

_SOURCE_URL = "https://github.com/acme/widgets/archive/refs/heads/main.zip"
_PAGE = "<!doctype html><html><head><title>Acme</title></head><body>ok</body></html>"
_PASSWORD = "password-1234"


class _Upstream:
    """Record outbound requests and answer them with one configured response."""

    def __init__(self, *, status: int = 200, body: str = _PAGE) -> None:
        self.calls: list[httpx2.Request] = []
        self.status: int = status
        self.body: str = body

    def _handler(self, request: httpx2.Request) -> httpx2.Response:
        self.calls.append(request)
        return httpx2.Response(
            self.status,
            text=self.body,
            headers={"content-type": "text/html; charset=utf-8"},
        )

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self._handler))


@asynccontextmanager
async def _mirror_client(
    tmp_path: Path,
    upstream: _Upstream,
    *,
    limit: int = 2,
) -> AsyncGenerator[httpx2.AsyncClient]:
    """Open a signed-in client whose quota is small enough to exhaust."""
    upstream_client = upstream.client()
    settings = Settings(
        static_dir=tmp_path / "absent",
        usage_limit=limit,
        usage_window_seconds=3600,
    )
    # This helper asserts on its own allowance, so its store has to come from
    # these settings rather than from the shared, effectively unlimited one.
    async with opened_store(build_account_store(settings)) as store:
        app = create_app(
            settings=settings,
            upstream_client=upstream_client,
            capacity_limiter=anyio.CapacityLimiter(2),
            mailer=RecordingMailer(),
            account_store=store,
        )
        try:
            async with build_client(app) as api:
                yield await sign_in(api)
        finally:
            await upstream_client.aclose()


def _write_spa(tmp_path: Path, *, favicon: bool) -> Path:
    static_dir = tmp_path / "dist"
    (static_dir / "assets").mkdir(parents=True)
    shell = static_dir / "index.html"
    _ = shell.write_text("<!doctype html><title>shell</title>", encoding="utf-8")
    bundle = static_dir / "assets" / "app.js"
    _ = bundle.write_text("console.log('shell')", encoding="utf-8")
    if favicon:
        _ = (static_dir / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return static_dir


def _session(response: httpx2.Response) -> SessionResponse:
    return TypeAdapter(SessionResponse).validate_json(response.content)


def _error_code(response: httpx2.Response) -> str:
    return TypeAdapter(ErrorEnvelope).validate_json(response.content)["error"]["code"]


async def _register_and_confirm(client: httpx2.AsyncClient, username: str) -> None:
    """Register an account and consume the link its registration mailed."""
    response = await client.post(
        "/api/auth/register",
        json={"username": username, "password": _PASSWORD, "email": address_for(username)},
    )
    assert response.status_code == 200
    confirmed = await client.get("/api/auth/verify", params={"token": verification_token()})
    assert confirmed.status_code == 200


async def test_registering_waits_for_the_address_to_be_confirmed(
    anon_client: httpx2.AsyncClient,
) -> None:
    VERIFICATION_MAILBOX.clear()
    response = await anon_client.post(
        "/api/auth/register",
        json={"username": "fresh-user", "password": _PASSWORD, "email": "fresh@example.com"},
    )

    assert response.status_code == 200
    body = _session(response)
    assert body.authenticated is False
    assert body.username == "fresh-user"
    assert body.quota is None
    assert body.email == "fresh@example.com"
    assert body.email_verified is False
    # No session is handed out before the address is confirmed.
    assert "set-cookie" not in response.headers
    assert [recipient for recipient, _ in VERIFICATION_MAILBOX] == ["fresh@example.com"]

    pending = await anon_client.post(
        "/api/auth/login",
        json={"username": "fresh-user", "password": _PASSWORD},
    )
    assert pending.status_code == 200
    assert _session(pending).email_verified is False
    assert "set-cookie" not in pending.headers

    confirmed = await anon_client.get(
        "/api/auth/verify",
        params={"token": verification_token()},
    )
    assert confirmed.status_code == 200
    assert _session(confirmed).email_verified is True

    accepted = await anon_client.post(
        "/api/auth/login",
        json={"username": "fresh-user", "password": _PASSWORD},
    )
    assert accepted.status_code == 200
    assert _session(accepted).authenticated is True
    assert "ga_session=" in accepted.headers["set-cookie"]

    me = await anon_client.get("/api/auth/me")
    assert _session(me).username == "fresh-user"
    assert _session(me).email_verified is True


async def test_registering_a_taken_name_is_refused(client: httpx2.AsyncClient) -> None:
    response = await client.post(
        "/api/auth/register",
        json={
            "username": "test-account",
            "password": _PASSWORD,
            "email": "a-different-address@example.com",
        },
    )

    assert response.status_code == 409
    assert _error_code(response) == "username_taken"


@pytest.mark.parametrize(
    "payload",
    [
        {"username": "ab", "password": _PASSWORD, "email": "a@example.com"},
        {"username": "bad name", "password": _PASSWORD, "email": "a@example.com"},
        {"username": "_leading", "password": _PASSWORD, "email": "a@example.com"},
        {"username": "trailing-", "password": _PASSWORD, "email": "a@example.com"},
        {"username": "fresh-user", "password": "short", "email": "a@example.com"},
        {"username": "fresh-user", "password": _PASSWORD, "email": "nowhere"},
        {"username": "fresh-user", "password": _PASSWORD, "email": "nobody@example"},
        {"username": "fresh-user", "password": _PASSWORD, "email": "two@@example.com"},
        {"username": "fresh-user", "password": _PASSWORD},
        {"username": "fresh-user", "password": _PASSWORD, "email": "a@example.com", "extra": True},
    ],
)
async def test_register_rejects_malformed_payloads(
    anon_client: httpx2.AsyncClient,
    payload: dict[str, object],
) -> None:
    response = await anon_client.post("/api/auth/register", json=payload)

    assert response.status_code == 400
    assert _error_code(response) == "invalid_request"


async def test_login_accepts_only_the_stored_password(anon_client: httpx2.AsyncClient) -> None:
    await _register_and_confirm(anon_client, "member")
    _ = await anon_client.post("/api/auth/logout")

    wrong = await anon_client.post(
        "/api/auth/login",
        json={"username": "member", "password": "not-the-password"},
    )
    assert wrong.status_code == 401
    assert _error_code(wrong) == "invalid_credentials"

    unknown = await anon_client.post(
        "/api/auth/login",
        json={"username": "ghost", "password": _PASSWORD},
    )
    assert unknown.status_code == 401
    assert _error_code(unknown) == "invalid_credentials"

    accepted = await anon_client.post(
        "/api/auth/login",
        json={"username": "member", "password": _PASSWORD},
    )
    assert accepted.status_code == 200
    assert _session(accepted).authenticated is True


async def test_logout_clears_the_session(client: httpx2.AsyncClient) -> None:
    response = await client.post("/api/auth/logout")

    assert response.status_code == 200
    assert _session(response).authenticated is False
    assert "ga_session=" in response.headers["set-cookie"]

    me = await client.get("/api/auth/me")
    assert _session(me).authenticated is False

    blocked = await client.post("/api/resolve", json={"url": _SOURCE_URL})
    assert blocked.status_code == 401
    assert _error_code(blocked) == "unauthorized"


async def test_an_anonymous_visitor_reads_an_empty_session(
    anon_client: httpx2.AsyncClient,
) -> None:
    response = await anon_client.get("/api/auth/me")

    assert response.status_code == 200
    body = _session(response)
    assert body.authenticated is False
    assert body.username is None
    assert body.quota is None


async def test_protected_endpoints_require_a_session(anon_client: httpx2.AsyncClient) -> None:
    resolve = await anon_client.post("/api/resolve", json={"url": _SOURCE_URL})

    assert resolve.status_code == 401
    assert _error_code(resolve) == "unauthorized"


async def test_a_browser_is_redirected_to_the_sign_in_screen(
    anon_client: httpx2.AsyncClient,
) -> None:
    response = await anon_client.get("/acme/widgets", headers={"accept": "text/html"})

    assert response.status_code == 302
    assert response.headers["location"] == "/"


async def test_the_quota_is_spent_once_per_mirrored_access(tmp_path: Path) -> None:
    upstream = _Upstream()

    async with _mirror_client(tmp_path, upstream, limit=2) as api:
        first = await api.get("/acme/widgets")
        second = await api.get("/acme/other")
        third = await api.get("/acme/third")
        me = await api.get("/api/auth/me")

    assert [first.status_code, second.status_code] == [200, 200]
    assert third.status_code == 429
    assert _error_code(third) == "quota_exceeded"
    assert int(third.headers["retry-after"]) > 0
    quota = _session(me).quota
    assert quota is not None
    assert quota.limit == 2
    assert quota.remaining == 0
    assert len(upstream.calls) == 2


async def test_a_failed_mirror_does_not_spend_the_quota(tmp_path: Path) -> None:
    upstream = _Upstream(status=404, body="<html>missing</html>")

    async with _mirror_client(tmp_path, upstream, limit=1) as api:
        missing = await api.get("/acme/missing")
        upstream.status = 200
        served = await api.get("/acme/widgets")
        me = await api.get("/api/auth/me")

    assert missing.status_code == 404
    assert served.status_code == 200
    quota = _session(me).quota
    assert quota is not None
    assert quota.remaining == 0


async def test_a_head_probe_does_not_spend_the_quota(tmp_path: Path) -> None:
    upstream = _Upstream()

    async with _mirror_client(tmp_path, upstream, limit=1) as api:
        probe = await api.head("/acme/widgets")
        me = await api.get("/api/auth/me")

    assert probe.status_code == 200
    quota = _session(me).quota
    assert quota is not None
    assert quota.remaining == 1


async def test_the_built_shell_is_public_and_serves_its_assets(
    tmp_path: Path,
    account_store: AccountStore,
) -> None:
    app = create_app(
        settings=Settings(static_dir=_write_spa(tmp_path, favicon=True)),
        account_store=account_store,
    )

    async with build_client(app) as api:
        shell = await api.get("/")
        asset = await api.get("/assets/app.js")
        icon = await api.get("/favicon.svg")

    assert shell.status_code == 200
    assert b"shell" in shell.content
    assert shell.headers["cache-control"] == "no-cache"
    assert asset.status_code == 200
    assert asset.content == b"console.log('shell')"
    assert icon.status_code == 200


async def test_a_build_without_an_icon_reports_not_found(
    tmp_path: Path,
    account_store: AccountStore,
) -> None:
    app = create_app(
        settings=Settings(static_dir=_write_spa(tmp_path, favicon=False)),
        account_store=account_store,
    )

    async with build_client(app) as api:
        response = await api.get("/favicon.svg")

    assert response.status_code == 404
    assert _error_code(response) == "not_found"


class _BrokenMailer(RecordingMailer):
    """Record the attempt, then fail the way an unreachable server would."""

    @override
    async def send_verification(self, recipient: str, link: str) -> None:
        await super().send_verification(recipient, link)
        raise MailDeliveryError


async def _seed_account_without_address(username: str) -> None:
    """Insert an account the way the store wrote one before verification."""
    columns = "(username, password_hash, created_at) VALUES(%s, %s, %s)"
    statement = "INSERT INTO `" + prefixed_table("users") + "`" + columns
    await execute_test_sql(statement, (username, hash_password(_PASSWORD), time.time()))


async def test_a_browser_follows_the_mailed_link_into_the_shell(
    anon_client: httpx2.AsyncClient,
) -> None:
    VERIFICATION_MAILBOX.clear()
    _ = await anon_client.post(
        "/api/auth/register",
        json={"username": "browser-user", "password": _PASSWORD, "email": "b@example.com"},
    )

    landed = await anon_client.get(
        "/api/auth/verify",
        params={"token": verification_token()},
        headers={"accept": "text/html"},
    )

    assert landed.status_code == 303
    assert landed.headers["location"] == "/?verified=1"
    assert SESSION_COOKIE_NAME in anon_client.cookies

    # Opening the link proved control of the address, so the browser arrives
    # already signed in and the visitor never types the password a second time.
    session = _session(await anon_client.get("/api/auth/me"))
    assert session.authenticated is True
    assert session.username == "browser-user"
    assert session.email_verified is True


async def test_a_browser_with_a_spent_link_returns_with_a_flag(
    anon_client: httpx2.AsyncClient,
) -> None:
    landed = await anon_client.get(
        "/api/auth/verify",
        params={"token": "already-spent"},
        headers={"accept": "text/html"},
    )

    assert landed.status_code == 303
    assert landed.headers["location"] == "/?verify_failed=1"


async def test_a_script_gets_a_json_refusal_for_an_unknown_token(
    anon_client: httpx2.AsyncClient,
) -> None:
    response = await anon_client.get(
        "/api/auth/verify",
        params={"token": "already-spent"},
        headers={"accept": "application/json"},
    )

    assert response.status_code == 400
    assert _error_code(response) == "verification_invalid"


async def test_a_script_confirms_an_address_without_gaining_a_session(
    anon_client: httpx2.AsyncClient,
) -> None:
    VERIFICATION_MAILBOX.clear()
    _ = await anon_client.post(
        "/api/auth/register",
        json={"username": "script-user", "password": _PASSWORD, "email": "s@example.com"},
    )

    confirmed = await anon_client.get(
        "/api/auth/verify",
        params={"token": verification_token()},
        headers={"accept": "application/json"},
    )

    assert confirmed.status_code == 200
    assert _session(confirmed).email_verified is True
    # Only the browser entry point signs anyone in, so a script still has to
    # ask for a session explicitly.
    assert SESSION_COOKIE_NAME not in anon_client.cookies
    assert _session(await anon_client.get("/api/auth/me")).authenticated is False


async def test_a_link_can_be_requested_again_without_a_new_address(
    anon_client: httpx2.AsyncClient,
) -> None:
    VERIFICATION_MAILBOX.clear()
    registered = await anon_client.post(
        "/api/auth/register",
        json={"username": "again-user", "password": _PASSWORD, "email": "again@example.com"},
    )
    assert registered.status_code == 200

    requested = await anon_client.post(
        "/api/auth/verify/request",
        json={"username": "again-user", "password": _PASSWORD},
    )

    assert requested.status_code == 200
    body = _session(requested)
    assert body.email == "again@example.com"
    assert body.email_verified is False
    assert len(VERIFICATION_MAILBOX) == 2

    confirmed = await anon_client.get("/api/auth/verify", params={"token": verification_token()})
    assert _session(confirmed).email_verified is True


async def test_an_account_without_an_address_must_supply_one(
    tmp_path: Path,
    account_store: AccountStore,
) -> None:
    await _seed_account_without_address("legacy")
    app = create_app(
        settings=Settings(static_dir=tmp_path / "absent"),
        mailer=RecordingMailer(),
        account_store=account_store,
    )

    async with build_client(app) as api:
        without = await api.post(
            "/api/auth/verify/request",
            json={"username": "legacy", "password": _PASSWORD},
        )
        attached = await api.post(
            "/api/auth/verify/request",
            json={"username": "legacy", "password": _PASSWORD, "email": "legacy@example.com"},
        )

    assert without.status_code == 400
    assert _error_code(without) == "invalid_request"
    assert attached.status_code == 200
    body = _session(attached)
    assert body.authenticated is False
    assert body.email == "legacy@example.com"
    assert body.email_verified is False


async def test_requesting_a_link_for_a_confirmed_account_is_refused(
    anon_client: httpx2.AsyncClient,
) -> None:
    await _register_and_confirm(anon_client, "confirmed")

    response = await anon_client.post(
        "/api/auth/verify/request",
        json={"username": "confirmed", "password": _PASSWORD},
    )

    assert response.status_code == 409
    assert _error_code(response) == "already_verified"


async def test_a_wrong_password_never_mails_a_link(anon_client: httpx2.AsyncClient) -> None:
    VERIFICATION_MAILBOX.clear()
    response = await anon_client.post(
        "/api/auth/verify/request",
        json={"username": "ghost", "password": _PASSWORD, "email": "ghost@example.com"},
    )

    assert response.status_code == 401
    assert _error_code(response) == "invalid_credentials"
    assert VERIFICATION_MAILBOX == []


async def test_a_failing_transport_rolls_the_registration_back(
    tmp_path: Path,
    account_store: AccountStore,
) -> None:
    app = create_app(
        settings=Settings(static_dir=tmp_path / "absent"),
        mailer=_BrokenMailer(),
        account_store=account_store,
    )

    async with build_client(app) as api:
        failed = await api.post(
            "/api/auth/register",
            json={"username": "mail-fails", "password": _PASSWORD, "email": "mf@example.com"},
        )
        retried = await api.post(
            "/api/auth/register",
            json={"username": "mail-fails", "password": _PASSWORD, "email": "mf@example.com"},
        )

    assert failed.status_code == 503
    assert _error_code(failed) == "email_unavailable"
    # The row went back with the failure, so the name is still free: a second
    # attempt fails for the transport again instead of reporting a conflict.
    assert retried.status_code == 503
    assert _error_code(retried) == "email_unavailable"


async def test_a_link_inside_the_cooldown_is_refused(tmp_path: Path) -> None:
    settings = Settings(static_dir=tmp_path / "absent", verification_resend_seconds=60)
    # The cooldown is this test's own, so its store cannot be the shared,
    # throttle-free one from the fixture.
    async with opened_store(build_account_store(settings)) as store:
        app = create_app(
            settings=settings,
            mailer=RecordingMailer(),
            account_store=store,
        )

        async with build_client(app) as api:
            registered = await api.post(
                "/api/auth/register",
                json={"username": "cooldown", "password": _PASSWORD, "email": "cool@example.com"},
            )
            again = await api.post(
                "/api/auth/verify/request",
                json={"username": "cooldown", "password": _PASSWORD, "email": "cool@example.com"},
            )

    assert registered.status_code == 200
    assert again.status_code == 429
    assert _error_code(again) == "verification_throttled"
    assert int(again.headers["retry-after"]) > 0


async def test_registering_a_taken_address_is_refused(anon_client: httpx2.AsyncClient) -> None:
    VERIFICATION_MAILBOX.clear()
    holder = await anon_client.post(
        "/api/auth/register",
        json={"username": "address-holder", "password": _PASSWORD, "email": "shared@example.com"},
    )
    rival = await anon_client.post(
        "/api/auth/register",
        json={"username": "address-rival", "password": _PASSWORD, "email": "shared@example.com"},
    )

    assert holder.status_code == 200
    assert rival.status_code == 409
    assert _error_code(rival) == "email_taken"
