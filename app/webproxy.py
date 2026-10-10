"""Same-origin reverse proxy for browsing public github.com pages.

Only github.com itself is proxied. GitHub serves its assets from absolute URLs
on its own CDN hosts (github.githubassets.com and friends), so the browser
fetches those directly and this service never carries them.
"""

from collections.abc import AsyncIterator, Mapping
from urllib.parse import urljoin, urlsplit

import httpx2
from starlette.responses import RedirectResponse, Response, StreamingResponse

from app.compression import accepts_gzip, gzip_stream, note_accept_encoding_vary
from app.errors import AppError
from app.htmlmirror import MIRROR_BODY_LIMIT, is_rewritable_html, mirror_body, relay
from app.url_policy import encode_proxy_token, parse_source_url

_GITHUB_ORIGIN = "https://github.com"
_GITHUB_HOST = "github.com"
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_SAFE_REQUEST_HEADERS = ("accept", "accept-language", "if-none-match", "if-modified-since")
_SAFE_RESPONSE_HEADERS = frozenset(
    {
        "cache-control",
        "content-encoding",
        "content-language",
        "content-length",
        "content-type",
        "etag",
        "expires",
        "last-modified",
        "vary",
    }
)
_DOT_SEGMENTS = frozenset({"", ".", ".."})
_DELETE_CHARACTER = 0x7F
_SPACE_CHARACTER = 0x20
# GitHub's scripts assume they boot on github.com itself. Loaded from this
# origin they fail to hydrate and re-append the whole document in a loop, so a
# mirrored page is served as its server-rendered markup with every script
# stripped. "script-src 'none'" makes that a guarantee, not a promise.
_PAGE_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'none'; "
    "style-src 'self' 'unsafe-inline' https://github.githubassets.com; "
    "img-src 'self' data: https://github.githubassets.com "
    "https://avatars.githubusercontent.com https://user-images.githubusercontent.com "
    "https://github-cloud.s3.amazonaws.com; "
    "font-src 'self' data: https://github.githubassets.com; "
    "connect-src 'none'; "
    "form-action 'self' https://github.com; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'"
)


def _invalid_path() -> AppError:
    return AppError("invalid_request", "请求路径无效。", 400)


def _upstream_timeout() -> AppError:
    return AppError("upstream_timeout", "上游访问超时。", 504)


def _upstream_unavailable() -> AppError:
    return AppError("upstream_unavailable", "上游访问失败。", 502)


def _has_control(value: str) -> bool:
    return any(
        ord(character) < _SPACE_CHARACTER or ord(character) == _DELETE_CHARACTER
        for character in value
    )


def _validate_path(path: str) -> None:
    """Reject anything that could escape the github.com origin.

    An empty path is the origin root and stays valid.
    """
    if path.startswith("/") or "\\" in path or _has_control(path):
        raise _invalid_path()
    if path and any(segment in _DOT_SEGMENTS for segment in path.split("/")):
        raise _invalid_path()


def _browse_url(path: str, query: str) -> str:
    url = f"{_GITHUB_ORIGIN}/{path}"
    if query:
        return f"{url}?{query}"
    return url


def _file_target(path: str, query: str) -> str | None:
    """Return a canonical file URL when the path is a known download shape.

    Those are handed to the existing file proxy so they keep Range support and
    the archive cache instead of being streamed as a page.
    """
    try:
        return parse_source_url(_browse_url(path, query)).url
    except AppError:
        return None


def _forwarded_request_headers(headers: Mapping[str, str]) -> dict[str, str]:
    forwarded: dict[str, str] = {}
    for name in _SAFE_REQUEST_HEADERS:
        value = headers.get(name)
        if value is not None:
            forwarded[name] = value
    return forwarded


def _forwarded_response_headers(response: httpx2.Response) -> dict[str, str]:
    return {
        name: value
        for name, value in response.headers.items()
        if name.lower() in _SAFE_RESPONSE_HEADERS
    }


def _rewrite_location(location: str, base_url: str) -> str:
    """Keep same-origin navigation inside this service, leave foreign hosts alone."""
    try:
        parts = urlsplit(urljoin(base_url, location))
    except ValueError:
        return location
    if parts.scheme != "https" or parts.hostname != _GITHUB_HOST:
        return location
    target = parts.path or "/"
    if parts.query:
        return f"{target}?{parts.query}"
    return target


def _bodyless_response(status_code: int, headers: Mapping[str, str]) -> Response:
    """Create a response without Starlette's implicit zero content length."""
    response = Response(status_code=status_code, headers=dict(headers))
    if "content-length" in response.headers:
        del response.headers["content-length"]
    return response


class WebPageProxy:
    """Stream public github.com pages through the shared bounded client."""

    def __init__(self, client: httpx2.AsyncClient) -> None:
        """Store the pooled upstream client."""
        self._client: httpx2.AsyncClient = client

    async def handle(
        self,
        path: str,
        method: str,
        query: str,
        request_headers: Mapping[str, str],
    ) -> Response:
        """Return a page response, or hand file paths to the file proxy."""
        if method not in {"GET", "HEAD"}:
            code = "invalid_request"
            message = "请求方法无效。"
            raise AppError(code, message, 405)
        _validate_path(path)
        file_url = _file_target(path, query)
        if file_url is not None:
            return RedirectResponse(f"/proxy/{encode_proxy_token(file_url)}", status_code=302)
        return await self._fetch(_browse_url(path, query), method, request_headers)

    async def _fetch(
        self,
        url: str,
        method: str,
        request_headers: Mapping[str, str],
    ) -> Response:
        request = self._client.build_request(
            method,
            url,
            headers=_forwarded_request_headers(request_headers),
        )
        try:
            response = await self._client.send(request, stream=True)
        except httpx2.TimeoutException as error:
            raise _upstream_timeout() from error
        except httpx2.RequestError as error:
            raise _upstream_unavailable() from error
        return await self._browser_response(
            response,
            str(request.url),
            method,
            request_headers.get("accept-encoding"),
        )

    async def _browser_response(
        self,
        response: httpx2.Response,
        base_url: str,
        method: str,
        accept_encoding: str | None,
    ) -> Response:
        headers = _forwarded_response_headers(response)
        if response.status_code in _REDIRECT_STATUSES:
            location = response.headers.get("location")
            await response.aclose()
            if location is not None:
                headers["location"] = _rewrite_location(location, base_url)
            return _bodyless_response(response.status_code, headers)
        headers["content-security-policy"] = _PAGE_CONTENT_SECURITY_POLICY
        # A mirrored page is another site's content served from this origin, so
        # it is kept out of search results entirely.
        headers["x-robots-tag"] = "noindex, nofollow"
        if method == "HEAD":
            await response.aclose()
            return _bodyless_response(response.status_code, headers)
        if is_rewritable_html(headers):
            # Scripts are stripped, so the upstream length no longer applies.
            _ = headers.pop("content-length", None)
            body: AsyncIterator[bytes] = mirror_body(response, MIRROR_BODY_LIMIT)
            if accepts_gzip(accept_encoding):
                headers["content-encoding"] = "gzip"
                note_accept_encoding_vary(headers)
                body = gzip_stream(body)
            return StreamingResponse(
                body,
                status_code=response.status_code,
                headers=headers,
            )
        return StreamingResponse(
            relay(response),
            status_code=response.status_code,
            headers=headers,
        )


__all__ = ["WebPageProxy"]
