"""Strict GitHub download URL classification and token encoding."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import SplitResult, quote, unquote_to_bytes, urlsplit, urlunsplit

from app.errors import AppError

MAX_SOURCE_URL_LENGTH = 4096
MAX_PROXY_TOKEN_LENGTH = (MAX_SOURCE_URL_LENGTH * 4 + 2) // 3
_CONTROL_CHARACTER_LIMIT = 0x20
_DELETE_CHARACTER = 0x7F
_MAX_DECODE_PASSES = 4
_MIN_OWNER_REPOSITORY_SEGMENTS = 2
_MIN_RELEASE_ROUTE_SEGMENTS = 4
_MIN_ARCHIVE_ROUTE_SEGMENTS = 4
_MIN_RAW_ROUTE_SEGMENTS = 3
_MIN_RAW_HOST_SEGMENTS = 4
_ALLOWED_SOURCE_HOSTS = frozenset({"github.com", "raw.githubusercontent.com"})
# Mirrored pages are routed by path, so a page whose first segment matches a
# route this application registers at its own root would either be shadowed by
# us or shadow us: ``/api/*`` and ``/proxy/*`` are the reserved prefixes,
# ``/assets/*`` plus ``/fonts/*`` are the built frontend's static mounts, and
# ``/stats`` is the data page's entry point. The canonical list lives in
# app.main; a mirror path is rejected unless its owner segment is provably not
# one of them.
_PAGE_OWNED_ROUTE_PREFIXES = frozenset({"api", "proxy", "assets", "fonts", "stats"})
_OWNER_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,100}\Z")
_MALFORMED_ESCAPE_PATTERN = re.compile(r"%(?![0-9A-Fa-f]{2})")
_SEPARATOR_ESCAPE_PATTERN = re.compile(r"%(?:2f|5c)", re.IGNORECASE)
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")
_PATH_SAFE = "!$&'()*+,;=:@~-._"


class ResourceKind(StrEnum):
    """Supported GitHub file resource classes."""

    RELEASE = "release"
    ARCHIVE = "archive"
    RAW = "raw"


class PageKind(StrEnum):
    """Supported GitHub HTML page classes.

    Deliberately separate from :class:`ResourceKind`: file kinds carry a
    :class:`RedirectHostRole` and are encoded into ``/proxy/{token}`` paths,
    while a page is mirrored in place under its own public path.
    """

    PAGE = "page"


class RedirectHostRole(StrEnum):
    """Resource-specific allowlists for redirect destinations."""

    RELEASE_ASSET = "release_asset"
    ARCHIVE = "archive"
    RAW_CONTENT = "raw_content"


@dataclass(frozen=True, slots=True)
class SourceTarget:
    """Canonical source URL and its validated routing policy."""

    url: str
    kind: ResourceKind
    redirect_host_role: RedirectHostRole


@dataclass(frozen=True, slots=True)
class PageTarget:
    """Canonical page URL and the same-origin path that mirrors it."""

    url: str
    path: str


def _invalid_url() -> AppError:
    return AppError("invalid_url", "下载链接格式无效。", 400)


def _unsupported_url() -> AppError:
    return AppError("unsupported_url", "暂不支持该下载链接。", 400)


def _unsupported_page() -> AppError:
    return AppError("unsupported_url", "暂不支持镜像该页面地址。", 400)


def _invalid_proxy_token() -> AppError:
    return AppError("invalid_proxy_token", "下载代理链接无效。", 400)


def _has_control(value: str) -> bool:
    return any(
        ord(character) < _CONTROL_CHARACTER_LIMIT or ord(character) == _DELETE_CHARACTER
        for character in value
    )


def _decode_path(path: str) -> str:
    current = path
    for _ in range(_MAX_DECODE_PASSES):
        if _MALFORMED_ESCAPE_PATTERN.search(current) or _SEPARATOR_ESCAPE_PATTERN.search(current):
            raise _invalid_url()
        try:
            decoded = unquote_to_bytes(current).decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise _invalid_url() from error
        if _has_control(decoded) or "\\" in decoded or "//" in decoded:
            raise _invalid_url()
        if decoded == current:
            return decoded
        current = decoded
    raise _invalid_url()


def _validated_segments(path: str) -> list[str]:
    if not path.startswith("/") or path.endswith("/"):
        raise _invalid_url()
    decoded = _decode_path(path)
    segments = decoded[1:].split("/")
    if any(segment in {".", ".."} for segment in segments):
        raise _invalid_url()
    return segments


def _validate_owner_repository(segments: list[str]) -> None:
    if len(segments) < _MIN_OWNER_REPOSITORY_SEGMENTS:
        raise _invalid_url()
    if _OWNER_PATTERN.fullmatch(segments[0]) is None:
        raise _invalid_url()
    if _REPOSITORY_PATTERN.fullmatch(segments[1]) is None:
        raise _invalid_url()


def _validate_page_owner(segments: list[str]) -> None:
    """Accept any public GitHub path, including the one-segment profile page.

    Unlike a file target, a page needs no repository segment: ``/1zyq1`` is a
    user profile and ``/acme/widgets`` is a repository, and the mirror serves
    both. Only the leading owner segment is constrained, by the same charset
    GitHub itself allows for logins.
    """
    if _OWNER_PATTERN.fullmatch(segments[0]) is None:
        raise _invalid_url()


def _classify_github_path(segments: list[str]) -> tuple[ResourceKind, RedirectHostRole]:
    _validate_owner_repository(segments)
    route = segments[2:]
    if (
        len(route) == _MIN_RELEASE_ROUTE_SEGMENTS
        and route[0:2] == ["releases", "download"]
        and all(route[2:])
    ):
        return ResourceKind.RELEASE, RedirectHostRole.RELEASE_ASSET
    if (
        len(route) >= _MIN_ARCHIVE_ROUTE_SEGMENTS
        and route[0:2] == ["archive", "refs"]
        and route[2] in {"heads", "tags"}
    ):
        return ResourceKind.ARCHIVE, RedirectHostRole.ARCHIVE
    if len(route) >= _MIN_RAW_ROUTE_SEGMENTS and route[0] == "raw":
        return ResourceKind.RAW, RedirectHostRole.RAW_CONTENT
    if len(route) >= _MIN_RAW_ROUTE_SEGMENTS and route[0] == "blob":
        segments[2] = "raw"
        return ResourceKind.RAW, RedirectHostRole.RAW_CONTENT
    raise _unsupported_url()


def _classify_raw_host(segments: list[str]) -> tuple[ResourceKind, RedirectHostRole]:
    _validate_owner_repository(segments)
    if len(segments) < _MIN_RAW_HOST_SEGMENTS:
        raise _unsupported_url()
    return ResourceKind.RAW, RedirectHostRole.RAW_CONTENT


def _is_ip_literal(host: str) -> bool:
    try:
        _ = ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _source_host(parts: SplitResult) -> str:
    if not parts.scheme:
        raise _invalid_url()
    if parts.scheme.lower() != "https":
        raise _unsupported_url()
    if parts.username is not None or parts.password is not None:
        raise _unsupported_url()
    try:
        port = parts.port
    except ValueError as error:
        raise _unsupported_url() from error
    if port not in {None, 443}:
        raise _unsupported_url()
    if not parts.netloc or not parts.hostname:
        raise _invalid_url()
    authority = parts.netloc.rsplit("@", 1)[-1]
    if ":" in authority and (not authority.rsplit(":", 1)[1].isdigit()):
        raise _unsupported_url()
    host = parts.hostname.lower()
    if _is_ip_literal(host) or host.isdigit() or host not in _ALLOWED_SOURCE_HOSTS:
        raise _unsupported_url()
    return host


def _validate_query(parts: SplitResult, host: str) -> None:
    if not parts.query:
        return
    if host == "github.com" and "/blob/" in parts.path and parts.query == "raw=1":
        return
    raise _invalid_url()


def _canonical_path(segments: list[str]) -> str:
    encoded = (quote(segment, safe=_PATH_SAFE) for segment in segments)
    return "/" + "/".join(encoded)


def parse_source_url(value: str) -> SourceTarget:
    """Validate and canonicalize one supported user-supplied GitHub URL."""
    if not 1 <= len(value) <= MAX_SOURCE_URL_LENGTH:
        raise _invalid_url()
    if (
        "\\" in value
        or "#" in value
        or _has_control(value)
        or any(char.isspace() for char in value)
    ):
        raise _invalid_url()
    try:
        parts = urlsplit(value)
    except ValueError as error:
        raise _unsupported_url() from error
    host = _source_host(parts)
    _validate_query(parts, host)
    segments = _validated_segments(parts.path)
    if host == "github.com":
        kind, role = _classify_github_path(segments)
    else:
        kind, role = _classify_raw_host(segments)
    canonical_url = urlunsplit(("https", host, _canonical_path(segments), "", ""))
    return SourceTarget(canonical_url, kind, role)


def parse_page_url(value: str) -> PageTarget:
    """Validate one public GitHub page URL and return its same-origin path.

    Mirrored pages are routed by their public path, so the mirror path is the
    canonical path itself. Everything that makes a URL unsafe as a *file*
    target is unsafe here too: scheme, host, userinfo, port, IP literals,
    control characters, dot segments and separator escapes are all rejected by
    the same helpers.

    The query string is carried over verbatim, because the mirror already
    forwards it and dropping it would silently reset filters such as ``?tab=``.
    It is deliberately not passed through :func:`_validate_query`, which
    exists to strip queries from canonicalized *file* tokens.
    """
    if not 1 <= len(value) <= MAX_SOURCE_URL_LENGTH:
        raise _invalid_url()
    if (
        "\\" in value
        or "#" in value
        or _has_control(value)
        or any(char.isspace() for char in value)
    ):
        raise _invalid_url()
    try:
        parts = urlsplit(value)
    except ValueError as error:
        raise _unsupported_url() from error
    if _source_host(parts) != "github.com":
        raise _unsupported_url()
    segments = _validated_segments(parts.path)
    _validate_page_owner(segments)
    if segments[0] in _PAGE_OWNED_ROUTE_PREFIXES:
        raise _unsupported_page()
    path = _canonical_path(segments)
    # urlunsplit takes (scheme, netloc, path, query, fragment): the query must
    # land in the fourth slot or it is emitted as a fragment separator.
    return PageTarget(
        urlunsplit(("https", "github.com", path, parts.query, "")),
        f"{path}?{parts.query}" if parts.query else path,
    )


def encode_proxy_token(canonical_url: str) -> str:
    """Encode a validated source URL as unpadded canonical base64url."""
    target = parse_source_url(canonical_url)
    encoded = base64.urlsafe_b64encode(target.url.encode("utf-8")).rstrip(b"=")
    return encoded.decode("ascii")


def decode_proxy_token(token: str) -> SourceTarget:
    """Decode, revalidate, and reject noncanonical proxy tokens."""
    if not token or len(token) > MAX_PROXY_TOKEN_LENGTH or _TOKEN_PATTERN.fullmatch(token) is None:
        raise _invalid_proxy_token()
    if len(token) % 4 == 1:
        raise _invalid_proxy_token()
    try:
        padded = token + ("=" * (-len(token) % 4))
        decoded = base64.b64decode(
            padded,
            altchars=b"-_",
            validate=True,
        ).decode("utf-8", errors="strict")
        target = parse_source_url(decoded)
    except (AppError, binascii.Error, UnicodeDecodeError) as error:
        raise _invalid_proxy_token() from error
    if encode_proxy_token(target.url) != token:
        raise _invalid_proxy_token()
    return target
