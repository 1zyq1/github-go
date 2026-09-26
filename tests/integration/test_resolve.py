"""Integration tests for URL resolution."""

import httpx2
import pytest
from app.schemas import ResolveResponse
from app.url_policy import PageKind, ResourceKind
from pydantic import TypeAdapter

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    ("source_url", "expected_kind", "canonical_url"),
    [
        (
            "https://github.com/acme/widgets/releases/download/v1.0/widget.zip",
            ResourceKind.RELEASE,
            "https://github.com/acme/widgets/releases/download/v1.0/widget.zip",
        ),
        (
            "https://github.com/acme/widgets/archive/refs/heads/main",
            ResourceKind.ARCHIVE,
            "https://github.com/acme/widgets/archive/refs/heads/main",
        ),
        (
            "https://github.com/acme/widgets/blob/main/docs%20guide.md?raw=1",
            ResourceKind.RAW,
            "https://github.com/acme/widgets/raw/main/docs%20guide.md",
        ),
        (
            "https://raw.githubusercontent.com/acme/widgets/main/README.md",
            ResourceKind.RAW,
            "https://raw.githubusercontent.com/acme/widgets/main/README.md",
        ),
    ],
)
async def test_resolve_returns_same_origin_proxy_path(
    client: httpx2.AsyncClient,
    source_url: str,
    expected_kind: ResourceKind,
    canonical_url: str,
) -> None:
    response = await client.post(
        "/api/resolve",
        json={"url": source_url},
    )

    assert response.status_code == 200
    payload = TypeAdapter(ResolveResponse).validate_json(response.content)
    assert payload.kind == expected_kind
    assert payload.source_url == canonical_url
    assert payload.proxy_url.startswith("/proxy/")
    assert payload.proxy_url.startswith("/")
    assert "://" not in payload.proxy_url


async def test_resolve_rejects_extra_fields(
    client: httpx2.AsyncClient,
) -> None:
    response = await client.post(
        "/api/resolve",
        json={"url": "https://github.com/acme/widgets/raw/main/file.txt", "extra": 1},
    )

    assert response.status_code == 400
    assert b"invalid_request" in response.content
    assert b"extra" not in response.content


async def test_resolve_rejects_non_json_media_type(
    client: httpx2.AsyncClient,
) -> None:
    response = await client.post(
        "/api/resolve",
        content=b'{"url":"https://github.com/acme/widgets/raw/main/file.txt"}',
        headers={"content-type": "text/plain"},
    )

    assert response.status_code == 400
    assert b"invalid_request" in response.content


async def test_resolve_rejects_malformed_json(
    client: httpx2.AsyncClient,
) -> None:
    response = await client.post(
        "/api/resolve",
        content=b"{not-json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert b"invalid_request" in response.content


@pytest.mark.parametrize(
    ("source_url", "mirror_path"),
    [
        ("https://github.com/1zyq1", "/1zyq1"),
        ("https://github.com/acme", "/acme"),
        ("https://github.com/acme/widgets", "/acme/widgets"),
        ("https://github.com/acme/widgets?tab=readme-ov-file", "/acme/widgets?tab=readme-ov-file"),
        ("https://github.com/acme/widgets/tree/main/docs", "/acme/widgets/tree/main/docs"),
    ],
)
async def test_resolve_returns_mirror_path_for_page_urls(
    client: httpx2.AsyncClient,
    source_url: str,
    mirror_path: str,
) -> None:
    response = await client.post(
        "/api/resolve",
        json={"url": source_url},
    )

    assert response.status_code == 200
    payload = TypeAdapter(ResolveResponse).validate_json(response.content)
    assert payload.kind == PageKind.PAGE
    assert payload.source_url == f"https://github.com{mirror_path}"
    assert payload.proxy_url == mirror_path
    assert "://" not in payload.proxy_url


@pytest.mark.parametrize(
    "source_url",
    [
        "https://github.com/acme/widgets/archive/refs/heads/main.zip",
        "https://github.com/acme/widgets/releases/download/v1.0/widget.zip",
        "https://github.com/acme/widgets/raw/main/docs/readme.md",
    ],
)
async def test_resolve_prefers_file_kind_over_page_mirror(
    client: httpx2.AsyncClient,
    source_url: str,
) -> None:
    """A file-shaped path is also a legal mirror path, so files must win."""
    response = await client.post(
        "/api/resolve",
        json={"url": source_url},
    )

    assert response.status_code == 200
    payload = TypeAdapter(ResolveResponse).validate_json(response.content)
    assert payload.kind != PageKind.PAGE
    assert payload.proxy_url.startswith("/proxy/")


@pytest.mark.parametrize(
    "source_url",
    [
        "https://github.com/api/widgets",
        "https://github.com/proxy/widgets",
        "https://github.com/assets/widgets",
        "https://github.com/fonts/widgets",
        "https://gitlab.com/acme/widgets",
        "https://github.com/acme/../etc",
    ],
)
async def test_resolve_rejects_unmirrorable_urls(
    client: httpx2.AsyncClient,
    source_url: str,
) -> None:
    response = await client.post(
        "/api/resolve",
        json={"url": source_url},
    )

    assert response.status_code == 400
    assert b"invalid_request" not in response.content
    assert source_url.encode() not in response.content
