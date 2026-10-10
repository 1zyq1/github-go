"""Integration tests for the same-origin github.com reverse proxy."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import httpx2
import pytest
from app.accounts import AccountStore
from app.config import Settings
from app.errors import ErrorEnvelope
from app.htmlmirror import is_rewritable_html, mirror_body
from app.main import create_app
from app.url_policy import decode_proxy_token
from pydantic import TypeAdapter

from tests.support import RecordingMailer, sign_in

pytestmark = pytest.mark.asyncio

_PAGE = "<!doctype html><html><body>mirrored github page</body></html>"
_ARCHIVE_PATH = "acme/widgets/archive/refs/heads/main.zip"
_MARKUP = (
    "<!doctype html><html><head>"
    '<link rel="modulepreload" href="https://github.githubassets.com/x.js">'
    "<script>window.boot = 1;</script>"
    '<script src="https://github.githubassets.com/app.js" defer></script>'
    "</head><body><p>kept text</p></body></html>"
)


class _Upstream:
    """Record upstream requests and answer them with one configured response."""

    def __init__(self) -> None:
        """Start from an HTML 200 that each test may override."""
        self.calls: list[httpx2.Request] = []
        self.status_code: int = 200
        self.headers: dict[str, str] = {"content-type": "text/html; charset=utf-8"}
        self.body: str = _PAGE

    def _handler(self, request: httpx2.Request) -> httpx2.Response:
        self.calls.append(request)
        return httpx2.Response(self.status_code, text=self.body, headers=self.headers)

    def client(self) -> httpx2.AsyncClient:
        """Build the mocked outbound client handed to the application."""
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self._handler))


@asynccontextmanager
async def _browse(
    account_store: AccountStore,
    upstream: _Upstream,
    tmp_path: Path,
) -> AsyncGenerator[httpx2.AsyncClient]:
    upstream_client = upstream.client()
    api = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(
            app=create_app(
                Settings(static_dir=tmp_path / "absent"),
                upstream_client=upstream_client,
                capacity_limiter=anyio.CapacityLimiter(1),
                mailer=RecordingMailer(),
                account_store=account_store,
            )
        ),
        base_url="http://test",
    )
    try:
        yield await sign_in(api)
    finally:
        await api.aclose()
        await upstream_client.aclose()


def _error_code(response: httpx2.Response) -> str:
    payload = TypeAdapter(ErrorEnvelope).validate_json(response.content)
    return payload["error"]["code"]


async def test_profile_path_is_mirrored_from_github(
    tmp_path: Path, account_store: AccountStore
) -> None:
    upstream = _Upstream()

    async with _browse(account_store, upstream, tmp_path) as api:
        response = await api.get("/1zyq1")

    assert response.status_code == 200
    assert response.text == _PAGE
    assert [str(call.url) for call in upstream.calls] == ["https://github.com/1zyq1"]


async def test_query_string_is_preserved(tmp_path: Path, account_store: AccountStore) -> None:
    upstream = _Upstream()

    async with _browse(account_store, upstream, tmp_path) as api:
        response = await api.get("/acme/widgets", params={"tab": "readme-ov-file"})

    assert response.status_code == 200
    assert [str(call.url) for call in upstream.calls] == [
        "https://github.com/acme/widgets?tab=readme-ov-file"
    ]


async def test_client_credentials_are_not_forwarded(
    tmp_path: Path, account_store: AccountStore
) -> None:
    upstream = _Upstream()

    async with _browse(account_store, upstream, tmp_path) as api:
        # Attach the extra cookie alongside the session cookie the guard needs,
        # rather than replacing it; none of these may reach GitHub, the session
        # cookie included.
        api.cookies.set("session", "secret")
        response = await api.get(
            "/acme/widgets",
            headers={
                "Authorization": "Bearer secret",
                "X-Forwarded-For": "10.0.0.1",
            },
        )

    assert response.status_code == 200
    sent = upstream.calls[0].headers
    assert "cookie" not in sent
    assert "authorization" not in sent
    assert "x-forwarded-for" not in sent


async def test_github_redirect_is_rewritten_to_this_origin(
    tmp_path: Path, account_store: AccountStore
) -> None:
    upstream = _Upstream()
    upstream.status_code = 302
    upstream.headers = {"location": "https://github.com/login"}

    async with _browse(account_store, upstream, tmp_path) as api:
        response = await api.get("/settings")

    assert response.status_code == 302
    assert response.headers["location"] == "/login"
    assert response.content == b""


async def test_foreign_redirect_is_passed_through(
    tmp_path: Path, account_store: AccountStore
) -> None:
    upstream = _Upstream()
    upstream.status_code = 301
    upstream.headers = {"location": "https://example.com/away"}

    async with _browse(account_store, upstream, tmp_path) as api:
        response = await api.get("/acme/widgets")

    assert response.status_code == 301
    assert response.headers["location"] == "https://example.com/away"


async def test_page_policy_allows_assets_but_refuses_scripts(
    tmp_path: Path, account_store: AccountStore
) -> None:
    upstream = _Upstream()

    async with _browse(account_store, upstream, tmp_path) as api:
        response = await api.get("/1zyq1")

    policy = response.headers["content-security-policy"]
    assert "script-src 'none'" in policy
    assert "github.githubassets.com" in policy
    assert "avatars.githubusercontent.com" in policy
    assert "base-uri 'self'" in policy


async def test_a_mirrored_page_is_kept_out_of_search_results(
    tmp_path: Path, account_store: AccountStore
) -> None:
    upstream = _Upstream()

    async with _browse(account_store, upstream, tmp_path) as api:
        response = await api.get("/1zyq1")

    assert response.headers["x-robots-tag"] == "noindex, nofollow"


async def test_upstream_set_cookie_is_not_forwarded(
    tmp_path: Path, account_store: AccountStore
) -> None:
    upstream = _Upstream()
    upstream.headers = {
        "content-type": "text/html; charset=utf-8",
        "set-cookie": "session=secret; Path=/",
    }

    async with _browse(account_store, upstream, tmp_path) as api:
        response = await api.get("/1zyq1")

    assert response.status_code == 200
    assert "set-cookie" not in response.headers


async def test_head_request_has_no_body(tmp_path: Path, account_store: AccountStore) -> None:
    upstream = _Upstream()

    async with _browse(account_store, upstream, tmp_path) as api:
        response = await api.head("/1zyq1")

    assert response.status_code == 200
    assert response.content == b""


async def test_archive_path_is_handed_to_the_file_proxy(
    tmp_path: Path, account_store: AccountStore
) -> None:
    upstream = _Upstream()

    async with _browse(account_store, upstream, tmp_path) as api:
        response = await api.get(f"/{_ARCHIVE_PATH}")

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("/proxy/")
    token = location.removeprefix("/proxy/")
    assert decode_proxy_token(token).url == f"https://github.com/{_ARCHIVE_PATH}"
    assert upstream.calls == []


@pytest.mark.parametrize("path", ["/api/does-not-exist", "/proxy/token/extra"])
async def test_reserved_prefixes_never_reach_github(
    tmp_path: Path, path: str, account_store: AccountStore
) -> None:
    upstream = _Upstream()

    async with _browse(account_store, upstream, tmp_path) as api:
        response = await api.get(path)

    assert response.status_code == 404
    assert _error_code(response) == "not_found"
    assert upstream.calls == []


@pytest.mark.parametrize("path", ["/a%5Cb", "/a/%2e%2e/b"])
async def test_malformed_path_is_rejected(
    tmp_path: Path, path: str, account_store: AccountStore
) -> None:
    upstream = _Upstream()

    async with _browse(account_store, upstream, tmp_path) as api:
        response = await api.get(path)

    assert response.status_code == 400
    assert _error_code(response) == "invalid_request"
    assert upstream.calls == []


async def test_scripts_are_stripped_from_mirrored_markup(
    tmp_path: Path, account_store: AccountStore
) -> None:
    upstream = _Upstream()
    upstream.body = _MARKUP

    async with _browse(account_store, upstream, tmp_path) as api:
        response = await api.get("/1zyq1")

    assert response.status_code == 200
    assert "<script" not in response.text
    assert "modulepreload" not in response.text
    assert "<p>kept text</p>" in response.text


async def test_content_length_is_dropped_when_the_body_is_rewritten(
    tmp_path: Path, account_store: AccountStore
) -> None:
    upstream = _Upstream()
    upstream.body = _MARKUP

    async with _browse(account_store, upstream, tmp_path) as api:
        response = await api.get("/1zyq1")

    assert "content-length" not in response.headers


async def test_non_html_body_is_passed_through_unchanged(
    tmp_path: Path, account_store: AccountStore
) -> None:
    upstream = _Upstream()
    upstream.headers = {"content-type": "application/json"}
    upstream.body = '{"kept": "<script>not markup</script>"}'

    async with _browse(account_store, upstream, tmp_path) as api:
        response = await api.get("/1zyq1")

    assert response.text == '{"kept": "<script>not markup</script>"}'


async def test_rewrite_guard_and_body_size_limit() -> None:
    identity = {"content-type": "text/html", "content-encoding": "identity"}
    assert is_rewritable_html({"content-type": "text/html"}) is True
    assert is_rewritable_html(identity) is True
    assert is_rewritable_html({"content-type": "text/html", "content-encoding": "gzip"}) is False
    assert is_rewritable_html({"content-type": "application/json"}) is False
    assert is_rewritable_html({}) is False

    small = httpx2.Response(200, content=b"<p>a</p><script>x</script>")
    assert [chunk async for chunk in mirror_body(small, 1024)] == [b"<p>a</p>"]

    oversized = b"<p>" + b"a" * 64 + b"</p><script>x</script>"
    response = httpx2.Response(200, content=oversized)
    assert b"".join([chunk async for chunk in mirror_body(response, 16)]) == oversized


async def test_mirrored_page_is_gzipped_when_the_client_accepts_it(
    tmp_path: Path, account_store: AccountStore
) -> None:
    upstream = _Upstream()
    upstream.body = _MARKUP
    upstream.headers["vary"] = "Cookie"

    async with _browse(account_store, upstream, tmp_path) as api:
        response = await api.get("/1zyq1", headers={"accept-encoding": "gzip, deflate, br"})

    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["vary"] == "Cookie, Accept-Encoding"
    assert "content-length" not in response.headers
    assert "<p>kept text</p>" in response.text
    assert "<script" not in response.text


async def test_mirrored_page_stays_plain_without_gzip_support(
    tmp_path: Path, account_store: AccountStore
) -> None:
    upstream = _Upstream()
    upstream.body = _MARKUP

    async with _browse(account_store, upstream, tmp_path) as api:
        response = await api.get("/1zyq1", headers={"accept-encoding": "identity"})

    assert "content-encoding" not in response.headers
    assert "vary" not in response.headers
    assert "<p>kept text</p>" in response.text
