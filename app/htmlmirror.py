"""Prepare a mirrored upstream body so it can be served from this origin."""

import re
from collections.abc import AsyncIterator, Mapping

import httpx2

MIRROR_BODY_LIMIT = 8 * 1024 * 1024

_HTML_CONTENT_TYPE = "text/html"
_IDENTITY_ENCODINGS = frozenset({"", "identity"})
_SCRIPT_PATTERN = re.compile(rb"<script\b[^>]*>.*?</script\s*>", re.DOTALL | re.IGNORECASE)
_SCRIPT_VOID_PATTERN = re.compile(rb"<script\b[^>]*?/\s*>", re.IGNORECASE)
_MODULEPRELOAD_PATTERN = re.compile(
    rb"<link\b[^>]*\brel=['\"]?modulepreload['\"]?[^>]*>",
    re.IGNORECASE,
)


def strip_scripts(markup: bytes) -> bytes:
    """Return the server-rendered markup with every script reference removed.

    GitHub loads its page code as `crossorigin` modules. On a foreign origin
    those scripts run, fail to hydrate the server-rendered DOM, and append the
    whole document again, so the mirrored page must not keep them.
    """
    without_elements = _SCRIPT_PATTERN.sub(b"", markup)
    without_elements = _SCRIPT_VOID_PATTERN.sub(b"", without_elements)
    return _MODULEPRELOAD_PATTERN.sub(b"", without_elements)


def is_rewritable_html(headers: Mapping[str, str]) -> bool:
    """Report whether the body may be rewritten as plain HTML text."""
    content_type = headers.get("content-type", "")
    if not content_type.lower().startswith(_HTML_CONTENT_TYPE):
        return False
    return headers.get("content-encoding", "").lower().strip() in _IDENTITY_ENCODINGS


async def relay(response: httpx2.Response) -> AsyncIterator[bytes]:
    """Stream a response body unchanged, then close the upstream response."""
    try:
        async for chunk in response.aiter_bytes():
            yield chunk
    finally:
        await response.aclose()


async def mirror_body(response: httpx2.Response, limit: int) -> AsyncIterator[bytes]:
    """Strip scripts from a small body, or stream anything larger untouched.

    The limit keeps a hostile or surprising upstream from being buffered in
    memory; oversize bodies fall back to plain streaming.
    """
    held: list[bytes] = []
    total = 0
    oversized = False
    try:
        async for chunk in response.aiter_bytes():
            if oversized:
                yield chunk
                continue
            held.append(chunk)
            total += len(chunk)
            if total > limit:
                oversized = True
                for buffered in held:
                    yield buffered
                held = []
        if not oversized:
            yield strip_scripts(b"".join(held))
    finally:
        await response.aclose()
