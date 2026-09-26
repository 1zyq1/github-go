"""Reject oversized API request bodies at the ASGI receive boundary."""

from typing import cast

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.errors import AppError, error_response

MAX_REQUEST_BODY_BYTES = 8192
_HEADER_PAIR_LENGTH = 2
# Every POST that carries credentials or a URL is bounded, so an oversized body
# is rejected before the application reads it into memory.
_LIMITED_PATHS = frozenset({"/api/resolve", "/api/auth/register", "/api/auth/login"})
_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "font-src 'self'; "
    "img-src 'self'; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "base-uri 'none'; "
    "object-src 'none'; "
    "frame-ancestors 'none'"
)
_SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("content-security-policy", _CONTENT_SECURITY_POLICY),
    ("x-content-type-options", "nosniff"),
    ("referrer-policy", "no-referrer"),
    ("permissions-policy", "camera=(), microphone=(), geolocation=()"),
    ("x-frame-options", "DENY"),
)


class _BodyTooLargeError(Exception):
    """Signal that the receive boundary exceeded its byte budget."""


class _InvalidContentLengthError(Exception):
    """Signal a malformed or ambiguous declared content length."""


def _scope_value(scope: Scope, key: str) -> object:
    return cast("object", scope.get(key))


def _declared_length(headers: object) -> int | None:
    if not isinstance(headers, list):
        raise _InvalidContentLengthError
    values: list[bytes] = []
    typed_headers = cast("list[object]", headers)
    for item in typed_headers:
        if not isinstance(item, tuple):
            raise _InvalidContentLengthError
        name_value = cast("tuple[object, ...]", item)
        if len(name_value) != _HEADER_PAIR_LENGTH:
            raise _InvalidContentLengthError
        name, value = name_value
        if not isinstance(name, bytes) or not isinstance(value, bytes):
            raise _InvalidContentLengthError
        if name.lower() == b"content-length":
            values.append(value)
    if not values:
        return None
    if len(values) != 1 or not values[0].isdigit():
        raise _InvalidContentLengthError
    try:
        return int(values[0])
    except ValueError as error:
        raise _InvalidContentLengthError from error


async def _send_error(
    scope: Scope,
    receive: Receive,
    send: Send,
    error: AppError,
) -> None:
    response = error_response(error, close_connection=True)
    await response(scope, receive, send)


class SecurityHeadersMiddleware:
    """Attach a fixed same-origin policy to every HTTP response."""

    def __init__(self, app: ASGIApp) -> None:
        """Store the downstream ASGI application."""
        self.app: ASGIApp = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Append security headers that the response did not already set."""
        if _scope_value(scope, "type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _SECURITY_HEADERS:
                    if name not in headers:
                        headers.append(name, value)
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


class RequestSizeLimitMiddleware:
    """Limit credential and resolve endpoints to a small, counted body."""

    def __init__(self, app: ASGIApp) -> None:
        """Store the downstream ASGI application."""
        self.app: ASGIApp = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Count request chunks before passing them to the application."""
        scope_type = _scope_value(scope, "type")
        path = _scope_value(scope, "path")
        method = _scope_value(scope, "method")
        if (
            not isinstance(scope_type, str)
            or scope_type != "http"
            or not isinstance(path, str)
            or path not in _LIMITED_PATHS
            or not isinstance(method, str)
            or method.upper() != "POST"
        ):
            await self.app(scope, receive, send)
            return
        try:
            declared_length = _declared_length(_scope_value(scope, "headers"))
        except _InvalidContentLengthError:
            await _send_error(
                scope,
                receive,
                send,
                AppError("invalid_request", "请求格式无效。", 400),
            )
            return
        if declared_length is not None and declared_length > MAX_REQUEST_BODY_BYTES:
            await _send_error(
                scope,
                receive,
                send,
                AppError("request_too_large", "请求内容过大。", 413),
            )
            return
        received_bytes = 0

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                body = cast("bytes", message.get("body", b""))
                received_bytes += len(body)
                if received_bytes > MAX_REQUEST_BODY_BYTES:
                    raise _BodyTooLargeError
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLargeError:
            await _send_error(
                scope,
                receive,
                send,
                AppError("request_too_large", "请求内容过大。", 413),
            )
