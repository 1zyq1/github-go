"""Focused security boundary tests through public interfaces."""

import httpx2
import pytest
from app.errors import AppError
from app.upstream import open_upstream
from app.url_policy import parse_source_url


def test_url_policy_rejects_boundary_paths_and_hosts() -> None:
    invalid_urls = (
        "https://github.com/acme/widgets/raw/%ZZ/file",
        "https://github.com/acme/widgets/raw/%252525252525252525252525252f/file",
        "https://github.com/acme/widgets/raw/main/file/",
        "https://github.com/acme",
        "https://github.com/acme!/widgets/raw/main/file",
        "https://github.com/acme/widgets!/raw/main/file",
        "https://github.com:bad/acme/widgets/raw/main/file",
        "https:///acme/widgets/raw/main/file",
        "",
        "x" * 4097,
        "https://[",
    )
    for url in invalid_urls:
        with pytest.raises(AppError):
            _ = parse_source_url(url)


@pytest.mark.asyncio
async def test_open_upstream_rejects_unsupported_methods() -> None:
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _request: httpx2.Response(200))
    )
    target = parse_source_url("https://raw.githubusercontent.com/acme/widgets/main/file.txt")

    try:
        with pytest.raises(AppError) as error:
            _ = await open_upstream(client, target, method="POST")
        assert error.value.status_code == 405
    finally:
        await client.aclose()
