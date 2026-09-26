import base64
import re

import pytest
from app.errors import AppError
from app.url_policy import (
    RedirectHostRole,
    ResourceKind,
    decode_proxy_token,
    encode_proxy_token,
    parse_page_url,
    parse_source_url,
)

SUPPORTED_URLS = [
    pytest.param(
        "https://github.com/acme/widget/releases/download/v1.2.0/widget.zip",
        "https://github.com/acme/widget/releases/download/v1.2.0/widget.zip",
        ResourceKind.RELEASE,
        RedirectHostRole.RELEASE_ASSET,
        id="release",
    ),
    pytest.param(
        "HTTPS://GitHub.COM:443/acme/widget/archive/refs/heads/main.zip",
        "https://github.com/acme/widget/archive/refs/heads/main.zip",
        ResourceKind.ARCHIVE,
        RedirectHostRole.ARCHIVE,
        id="archive-head-canonicalization",
    ),
    pytest.param(
        "https://github.com/acme/widget/archive/refs/tags/v1.0.0.tar.gz",
        "https://github.com/acme/widget/archive/refs/tags/v1.0.0.tar.gz",
        ResourceKind.ARCHIVE,
        RedirectHostRole.ARCHIVE,
        id="archive-tag",
    ),
    pytest.param(
        "https://github.com/acme/widget/raw/feature/docs/readme.md",
        "https://github.com/acme/widget/raw/feature/docs/readme.md",
        ResourceKind.RAW,
        RedirectHostRole.RAW_CONTENT,
        id="github-raw",
    ),
    pytest.param(
        "https://github.com/acme/widget/blob/main/docs/readme.md?raw=1",
        "https://github.com/acme/widget/raw/main/docs/readme.md",
        ResourceKind.RAW,
        RedirectHostRole.RAW_CONTENT,
        id="blob-raw-query",
    ),
    pytest.param(
        "https://github.com/acme/widget/blob/main/docs/readme.md",
        "https://github.com/acme/widget/raw/main/docs/readme.md",
        ResourceKind.RAW,
        RedirectHostRole.RAW_CONTENT,
        id="blob-without-query",
    ),
    pytest.param(
        "https://raw.githubusercontent.com/acme/widget/main/docs/readme.md",
        "https://raw.githubusercontent.com/acme/widget/main/docs/readme.md",
        ResourceKind.RAW,
        RedirectHostRole.RAW_CONTENT,
        id="raw-content-host",
    ),
]

REJECTED_URLS = [
    pytest.param("http://github.com/acme/widget/raw/main/file.txt", "unsupported_url", id="http"),
    pytest.param("ftp://github.com/acme/widget/raw/main/file.txt", "unsupported_url", id="ftp"),
    pytest.param("//github.com/acme/widget/raw/main/file.txt", "invalid_url", id="scheme-relative"),
    pytest.param(
        "https://user@github.com/acme/widget/raw/main/file.txt", "unsupported_url", id="userinfo"
    ),
    pytest.param(
        "https://user:pass@github.com/acme/widget/raw/main/file.txt",
        "unsupported_url",
        id="password",
    ),
    pytest.param(
        "https://github.com:444/acme/widget/raw/main/file.txt", "unsupported_url", id="non-443-port"
    ),
    pytest.param("https://127.0.0.1/file.txt", "unsupported_url", id="ipv4"),
    pytest.param("https://[::1]/file.txt", "unsupported_url", id="ipv6"),
    pytest.param("https://2130706433/file.txt", "unsupported_url", id="integer-host"),
    pytest.param(
        "https://github.com.evil.example/acme/widget/raw/main/file.txt",
        "unsupported_url",
        id="suffix-confusion",
    ),
    pytest.param(
        "https://raw.githubusercontent.com.evil.example/a/b/main/file.txt",
        "unsupported_url",
        id="raw-suffix-confusion",
    ),
    pytest.param(
        "https://github.com。evil.example/acme/widget/raw/main/file.txt",
        "unsupported_url",
        id="unicode-host-confusion",
    ),
    pytest.param(
        "https://github.com/acme/widget/../widget/raw/main/file.txt",
        "invalid_url",
        id="dot-segment",
    ),
    pytest.param(
        "https://github.com/acme/widget/%2e%2e/widget/raw/main/file.txt",
        "invalid_url",
        id="encoded-dot-segment",
    ),
    pytest.param(
        "https://github.com/acme/widget/raw/main%2Ffile.txt", "invalid_url", id="encoded-slash"
    ),
    pytest.param(
        "https://github.com/acme/widget/raw/main%5Cfile.txt", "invalid_url", id="encoded-backslash"
    ),
    pytest.param(
        "https://github.com/acme/widget/raw//main/file.txt", "invalid_url", id="empty-path-segment"
    ),
    pytest.param(
        "https://github.com/acme/widget/raw/main/%GGfile.txt",
        "invalid_url",
        id="malformed-encoding",
    ),
    pytest.param("https://github.com/acme/widget/raw/main/file\x00.txt", "invalid_url", id="nul"),
    pytest.param(
        "https://github.com/acme/widget/raw/main/file\r.txt", "invalid_url", id="carriage-return"
    ),
    pytest.param(
        "https://github.com/acme/widget/raw/main/file\n.txt", "invalid_url", id="line-feed"
    ),
    pytest.param(
        "https://github.com/acme/widget/raw/main/file.txt#fragment", "invalid_url", id="fragment"
    ),
    pytest.param(
        "https://github.com/acme/widget/releases/download/v1/file.zip?download=1",
        "invalid_url",
        id="release-query",
    ),
    pytest.param(
        "https://github.com/acme/widget/blob/main/file.txt?raw=0",
        "invalid_url",
        id="wrong-blob-query",
    ),
    pytest.param(
        "https://github.com/acme/widget/blob/main/file.txt?raw=1&x=1",
        "invalid_url",
        id="extra-blob-query",
    ),
    pytest.param(
        "https://github.com/acme/widget/raw/main", "unsupported_url", id="missing-resource"
    ),
    pytest.param(
        "https://github.com/acme/widget/tree/main/file.txt",
        "unsupported_url",
        id="unsupported-route",
    ),
    pytest.param(
        "https://github.com/acme/widget/archive/refs/pull/1/head.zip",
        "unsupported_url",
        id="unsupported-archive-ref",
    ),
    pytest.param(
        "https://raw.githubusercontent.com/acme/widget/main",
        "unsupported_url",
        id="missing-raw-resource",
    ),
]


