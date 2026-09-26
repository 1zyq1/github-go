"""Outbound gzip for mirrored GitHub pages.

The built single-page app is compressed by Starlette's ``GZipMiddleware``,
scoped to the static mounts so the binary download stream is never touched.
Mirrored pages need their own pass instead, because :mod:`app.htmlmirror`
rewrites the body inside a ``StreamingResponse`` on the main application.
"""

import zlib
from collections.abc import AsyncIterator, MutableMapping

_GZIP_LEVEL = 6
_GZIP_WBITS = zlib.MAX_WBITS | 16
_VARY_HEADER = "Accept-Encoding"


def accepts_gzip(accept_encoding: str | None) -> bool:
    """Report whether the client wants gzip, using Starlette's own rule."""
    return accept_encoding is not None and "gzip" in accept_encoding


async def gzip_stream(source: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    """Compress a byte stream as it arrives, framed as a gzip member."""
    compressor = zlib.compressobj(_GZIP_LEVEL, zlib.DEFLATED, _GZIP_WBITS)
    async for chunk in source:
        block = compressor.compress(chunk)
        if block:
            yield block
    tail = compressor.flush()
    if tail:
        yield tail


def note_accept_encoding_vary(headers: MutableMapping[str, str]) -> None:
    """Record that this representation now depends on ``Accept-Encoding``."""
    existing = headers.get("vary", "")
    names = [name.strip() for name in existing.split(",") if name.strip()]
    if _VARY_HEADER.lower() not in {name.lower() for name in names}:
        names.append(_VARY_HEADER)
    headers["vary"] = ", ".join(names)
