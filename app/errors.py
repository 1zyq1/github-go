"""Stable public error models with safe messages and headers."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TypedDict

from fastapi.responses import JSONResponse


class ErrorDetail(TypedDict):
    """Public error fields returned to clients."""

    code: str
    message: str


class ErrorEnvelope(TypedDict):
    """Top-level JSON error envelope."""

    error: ErrorDetail


def _empty_headers() -> Mapping[str, str]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class AppError(Exception):
    """Base exception for safe, client-facing application failures."""

    code: str
    message: str
    status_code: int
    headers: Mapping[str, str] = field(default_factory=_empty_headers)

    def __post_init__(self) -> None:
        """Freeze caller-provided headers and initialize exception text."""
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))
        Exception.__init__(self, self.message)


def error_envelope(error: AppError) -> ErrorEnvelope:
    """Build the stable JSON error envelope for an application error."""
    return {
        "error": {
            "code": error.code,
            "message": error.message,
        }
    }


def error_response(error: AppError, *, close_connection: bool = False) -> JSONResponse:
    """Render an application error without exposing internal details."""
    headers = dict(error.headers)
    if close_connection:
        headers["connection"] = "close"
    return JSONResponse(
        content=error_envelope(error),
        status_code=error.status_code,
        headers=headers,
    )