@pytest.mark.parametrize(("raw_url", "canonical_url", "kind", "role"), SUPPORTED_URLS)
def test_parse_source_url_supports_github_download_classes(
    raw_url: str,
    canonical_url: str,
    kind: ResourceKind,
    role: RedirectHostRole,
) -> None:
    target = parse_source_url(raw_url)

    assert target.url == canonical_url
    assert target.kind == kind
    assert target.redirect_host_role == role


@pytest.mark.parametrize(("raw_url", "expected_code"), REJECTED_URLS)
def test_parse_source_url_rejects_unsupported_or_unsafe_urls(
    raw_url: str,
    expected_code: str,
) -> None:
    with pytest.raises(AppError) as captured:
        _ = parse_source_url(raw_url)

    assert captured.value.code == expected_code
    assert raw_url not in captured.value.message


SUPPORTED_PAGE_URLS = [
    pytest.param("https://github.com/1zyq1", "/1zyq1", id="profile"),
    pytest.param("https://github.com/acme", "/acme", id="single-segment-owner"),
    pytest.param("https://github.com/acme/widgets", "/acme/widgets", id="repository"),
    pytest.param(
        "https://github.com/acme/widgets/tree/main/docs",
        "/acme/widgets/tree/main/docs",
        id="nested-path",
    ),
    pytest.param(
        "HTTPS://GitHub.COM:443/acme/widgets",
        "/acme/widgets",
        id="canonicalization",
    ),
    pytest.param(
        "https://github.com/acme/widgets?tab=readme-ov-file",
        "/acme/widgets?tab=readme-ov-file",
        id="query-preserved",
    ),
    pytest.param(
        "https://github.com/acme/widgets/pulls?q=is%3Apr+is%3Aopen",
        "/acme/widgets/pulls?q=is%3Apr+is%3Aopen",
        id="query-with-encoded-spaces",
    ),
    pytest.param(
        "https://github.com/acme/wid%20gets/tree/main/docs",
        "/acme/wid%20gets/tree/main/docs",
        id="encoded-space-in-segment",
    ),
]

