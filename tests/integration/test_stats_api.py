"""Integration tests for the public aggregate usage report."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import httpx2
import pytest
from app.accounts import AccountStore, build_account_store
from app.config import Settings
from app.main import create_app
from app.schemas import SessionResponse, StatsResponse
from pydantic import TypeAdapter

from tests.support import RecordingMailer, build_client, opened_store, sign_in

pytestmark = pytest.mark.asyncio

_LIMIT = 10
_KEYS = ["hour", "day", "week", "month", "total"]
_PAGE = "<!doctype html><html><head><title>Acme</title></head><body>ok</body></html>"


class _Upstream:
    """Answer every outbound request with one configured response."""

    def __init__(self, *, status: int = 200, body: str = _PAGE) -> None:
        self.status: int = status
        self.body: str = body

    def _handler(self, _request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            self.status,
            text=self.body,
            headers={"content-type": "text/html; charset=utf-8"},
        )

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self._handler))


def _write_spa(tmp_path: Path) -> Path:
    """Write the smallest built frontend the shell route will accept."""
    static_dir = tmp_path / "dist"
    (static_dir / "assets").mkdir(parents=True)
    shell = static_dir / "index.html"
    _ = shell.write_text("<!doctype html><title>shell</title>", encoding="utf-8")
    bundle = static_dir / "assets" / "app.js"
    _ = bundle.write_text("console.log('shell')", encoding="utf-8")
    return static_dir


@asynccontextmanager
async def _built_client(
    tmp_path: Path,
    upstream: _Upstream,
) -> AsyncGenerator[tuple[httpx2.AsyncClient, httpx2.AsyncClient]]:
    """Open an anonymous and a signed-in client over one built application."""
    upstream_client = upstream.client()
    settings = Settings(
        static_dir=_write_spa(tmp_path),
        usage_limit=_LIMIT,
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
            async with build_client(app) as anon, build_client(app) as member:
                yield anon, await sign_in(member)
        finally:
            await upstream_client.aclose()


def _stats(response: httpx2.Response) -> StatsResponse:
    return TypeAdapter(StatsResponse).validate_json(response.content)


def _session(response: httpx2.Response) -> SessionResponse:
    return TypeAdapter(SessionResponse).validate_json(response.content)


async def test_the_report_is_public_and_starts_empty(tmp_path: Path) -> None:
    async with _built_client(tmp_path, _Upstream()) as (anon, _member):
        response = await anon.get("/api/stats")

    assert response.status_code == 200
    windows = _stats(response).windows
    assert [window.key for window in windows] == _KEYS
    assert all(window.downloads == 0 for window in windows)
    assert all(window.pages == 0 for window in windows)
    assert all(window.users == 0 for window in windows)


async def test_a_delivered_page_is_reported(tmp_path: Path) -> None:
    async with _built_client(tmp_path, _Upstream()) as (_anon, member):
        served = await member.get("/acme/widgets")
        report = await member.get("/api/stats")

    assert served.status_code == 200
    by_key = {window.key: window for window in _stats(report).windows}
    assert by_key["hour"].pages == 1
    assert by_key["hour"].downloads == 0
    assert by_key["hour"].users == 1
    assert by_key["total"].pages == 1


async def test_a_failed_mirror_is_absent_from_the_report(tmp_path: Path) -> None:
    upstream = _Upstream(status=404, body="<html>missing</html>")

    async with _built_client(tmp_path, upstream) as (_anon, member):
        missing = await member.get("/acme/missing")
        report = await member.get("/api/stats")

    assert missing.status_code == 404
    windows = _stats(report).windows
    assert all(window.pages == 0 for window in windows)
    assert all(window.users == 0 for window in windows)


async def test_reading_the_report_does_not_spend_the_quota(tmp_path: Path) -> None:
    async with _built_client(tmp_path, _Upstream()) as (anon, member):
        for _ in range(3):
            _ = await anon.get("/api/stats")
        me = await member.get("/api/auth/me")

    quota = _session(me).quota
    assert quota is not None
    assert quota.limit == _LIMIT
    assert quota.remaining == _LIMIT


async def test_the_data_page_serves_the_shell_to_anyone(tmp_path: Path) -> None:
    async with _built_client(tmp_path, _Upstream()) as (anon, _member):
        page = await anon.get("/stats")

    assert page.status_code == 200
    assert b"shell" in page.content
    assert page.headers["cache-control"] == "no-cache"


async def test_the_api_only_mode_keeps_the_report_behind_the_gate(
    tmp_path: Path, account_store: AccountStore
) -> None:
    app = create_app(
        settings=Settings(static_dir=tmp_path / "absent"),
        account_store=account_store,
    )

    async with build_client(app) as anon:
        response = await anon.get("/api/stats")

    assert response.status_code == 401
