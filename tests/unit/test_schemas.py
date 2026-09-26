"""Unit tests for request and response schemas."""

import pytest
from app.schemas import ResolveRequest, ResolveResponse
from app.url_policy import MAX_SOURCE_URL_LENGTH, ResourceKind
from pydantic import ValidationError


def test_resolve_request_trims_surrounding_whitespace() -> None:
    source_url = "https://github.com/owner/repo/raw/main/README.md"

    request = ResolveRequest.model_validate({"url": f"  {source_url}  "})

    assert request.url == source_url


def test_resolve_request_rejects_oversized_url() -> None:
    with pytest.raises(ValidationError):
        _ = ResolveRequest.model_validate({"url": "x" * (MAX_SOURCE_URL_LENGTH + 1)})


def test_resolve_request_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        _ = ResolveRequest.model_validate(
            {
                "url": "https://github.com/owner/repo/raw/main/file.txt",
                "unexpected": True,
            }
        )


def test_resolve_request_rejects_non_string_url() -> None:
    with pytest.raises(ValidationError):
        _ = ResolveRequest.model_validate({"url": 42})


def test_resolve_response_preserves_typed_kind() -> None:
    response = ResolveResponse(
        kind=ResourceKind.ARCHIVE,
        source_url="https://github.com/owner/repo/archive/refs/heads/main.zip",
        proxy_url="/proxy/token",
    )

    assert response.kind == ResourceKind.ARCHIVE
    assert response.source_url == "https://github.com/owner/repo/archive/refs/heads/main.zip"
    assert response.proxy_url == "/proxy/token"