REJECTED_PAGE_URLS = [
    pytest.param("https://github.com/api/widgets", "unsupported_url", id="reserved-api"),
    pytest.param("https://github.com/proxy/widgets", "unsupported_url", id="reserved-proxy"),
    pytest.param("https://github.com/assets/widgets", "unsupported_url", id="reserved-assets"),
    pytest.param("https://github.com/fonts/widgets", "unsupported_url", id="reserved-fonts"),
    pytest.param("https://github.com/stats/widgets", "unsupported_url", id="reserved-stats"),
    pytest.param("https://github.com/api", "unsupported_url", id="reserved-api-profile"),
    pytest.param("https://github.com", "invalid_url", id="no-path"),
    pytest.param("https://github.com/", "invalid_url", id="root-only"),
    pytest.param("https://github.com/acme/widgets/", "invalid_url", id="trailing-slash"),
    pytest.param("https://github.com/acme/../etc", "invalid_url", id="dot-segment"),
    pytest.param("https://github.com/acme/%2e%2e/etc", "invalid_url", id="encoded-dot-segment"),
    pytest.param("https://github.com/acme/wid%2Fgets", "invalid_url", id="encoded-slash"),
    pytest.param("https://github.com/acme/wid%5Cgets", "invalid_url", id="encoded-backslash"),
    pytest.param("https://github.com/acme//widgets", "invalid_url", id="empty-path-segment"),
    pytest.param("https://github.com/acme/widgets#readme", "invalid_url", id="fragment"),
    pytest.param("https://github.com/acme/widgets\r", "invalid_url", id="carriage-return"),
    pytest.param("http://github.com/acme/widgets", "unsupported_url", id="http"),
    pytest.param("https://gitlab.com/acme/widgets", "unsupported_url", id="foreign-host"),
    pytest.param(
        "https://raw.githubusercontent.com/acme/widget/main/readme.md",
        "unsupported_url",
        id="raw-host-is-not-a-page",
    ),
    pytest.param(
        "https://github.com.evil.example/acme/widgets",
        "unsupported_url",
        id="suffix-confusion",
    ),
    pytest.param("https://127.0.0.1/acme/widgets", "unsupported_url", id="ipv4"),
    pytest.param("https://user:pw@github.com/acme/widgets", "unsupported_url", id="userinfo"),
    pytest.param("https://github.com:444/acme/widgets", "unsupported_url", id="non-443-port"),
    pytest.param("https://github.com/-acme/widgets", "invalid_url", id="owner-leading-hyphen"),
    pytest.param("https://github.com/acme-/widgets", "invalid_url", id="owner-trailing-hyphen"),
    pytest.param("https://github.com/acme_/widgets", "invalid_url", id="owner-underscore"),
    pytest.param("https://github.com/favicon.svg", "invalid_url", id="owner-with-dot"),
]


@pytest.mark.parametrize(("raw_url", "mirror_path"), SUPPORTED_PAGE_URLS)
def test_parse_page_url_returns_same_origin_mirror_path(raw_url: str, mirror_path: str) -> None:
    target = parse_page_url(raw_url)

    assert target.path == mirror_path
    assert target.path.startswith("/")
    assert "://" not in target.path
    assert not target.path.startswith(("/api", "/proxy", "/assets", "/fonts", "/stats"))


@pytest.mark.parametrize(("raw_url", "expected_code"), REJECTED_PAGE_URLS)
def test_parse_page_url_rejects_unmirrorable_or_unsafe_urls(
    raw_url: str,
    expected_code: str,
) -> None:
    with pytest.raises(AppError) as captured:
        _ = parse_page_url(raw_url)

    assert captured.value.code == expected_code
    assert raw_url not in captured.value.message


def test_proxy_token_round_trip_is_unpadded_and_url_safe() -> None:
    source = parse_source_url("https://github.com/acme/widget/releases/download/v1/widget.zip")

    token = encode_proxy_token(source.url)

    assert re.fullmatch(r"[A-Za-z0-9_-]+", token)
    assert "=" not in token
    assert decode_proxy_token(token) == source


@pytest.mark.parametrize(
    "token",
    [
        "",
        "not+base64url",
        "YWJj=",
        "A" * 6000,
    ],
)
def test_decode_proxy_token_rejects_malformed_or_oversized_tokens(token: str) -> None:
    with pytest.raises(AppError) as captured:
        _ = decode_proxy_token(token)

    assert captured.value.code == "invalid_proxy_token"


def test_decode_proxy_token_rejects_invalid_utf8() -> None:
    token = base64.urlsafe_b64encode(b"\xff").rstrip(b"=").decode("ascii")

    with pytest.raises(AppError) as captured:
        _ = decode_proxy_token(token)

    assert captured.value.code == "invalid_proxy_token"


def test_decode_proxy_token_rejects_noncanonical_encoding() -> None:
    noncanonical_url = "HTTPS://GitHub.COM/acme/widget/raw/main/file.txt"
    token = base64.urlsafe_b64encode(noncanonical_url.encode()).rstrip(b"=").decode("ascii")

    with pytest.raises(AppError) as captured:
        _ = decode_proxy_token(token)

    assert captured.value.code == "invalid_proxy_token"


def test_encode_proxy_token_rejects_unsafe_source() -> None:
    with pytest.raises(AppError) as captured:
        _ = encode_proxy_token("https://example.com/file.txt")

    assert captured.value.code == "unsupported_url"
