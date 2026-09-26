"""Session-cookie authentication and per-user access quota enforcement.

The middleware authenticates every request, rejects the unauthenticated, and
spends one unit of quota for each mirrored page view or file download. Quota is
reserved *before* the upstream is contacted and refunded whenever the response
turns out not to be a success, so redirects, 404s, upstream failures and
exceptions never cost the user a use.
"""

from dataclasses import dataclass
from enum import Enum
from http.cookies import CookieError, SimpleCookie
from typing import cast

from fastapi import Request
from starlette.datastructures import Headers
from starlette.responses import RedirectResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.accounts import AccessKind, AccountStore, QuotaDecision, User
from app.errors import AppError, error_response

SESSION_COOKIE_NAME = "ga_session"
LOGIN_REQUIRED_MESSAGE = "请先登录后再使用。"
_SPA_PATH = "/"
_LIMITED_PATH = "/?limited=1"
_HEALTH_PATH = "/api/health"
_OPTIONAL_PREFIX = "/api/auth/"
_API_PREFIX = "/api/"
# Global protected routes; mirrored pages are routed by path everywhere else.
_PROXY_PREFIX = "/proxy/"
_COUNTED_STATUSES = frozenset({200, 206})
_SECONDS_PER_MINUTE = 60


class Gate(Enum):
    """How a request path is guarded."""

    PUBLIC = "public"
    OPTIONAL = "optional"
    REQUIRED = "required"
    QUOTA = "quota"


@dataclass(frozen=True, slots=True)
class GatePolicy:
    """The unauthenticated surface of one deployment.

    Which paths may stay open depends on what the application actually mounted.
    With no built frontend the mirror route claims ``/`` too, so only the health
    probe and the account endpoints may be left public; assuming ``/`` is the
    SPA shell there would hand out unmetered mirror access.
    """

    public_exact: frozenset[str] = frozenset()
    public_prefixes: tuple[str, ...] = ()

    def classify(self, path: str) -> Gate:
        """Return the guard that applies to a request path.

        Anything the application does not otherwise own falls through to the
        mirror route, so the default is the strictest gate rather than PUBLIC.
        """
        if path == _HEALTH_PATH or path in self.public_exact:
            return Gate.PUBLIC
        if path.startswith(self.public_prefixes):
            return Gate.PUBLIC
        if path.startswith(_OPTIONAL_PREFIX):
            return Gate.OPTIONAL
        if path.startswith(_API_PREFIX):
            return Gate.REQUIRED
        return Gate.QUOTA


def session_token(scope: Scope) -> str | None:
    """Read the session cookie value out of the request headers."""
    raw = Headers(scope=scope).get("cookie")
    if raw is None:
        return None
    cookie = SimpleCookie()
    try:
        cookie.load(raw)
    except CookieError:
        return None
    morsel = cookie.get(SESSION_COOKIE_NAME)
    if morsel is None:
        return None
    return morsel.value


def current_user(request: Request) -> User | None:
    """Return the account attached by the middleware, if any."""
    value = cast("object", getattr(request.state, "user", None))
    return value if isinstance(value, User) else None


def current_session_token(request: Request) -> str | None:
    """Return the raw session cookie presented on this request, if any."""
    value = cast("object", getattr(request.state, "session_token", None))
    return value if isinstance(value, str) else None


def _prefers_html(scope: Scope) -> bool:
    return "text/html" in Headers(scope=scope).get("accept", "")


def _access_kind(scope: Scope) -> AccessKind:
    """Classify one metered access by the resource it actually delivered."""
    path = cast("str", scope.get("path", ""))
    return AccessKind.FILE if path.startswith(_PROXY_PREFIX) else AccessKind.PAGE


def _attach(scope: Scope, user: User | None, token: str | None) -> None:
    state = cast("object", scope.get("state"))
    if not isinstance(state, dict):
        state = {}
        scope["state"] = state
    cast("dict[str, object]", state)["user"] = user
    cast("dict[str, object]", state)["session_token"] = token


