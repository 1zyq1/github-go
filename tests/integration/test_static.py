"""Integration tests for SPA serving, API isolation, and security headers."""

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import httpx2
import pytest
from app.accounts import AccountStore
from app.config import Settings
from app.errors import ErrorEnvelope
from app.main import create_app
from app.schemas import ResolveResponse
from pydantic import TypeAdapter

from tests.support import RecordingMailer, sign_in

pytestmark = pytest.mark.asyncio

_INDEX_HTML = '<!doctype html><html lang="zh-CN"><body><div id="app"></div></body></html>'
# Comfortably past GZipMiddleware's 500-byte floor, so the gzip path is reachable.
_ASSET_JS = 'document.documentElement.dataset.ready = "1";' + "/* vendor chunk */" * 60
_FONT_WOFF2 = b"wOF2" + bytes(1996)
_FAVICON = '<svg xmlns="http://www.w3.org/2000/svg" />'
_SOURCE_URL = "https://raw.githubusercontent.com/acme/widgets/main/file.bin"
_GITHUB_PAGE = "<!doctype html><html><body>mirrored github page</body></html>"


def _write_dist(root: Path) -> Path:
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "fonts").mkdir()
    _ = (dist / "index.html").write_text(_INDEX_HTML, encoding="utf-8")
    _ = (dist / "assets" / "app.js").write_text(_ASSET_JS, encoding="utf-8")
    _ = (dist / "fonts" / "inter.woff2").write_bytes(_FONT_WOFF2)
    _ = (dist / "favicon.svg").write_text(_FAVICON, encoding="utf-8")
    return dist


def _github_page(_request: httpx2.Request) -> httpx2.Response:
    """Answer every upstream request with a recognizable GitHub page."""
    return httpx2.Response(
        200,
        text=_GITHUB_PAGE,
        headers={"content-type": "text/html; charset=utf-8"},
    )


