"""Unit tests for the pure ASGI request-size boundary."""

from dataclasses import dataclass
from typing import TypedDict, cast

import pytest
from app.errors import ErrorEnvelope
from app.middleware import MAX_REQUEST_BODY_BYTES, RequestSizeLimitMiddleware
from pydantic import TypeAdapter
from starlette.types import Message, Receive, Scope, Send

pytestmark = pytest.mark.asyncio


@dataclass(frozen=True, slots=True)
class ASGIResult:
    status_code: int
    headers: list[tuple[bytes, bytes]]
    body: bytes
    receive_count: int


class _ResponseStart(TypedDict):
    type: str
    status: int
    headers: list[tuple[bytes, bytes]]


async def _consuming_app(_scope: Scope, receive: Receive, send: Send) -> None:
    body_parts = bytearray()
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return
        body_value = cast("object", message.get("body", b""))
        if not isinstance(body_value, bytes):
            error_message = "expected an ASGI bytes body"
            raise TypeError(error_message)
        body = body_value
        body_parts.extend(body)
        if not message.get("more_body", False):
            break
    await send(
        {
            "type": "http.response.start",
            "status": 204,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": bytes(body_parts)})


async def _call_middleware(
    *,
    path: str,
    chunks: tuple[bytes, ...],
    headers: object,
) -> ASGIResult:
    receive_count = 0
    sent: list[Message] = []
    messages: list[Message] = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]

    async def receive() -> Message:
        nonlocal receive_count
        if receive_count >= len(messages):
            return {"type": "http.disconnect"}
        message = messages[receive_count]
        receive_count += 1
        return message

    async def send(message: Message) -> None:
        sent.append(message)

    scope = cast(
        "Scope",
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
        },
    )
    await RequestSizeLimitMiddleware(_consuming_app)(scope, receive, send)
    start = cast(
        "_ResponseStart",
        cast(
            "object",
            next(item for item in sent if item["type"] == "http.response.start"),
        ),
    )
    body = b"".join(
        cast("bytes", item.get("body", b""))
        for item in sent
        if item["type"] == "http.response.body"
    )
    return ASGIResult(start["status"], start["headers"], body, receive_count)


def _error_code(body: bytes) -> str:
    return TypeAdapter(ErrorEnvelope).validate_json(body)["error"]["code"]


async def test_rejects_declared_oversize_before_receiving_body() -> None:
    result = await _call_middleware(
        path="/api/resolve",
        chunks=(b"x" * (MAX_REQUEST_BODY_BYTES + 1),),
        headers=[(b"content-length", str(MAX_REQUEST_BODY_BYTES + 1).encode("ascii"))],
    )

    assert result.status_code == 413
    assert result.receive_count == 0
    assert (b"connection", b"close") in result.headers
    assert _error_code(result.body) == "request_too_large"


async def test_rejects_actual_chunked_oversize_without_content_length() -> None:
    result = await _call_middleware(
        path="/api/resolve",
        chunks=(b"x" * 4096, b"x" * (MAX_REQUEST_BODY_BYTES - 4096 + 1)),
        headers=[],
    )

    assert result.status_code == 413
    assert result.receive_count == 2
    assert _error_code(result.body) == "request_too_large"


@pytest.mark.parametrize(
    "headers",
    [
        b"content-length: 1",
        [("content-length",)],
        [("content-length", "1")],
        [("content-length", b"1", b"extra")],
        [("content-length", b"1"), ("content-length", b"2")],
        [("content-length", b"abc")],
        [("content-length", b"9" * 5000)],
    ],
)
async def test_rejects_malformed_or_ambiguous_content_length(headers: object) -> None:
    result = await _call_middleware(
        path="/api/resolve",
        chunks=(b"{}",),
        headers=headers,
    )

    assert result.status_code == 400
    assert result.receive_count == 0
    assert _error_code(result.body) == "invalid_request"


async def test_accepts_exact_limit() -> None:
    body = b"x" * MAX_REQUEST_BODY_BYTES
    result = await _call_middleware(
        path="/api/resolve",
        chunks=(body,),
        headers=[(b"content-length", str(MAX_REQUEST_BODY_BYTES).encode("ascii"))],
    )

    assert result.status_code == 204
    assert result.body == body


async def test_does_not_limit_other_paths() -> None:
    result = await _call_middleware(
        path="/api/health",
        chunks=(b"x" * (MAX_REQUEST_BODY_BYTES + 1),),
        headers=[],
    )

    assert result.status_code == 204
    assert result.receive_count == 1