async def _send_error(scope: Scope, receive: Receive, send: Send, error: AppError) -> None:
    response = error_response(error)
    await response(scope, receive, send)


async def _redirect(scope: Scope, receive: Receive, send: Send, location: str) -> None:
    response = RedirectResponse(url=location, status_code=302)
    await response(scope, receive, send)


def _quota_error(decision: QuotaDecision) -> AppError:
    minutes = max(1, -(-decision.retry_after_seconds // _SECONDS_PER_MINUTE))
    message = f"本小时已用完 {decision.limit} 次访问额度。请在 {minutes} 分钟后重试。"
    return AppError(
        "quota_exceeded",
        message,
        429,
        headers={"retry-after": str(max(1, decision.retry_after_seconds))},
    )


async def _reject_unauthenticated(scope: Scope, receive: Receive, send: Send, gate: Gate) -> None:
    """Send a browser to the sign-in screen, or a clear 401 to any other client."""
    if gate is Gate.QUOTA and _prefers_html(scope):
        await _redirect(scope, receive, send, _SPA_PATH)
        return
    await _send_error(scope, receive, send, AppError("unauthorized", LOGIN_REQUIRED_MESSAGE, 401))


class AuthMiddleware:
    """Authenticate the session cookie and meter every mirrored access."""

    def __init__(
        self,
        app: ASGIApp,
        store: AccountStore,
        policy: GatePolicy,
    ) -> None:
        """Store the downstream application, account store, and gate policy."""
        self.app: ASGIApp = app
        self._store: AccountStore = store
        self._policy: GatePolicy = policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Guard the request according to its path before it reaches a route."""
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        gate = self._policy.classify(cast("str", scope.get("path", "")))
        if gate is Gate.PUBLIC:
            await self.app(scope, receive, send)
            return
        method = cast("str", scope.get("method", "GET")).upper()
        await self._guard(scope, receive, send, gate, method)

    async def _guard(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        gate: Gate,
        method: str,
    ) -> None:
        # The schema is created on first use so the guard works even when the
        # ASGI lifespan never ran, as in tests.
        await self._store.initialize()
        token = session_token(scope)
        user = await self._store.resolve_session(token)
        if gate is Gate.OPTIONAL:
            _attach(scope, user, token)
            await self.app(scope, receive, send)
            return
        if user is None:
            await _reject_unauthenticated(scope, receive, send, gate)
            return
        _attach(scope, user, token)
        # Only actually fetching something costs a use; a HEAD probe, a logout,
        # or a resolve submission does not.
        if gate is not Gate.QUOTA or method != "GET":
            await self.app(scope, receive, send)
            return
        await self._meter(scope, receive, send, user)

    async def _meter(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        user: User,
    ) -> None:
        decision = await self._store.consume(user.id)
        if not decision.allowed:
            if _prefers_html(scope):
                await _redirect(scope, receive, send, _LIMITED_PATH)
                return
            await _send_error(scope, receive, send, _quota_error(decision))
            return
        status = await self._forward_counted(scope, receive, send, decision)
        # Only content that actually reached the client is published as a use;
        # the refunded cases above are deliberately absent from the report.
        if status in _COUNTED_STATUSES:
            await self._store.record_access(user.id, _access_kind(scope))

    async def _forward_counted(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        decision: QuotaDecision,
    ) -> int:
        """Run a metered request, refunding the reservation on any non-success."""
        status = 0

        async def capture(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = cast("int", message["status"])
            await send(message)

        try:
            await self.app(scope, receive, capture)
        except BaseException:
            if decision.usage_id is not None:
                await self._store.refund(decision.usage_id)
            raise
        if decision.usage_id is not None and status not in _COUNTED_STATUSES:
            await self._store.refund(decision.usage_id)
        return status


__all__ = [
    "LOGIN_REQUIRED_MESSAGE",
    "SESSION_COOKIE_NAME",
    "AuthMiddleware",
    "Gate",
    "GatePolicy",
    "current_session_token",
    "current_user",
    "session_token",
]