@asynccontextmanager
async def _client(
    account_store: AccountStore,
    settings: Settings,
    handler: Callable[[httpx2.Request], httpx2.Response] = _github_page,
) -> AsyncGenerator[httpx2.AsyncClient]:
    upstream = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    api = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(
            app=create_app(
                settings,
                upstream_client=upstream,
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
        await upstream.aclose()


def _error_code(response: httpx2.Response) -> str:
    payload = TypeAdapter(ErrorEnvelope).validate_json(response.content)
    return payload["error"]["code"]


async def test_serves_index_and_built_assets_when_dist_exists(
    tmp_path: Path, account_store: AccountStore
) -> None:
    settings = Settings(static_dir=_write_dist(tmp_path))

    async with _client(account_store, settings) as api:
        index = await api.get("/")
        asset = await api.get("/assets/app.js")
        favicon = await api.get("/favicon.svg")

    assert index.status_code == 200
    assert index.headers["content-type"].startswith("text/html")
    # The shell names the hashed bundle, so it must always be revalidated or a
    # cached copy pins the browser to a stale build.
    assert index.headers["cache-control"] == "no-cache"
    assert _INDEX_HTML in index.text
    assert asset.status_code == 200
    assert _ASSET_JS in asset.text
    assert asset.headers["content-type"].startswith(("text/javascript", "application/javascript"))
    assert favicon.status_code == 200
    assert favicon.headers["content-type"].startswith("image/svg+xml")


async def test_unknown_route_is_mirrored_instead_of_served_as_index(
    tmp_path: Path, account_store: AccountStore
) -> None:
    settings = Settings(static_dir=_write_dist(tmp_path))

    async with _client(account_store, settings) as api:
        unknown = await api.get("/does-not-exist")

    assert unknown.status_code == 200
    assert _GITHUB_PAGE in unknown.text
    assert _INDEX_HTML not in unknown.text


async def test_api_and_proxy_prefixes_keep_json_404(
    tmp_path: Path, account_store: AccountStore
) -> None:
    settings = Settings(static_dir=_write_dist(tmp_path))

    async with _client(account_store, settings) as api:
        unknown_api = await api.get("/api/does-not-exist")
        unknown_proxy = await api.get("/proxy/token/extra")
        wrong_method = await api.get("/api/resolve")

    assert _error_code(unknown_api) == "not_found"
    assert _error_code(unknown_proxy) == "not_found"
    assert _error_code(wrong_method) == "not_found"


async def test_api_only_mode_mirrors_github_at_the_root(
    tmp_path: Path, account_store: AccountStore
) -> None:
    settings = Settings(static_dir=tmp_path / "absent")

    async with _client(account_store, settings) as api:
        root = await api.get("/")
        resolved = await api.post("/api/resolve", json={"url": _SOURCE_URL})

    assert root.status_code == 200
    assert _GITHUB_PAGE in root.text
    assert resolved.status_code == 200
    assert (
        TypeAdapter(ResolveResponse).validate_json(resolved.content).proxy_url.startswith("/proxy/")
    )


@pytest.mark.parametrize("path", ["/api/health", "/", "/assets/app.js"])
async def test_security_headers_are_present(
    tmp_path: Path, path: str, account_store: AccountStore
) -> None:
    settings = Settings(static_dir=_write_dist(tmp_path))

    async with _client(account_store, settings) as api:
        response = await api.get(path)

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
    assert response.headers["x-frame-options"] == "DENY"
    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    # The shell loads exactly one third-party script, so its origin is named
    # rather than opening script-src to a whole scheme.
    assert "script-src 'self' https://u.1zyq1.com" in csp
    assert "connect-src 'self' https://u.1zyq1.com" in csp


async def test_the_crawl_documents_are_public_and_self_describing(
    tmp_path: Path, account_store: AccountStore
) -> None:
    settings = Settings(static_dir=_write_dist(tmp_path))
    base = settings.public_base_url.rstrip("/")

    async with _client(account_store, settings) as api:
        # Dropping the cookie proves the gate leaves both documents open.
        api.cookies.clear()
        robots = await api.get("/robots.txt")
        sitemap = await api.get("/sitemap.xml")

    assert robots.status_code == 200
    assert robots.headers["content-type"].startswith("text/plain")
    assert "Disallow: /api/" in robots.text
    assert "Disallow: /proxy/" in robots.text
    assert f"Sitemap: {base}/sitemap.xml" in robots.text
    assert sitemap.status_code == 200
    assert sitemap.headers["content-type"].startswith("application/xml")
    assert f"<loc>{base}/</loc>" in sitemap.text
    assert f"<loc>{base}/stats</loc>" in sitemap.text


async def test_the_preview_card_is_served_when_the_build_has_one(
    tmp_path: Path, account_store: AccountStore
) -> None:
    static_dir = _write_dist(tmp_path)
    _ = (static_dir / "og.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(24))
    settings = Settings(static_dir=static_dir)

    async with _client(account_store, settings) as api:
        response = await api.get("/og.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


async def test_a_build_without_a_preview_card_reports_not_found(
    tmp_path: Path, account_store: AccountStore
) -> None:
    settings = Settings(static_dir=_write_dist(tmp_path))

    async with _client(account_store, settings) as api:
        response = await api.get("/og.png")

    assert response.status_code == 404
    assert _error_code(response) == "not_found"


async def test_text_assets_are_gzipped_when_the_client_accepts_it(
    tmp_path: Path, account_store: AccountStore
) -> None:
    settings = Settings(static_dir=_write_dist(tmp_path))

    async with _client(account_store, settings) as api:
        response = await api.get("/assets/app.js", headers={"accept-encoding": "gzip, deflate"})

    assert response.headers["content-encoding"] == "gzip"
    assert "Accept-Encoding" in response.headers["vary"]
    assert _ASSET_JS in response.text
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_text_assets_stay_plain_without_gzip_support(
    tmp_path: Path, account_store: AccountStore
) -> None:
    settings = Settings(static_dir=_write_dist(tmp_path))

    async with _client(account_store, settings) as api:
        response = await api.get("/assets/app.js", headers={"accept-encoding": "identity"})

    assert "content-encoding" not in response.headers
    assert _ASSET_JS in response.text


async def test_already_compressed_fonts_are_never_gzipped_again(
    tmp_path: Path, account_store: AccountStore
) -> None:
    settings = Settings(static_dir=_write_dist(tmp_path))

    async with _client(account_store, settings) as api:
        response = await api.get("/fonts/inter.woff2", headers={"accept-encoding": "gzip"})

    assert "content-encoding" not in response.headers
    assert response.content == _FONT_WOFF2
