"""FastAPI application factory and public API routes."""

import logging
import secrets
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from typing import cast
from urllib.parse import urlencode

import anyio
import httpx2
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import FileResponse, RedirectResponse, Response
from starlette.types import ASGIApp

from app.accounts import (
    AccountStore,
    EmailTakenError,
    QuotaSnapshot,
    StatWindow,
    User,
    UsernameTakenError,
    VerificationThrottledError,
    build_account_store,
)
from app.auth import (
    SESSION_COOKIE_NAME,
    AuthMiddleware,
    GatePolicy,
    current_session_token,
    current_user,
)
from app.cache import DownloadCache
from app.config import Settings
from app.errors import AppError, error_response
from app.mailer import MailDeliveryError, Mailer, build_mailer, verification_link
from app.middleware import RequestSizeLimitMiddleware, SecurityHeadersMiddleware
from app.proxy import ProxyService
from app.schemas import (
    GrantRequest,
    HealthResponse,
    LoginRequest,
    QuotaView,
    RegisterRequest,
    ResolveRequest,
    ResolveResponse,
    SessionResponse,
    StatsResponse,
    StatsWindowView,
    VerificationRequest,
)
from app.sso import (
    issue_assertion,
    parse_assertion,
    sign_state,
    sso_username,
    verify_grant_ticket,
    verify_state,
)
from app.upstream import build_upstream_client
from app.url_policy import PageKind, encode_proxy_token, parse_page_url, parse_source_url
from app.webproxy import WebPageProxy

logger = logging.getLogger(__name__)

_NOT_FOUND_STATUS = HTTPStatus.NOT_FOUND
_METHOD_NOT_ALLOWED_STATUS = HTTPStatus.METHOD_NOT_ALLOWED
_CLIENT_ERROR_MIN = HTTPStatus.BAD_REQUEST
_SERVER_ERROR = HTTPStatus.INTERNAL_SERVER_ERROR
_SPA_DIRECTORIES = ("assets", "fonts")
_PRECOMPRESSED_DIRECTORIES = frozenset({"fonts"})
_RESERVED_PATH_PREFIXES = frozenset({"api", "proxy"})


def _invalid_request() -> AppError:
    """Build the public validation error without echoing input."""
    return AppError("invalid_request", "请求格式无效。", 400)


def _service_unavailable() -> AppError:
    """Build the failure used when a lifespan-scoped service is missing."""
    return AppError("internal_error", "服务暂时不可用。", 500)


def _build_download_cache(settings: Settings) -> DownloadCache | None:
    """Build the payload cache, or disable caching with a zero size cap."""
    if settings.archive_cache_max_bytes <= 0:
        return None
    return DownloadCache(settings.archive_cache_dir, settings.archive_cache_max_bytes)


def _spa_gate_policy(settings: Settings) -> GatePolicy:
    """Derive the unauthenticated surface from what the frontend actually owns.

    In API-only mode nothing is public: the mirror route claims ``/`` and the
    asset paths as well, so leaving them open would hand out unmetered access.
    """
    static_dir = settings.resolved_static_dir
    if not (static_dir / "index.html").is_file():
        return GatePolicy()
    prefixes = tuple(f"/{name}/" for name in _SPA_DIRECTORIES if (static_dir / name).is_dir())
    return GatePolicy(
        # ``/stats`` is the shell's second entry point and its report is meant to
        # be readable by anyone, so both stay open. The trailing-slash form is
        # listed because the router only normalizes it after the gate has run.
        # The crawl documents are open for the same reason: a robot that meets a
        # 401 records nothing but an error.
        public_exact=frozenset(
            {
                "/",
                "/api/stats",
                "/favicon.svg",
                "/og.png",
                "/robots.txt",
                "/sitemap.xml",
                "/stats",
                "/stats/",
            }
        ),
        public_prefixes=prefixes,
    )


def _route_template(request: Request) -> str:
    """Return a route template without logging user-controlled path data."""
    route = cast("object", request.scope.get("route"))
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


def _log_request(
    request: Request,
    route: str,
    status: int,
    started: float,
    request_id: str,
) -> None:
    """Write a minimal access record without user-controlled values."""
    logger.info(
        "request method=%s route=%s status=%d duration_ms=%.2f request_id=%s",
        request.method,
        route,
        status,
        (time.perf_counter() - started) * 1000,
        request_id,
    )


def _register_request_logging(application: FastAPI) -> None:
    """Register access logging for completed and failed requests."""

    @application.middleware("http")
    async def log_request(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started = time.perf_counter()
        request_id = secrets.token_hex(8)
        try:
            response = await call_next(request)
        except Exception:
            _log_request(request, _route_template(request), 500, started, request_id)
            raise
        _log_request(request, _route_template(request), response.status_code, started, request_id)
        return response


def _not_found() -> AppError:
    """Build the public not-found error used by every unmatched path."""
    return AppError("not_found", "请求的资源不存在。", _NOT_FOUND_STATUS)


def _http_error(error: StarletteHTTPException) -> JSONResponse:
    """Convert a framework HTTP error into the stable public envelope."""
    status = error.status_code
    if status == _NOT_FOUND_STATUS:
        return error_response(_not_found())
    if status == _METHOD_NOT_ALLOWED_STATUS:
        return error_response(AppError("method_not_allowed", "请求方法不被支持。", status))
    if _CLIENT_ERROR_MIN <= status < _SERVER_ERROR:
        return error_response(_invalid_request())
    logger.error("Unhandled framework error (status=%d)", status)
    return error_response(AppError("internal_error", "服务暂时不可用。", _SERVER_ERROR))


def _register_exception_handlers(application: FastAPI) -> None:
    """Register stable public handlers for expected and unexpected errors."""

    @application.exception_handler(AppError)
    async def handle_app_error(_request: Request, error: Exception) -> JSONResponse:
        if not isinstance(error, AppError):
            raise error
        return error_response(error)

    @application.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        _request: Request,
        error: StarletteHTTPException,
    ) -> JSONResponse:
        return _http_error(error)

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        return error_response(_invalid_request())

    @application.exception_handler(Exception)
    async def handle_unexpected_error(_request: Request, error: Exception) -> JSONResponse:
        logger.error("Unhandled application error (%s)", type(error).__name__)
        return error_response(AppError("internal_error", "服务暂时不可用。", 500))


def _register_routes(
    application: FastAPI,
    get_proxy_service: Callable[[], ProxyService],
) -> None:
    """Register health, resolve, and streaming proxy endpoints."""

    @application.get("/api/health")
    async def health() -> HealthResponse:
        """Report local process readiness without contacting GitHub."""
        return HealthResponse()

    @application.post("/api/resolve")
    async def resolve(payload: ResolveRequest) -> ResolveResponse:
        """Resolve one validated GitHub URL into a same-origin path.

        Files win over pages: ``parse_source_url`` recognizes download shapes
        first, and only a URL it rejects falls through to page mirroring. That
        ordering matters because a file-shaped path such as
        ``/owner/repo/archive/refs/heads/main.zip`` is also a legal mirror
        path, and the user asking for it wants the streamed file, not the
        GitHub page that would 302 to it.
        """
        try:
            target = parse_source_url(payload.url)
        except AppError:
            page = parse_page_url(payload.url)
            return ResolveResponse(
                kind=PageKind.PAGE,
                source_url=page.url,
                proxy_url=page.path,
            )
        token = encode_proxy_token(target.url)
        return ResolveResponse(
            kind=target.kind,
            source_url=target.url,
            proxy_url=f"/proxy/{token}",
        )

    @application.api_route("/proxy/{token}", methods=["GET", "HEAD"], include_in_schema=False)
    async def proxy(token: str, request: Request) -> Response:
        """Stream a validated GitHub file through the shared bounded client."""
        return await get_proxy_service().handle(token, request.method, request.headers)


def _anonymous_session() -> SessionResponse:
    """Build the body returned to a visitor with no live session."""
    return SessionResponse(authenticated=False, username=None, quota=None)


def _pending_session(user: User) -> JSONResponse:
    """Report an account whose address is still waiting to be confirmed.

    The body carries no cookie and no quota: the account exists but has no
    session, so the only thing the client can do with it is show the
    verification step.
    """
    body = SessionResponse(
        authenticated=False,
        username=user.username,
        quota=None,
        email=user.email,
        email_verified=False,
    )
    return JSONResponse(content=body.model_dump())


def _build_session_response(
    settings: Settings,
    user: User,
    quota: QuotaSnapshot,
    *,
    token: str | None,
) -> JSONResponse:
    """Render the session body, setting the cookie only when signing in."""
    body = SessionResponse(
        authenticated=True,
        username=user.username,
        quota=QuotaView(
            limit=quota.limit,
            remaining=quota.remaining,
            reset_after_seconds=quota.reset_after_seconds,
        ),
        email=user.email,
        email_verified=True,
    )
    response = JSONResponse(content=body.model_dump())
    if token is not None:
        _set_session_cookie(response, settings, token)
    return response


def _set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    """Attach the cookie that carries the whole access grant."""
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )


def _build_stats_response(windows: tuple[StatWindow, ...]) -> StatsResponse:
    """Render the aggregate usage report in its published window order."""
    return StatsResponse(
        windows=[
            StatsWindowView(
                key=window.key,
                downloads=window.stats.downloads,
                pages=window.stats.pages,
                users=window.stats.users,
            )
            for window in windows
        ]
    )


def _username_taken() -> AppError:
    return AppError("username_taken", "该用户名已被占用。", 409)


def _invalid_credentials() -> AppError:
    return AppError("invalid_credentials", "用户名或密码不正确。", 401)


def _email_taken() -> AppError:
    return AppError("email_taken", "该邮箱已被其他账号使用。", 409)


def _already_verified() -> AppError:
    return AppError("already_verified", "该账号的邮箱已经验证过了。请直接登录。", 409)


def _verification_invalid() -> AppError:
    return AppError("verification_invalid", "验证链接无效或已过期。请重新获取。", 400)


def _email_unavailable() -> AppError:
    return AppError("email_unavailable", "验证邮件发送失败。请稍后重试。", 503)


def _verification_throttled(retry_after_seconds: int) -> AppError:
    headers = {"retry-after": str(max(1, retry_after_seconds))}
    return AppError("verification_throttled", "验证邮件刚刚发送过。请稍后再试。", 429, headers)


async def _send_verification(
    store: AccountStore,
    settings: Settings,
    mailer: Mailer,
    user: User,
    email: str,
) -> None:
    """Issue one verification token and hand its link to the transport."""
    try:
        raw_token = await store.open_verification(user.id, email)
    except EmailTakenError:
        raise _email_taken() from None
    except VerificationThrottledError as error:
        raise _verification_throttled(error.retry_after_seconds) from None
    try:
        await mailer.send_verification(email, verification_link(settings, raw_token))
    except MailDeliveryError:
        raise _email_unavailable() from None


async def _register_account(
    store: AccountStore,
    settings: Settings,
    mailer: Mailer,
    payload: RegisterRequest,
) -> JSONResponse:
    """Create an account that still has to confirm its address."""
    try:
        user = await store.register(payload.username, payload.password, payload.email)
    except UsernameTakenError:
        raise _username_taken() from None
    except EmailTakenError:
        raise _email_taken() from None
    try:
        await _send_verification(store, settings, mailer, user, payload.email)
    except AppError:
        # An account nobody can activate is worse than none at all: the name
        # would be taken and the address unusable, so the row goes back with
        # the failure that prevented the message from being sent.
        await store.discard_account(user.id)
        raise
    return _pending_session(user)


async def _login_account(
    store: AccountStore,
    settings: Settings,
    payload: LoginRequest,
) -> JSONResponse:
    """Sign a verified account in, or report the one still to be confirmed."""
    user = await store.authenticate(payload.username, payload.password)
    if user is None:
        raise _invalid_credentials()
    if not user.email_verified:
        # No cookie is set here: an unconfirmed account has no session to hand
        # out, and the client is expected to show the verification step.
        return _pending_session(user)
    token = await store.open_session(user.id)
    return _build_session_response(settings, user, await store.snapshot(user.id), token=token)


async def _request_verification(
    store: AccountStore,
    settings: Settings,
    mailer: Mailer,
    payload: VerificationRequest,
) -> JSONResponse:
    """Mail a verification link, attaching an address when the account has none."""
    user = await store.authenticate(payload.username, payload.password)
    if user is None:
        raise _invalid_credentials()
    if user.email_verified:
        raise _already_verified()
    email = payload.email or user.email
    if email is None:
        raise _invalid_request()
    await _send_verification(store, settings, mailer, user, email)
    return _pending_session(User(user.id, user.username, email, email_verified=False))


def _wants_json(request: Request) -> bool:
    """Report whether the caller asked for a machine-readable body."""
    return "text/html" not in request.headers.get("accept", "")


async def _complete_verification(
    store: AccountStore,
    settings: Settings,
    token: str,
    *,
    wants_json: bool,
) -> Response:
    """Consume a verification token and land the recipient somewhere useful.

    A mailed link is opened in a browser, so the outcome is reported by
    redirecting to the shell with a flag the page can render. Whoever opens a
    valid link has just proved control of the address, so that browser is also
    signed in on the way out: the landing page is already authenticated, and
    any other tab of the same browser adopts the cookie on its next read. A
    script or a test client gets the outcome as JSON and no cookie, because it
    can ask to sign in explicitly.
    """
    user = await store.verify_email(token)
    if user is None:
        if wants_json:
            raise _verification_invalid()
        return RedirectResponse("/?verify_failed=1", status_code=303)
    if wants_json:
        body = SessionResponse(
            authenticated=False,
            username=user.username,
            quota=None,
            email=user.email,
            email_verified=True,
        )
        return JSONResponse(content=body.model_dump())
    response = RedirectResponse("/?verified=1", status_code=303)
    _set_session_cookie(response, settings, await store.open_session(user.id))
    return response


async def _logout_session(store: AccountStore, token: str | None) -> JSONResponse:
    """Discard the presented session and clear its cookie."""
    await store.close_session(token)
    response = JSONResponse(content=_anonymous_session().model_dump())
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


async def _read_session(
    store: AccountStore,
    user: User | None,
    settings: Settings,
) -> JSONResponse:
    """Report the current session and quota, or an anonymous visitor."""
    if user is None:
        return JSONResponse(content=_anonymous_session().model_dump())
    return _build_session_response(settings, user, await store.snapshot(user.id), token=None)


def _register_auth_routes(
    application: FastAPI,
    store: AccountStore,
    settings: Settings,
    mailer: Mailer,
) -> None:
    """Register the account, session, and email verification endpoints."""

    @application.post("/api/auth/register")
    async def register(payload: RegisterRequest) -> JSONResponse:
        """Create an account that still has to confirm its address."""
        return await _register_account(store, settings, mailer, payload)

    @application.post("/api/auth/login")
    async def login(payload: LoginRequest) -> JSONResponse:
        """Sign in a verified account, or report the one still to be confirmed."""
        return await _login_account(store, settings, payload)

    @application.post("/api/auth/logout")
    async def logout(request: Request) -> JSONResponse:
        """Discard the presented session and clear its cookie."""
        return await _logout_session(store, current_session_token(request))

    @application.get("/api/auth/me")
    async def me(request: Request) -> JSONResponse:
        """Report the current session and quota, or an anonymous visitor."""
        return await _read_session(store, current_user(request), settings)

    @application.post("/api/auth/verify/request")
    async def request_verification(payload: VerificationRequest) -> JSONResponse:
        """Mail a verification link to a new or already registered address."""
        return await _request_verification(store, settings, mailer, payload)

    @application.get("/api/auth/verify")
    async def verify(request: Request, token: str = "") -> Response:
        """Consume one verification token from a mailed link."""
        return await _complete_verification(store, settings, token, wants_json=_wants_json(request))


def _sso_append_query(url: str, params: dict[str, str]) -> str:
    """Append non-empty parameters to a URL, reusing an existing separator."""
    query = urlencode({key: value for key, value in params.items() if value})
    if not query:
        return url
    return f"{url}{'&' if '?' in url else '?'}{query}"


def _is_local_host(host: str) -> bool:
    """Whether a host is loopback or a private address, so it may stay http."""
    if host in {"localhost", "127.0.0.1", "::1", "[::1]"} or host.endswith(".local"):
        return True
    if host.startswith(("10.", "192.168.")):
        return True
    return any(host.startswith(f"172.{number}.") for number in range(16, 32))


def _sso_self_callback(settings: Settings, request: Request) -> str:
    """This service's own SSO callback URL, configured or derived from the request."""
    configured = settings.sso_self_callback_url.strip()
    if configured:
        return configured
    scheme = request.url.scheme
    host = request.url.hostname or ""
    if scheme == "http" and host and not _is_local_host(host):
        # A TLS-terminating proxy may not forward the scheme, so non-local hosts
        # are assumed to be served over HTTPS.
        scheme = "https"
    port = request.url.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    return f"{scheme}://{netloc}/api/sso/callback"


def _sso_unavailable() -> AppError:
    """Build the failure used when SSO is not configured."""
    return AppError("sso_unavailable", "单点登录未配置。", 503)


def _sso_invalid_redirect() -> AppError:
    """Build the failure used when an authorize redirect_uri is not allowed."""
    return AppError("invalid_redirect", "非法的回跳地址。", 400)


_GRANT_NO_USER = "找不到对应的加速站账号。请先用博客账号登录一次加速站"


def _grant_rejected(message: str) -> AppError:
    return AppError("grant_rejected", message, 400)


def _register_sso_routes(  # noqa: C901
    application: FastAPI,
    store: AccountStore,
    settings: Settings,
) -> None:
    """Register the cross-site SSO endpoints shared with the blog."""

    @application.get("/api/sso/providers", include_in_schema=False)
    async def sso_providers() -> JSONResponse:
        """Tell the SPA which cross-site buttons to show (SSO sign-in and redeem)."""
        return JSONResponse(
            {"blog": settings.sso_enabled, "redeem_enabled": settings.redeem_enabled}
        )

    @application.get("/api/sso/start", include_in_schema=False)
    async def sso_start(request: Request, redirect: str = "/") -> Response:
        """As the RP, sign a state and bounce the browser to the blog authorize."""
        if not settings.sso_enabled:
            raise _sso_unavailable()
        state = sign_state(settings, redirect)
        target = _sso_append_query(
            settings.sso_peer_authorize_url.strip(),
            {"redirect_uri": _sso_self_callback(settings, request), "state": state},
        )
        return RedirectResponse(target, status_code=302)

    @application.get("/api/sso/authorize", include_in_schema=False)
    async def sso_authorize(request: Request, redirect_uri: str = "", state: str = "") -> Response:
        """As the IdP, sign an assertion for a signed-in user, or send them to login."""
        if not settings.sso_enabled:
            raise _sso_unavailable()
        allowed = settings.sso_peer_callback_url.strip()
        if not allowed or redirect_uri != allowed:
            raise _sso_invalid_redirect()
        user = current_user(request)
        if user is not None:
            assertion = issue_assertion(settings, user)
            return RedirectResponse(
                _sso_append_query(redirect_uri, {"assertion": assertion, "state": state}),
                status_code=302,
            )
        # Not signed in: the SPA home shows the auth panel and, once the visitor is
        # signed in, sends them back to this authorize URL (now with a session).
        return_to = request.url.path
        if request.url.query:
            return_to = f"{return_to}?{request.url.query}"
        return RedirectResponse(_sso_append_query("/", {"sso_return": return_to}), status_code=302)

    @application.get("/api/sso/callback", include_in_schema=False)
    async def sso_callback(assertion: str = "", state: str = "") -> Response:
        """As the RP, verify the blog's assertion, open an account, and sign in."""
        if not settings.sso_enabled:
            raise _sso_unavailable()
        try:
            return_path = verify_state(settings, state)
            identity = parse_assertion(settings, assertion)
            user = await store.open_sso_account(identity.email, sso_username(identity.username))
            token = await store.open_session(user.id)
        except AppError as error:
            return RedirectResponse(
                _sso_append_query("/", {"sso": "error", "message": error.message}),
                status_code=303,
            )
        response = RedirectResponse(_sso_append_query(return_path, {"sso": "ok"}), status_code=303)
        _set_session_cookie(response, settings, token)
        return response

    @application.post("/api/sso/grant", include_in_schema=False)
    async def sso_grant(payload: GrantRequest) -> JSONResponse:
        """Blog -> accelerator server-to-server: top up the bonus reserve.

        The blog deducts credits first, then signs this grant ticket; we verify
        it and add the bonus to the account matched by email. Anonymous is fine:
        the ticket (not a session) is the authentication.
        """
        if not settings.redeem_enabled:
            raise _sso_unavailable()
        email, attempts = verify_grant_ticket(settings, payload.ticket)
        user = await store.find_by_email(email)
        if user is None:
            raise _grant_rejected(_GRANT_NO_USER)
        granted = await store.add_bonus(user.id, attempts)
        logger.info(
            "博客授额票据生效。%s(%s) 加 %d 次。剩余兑换额度 %d",
            user.username,
            email,
            attempts,
            granted,
        )
        return JSONResponse({"ok": True, "attempts_granted": attempts, "bonus": granted})


def _register_stats_routes(application: FastAPI, store: AccountStore) -> None:
    """Register the aggregate usage report shown by the data page."""

    @application.get("/api/stats")
    async def stats() -> StatsResponse:
        """Report aggregate usage across every published time window.

        The body carries counts only: no usernames, no addresses, and no
        per-request detail, so it can stay readable without a session.
        """
        return _build_stats_response(await store.service_stats())


def _static_app(directory_name: str, directory: Path) -> ASGIApp:
    """Serve one build directory, gzipping the text assets inside it.

    The middleware is scoped to this mount: applied to the whole application it
    would also compress the binary download stream, where it is pure overhead.
    ``fonts`` is mounted without it, because woff2 is already compressed and
    :mod:`mimetypes` does not know the extension, so those files would only be
    gzipped into something marginally larger.
    """
    static: ASGIApp = StaticFiles(directory=directory)
    if directory_name not in _PRECOMPRESSED_DIRECTORIES:
        static = GZipMiddleware(static)
    return static


_CRAWL_CACHE_HEADERS: dict[str, str] = {"cache-control": "public, max-age=3600"}


def _canonical_base(settings: Settings) -> str:
    """Return the origin this deployment is published under, without a slash."""
    return settings.public_base_url.rstrip("/")


def _robots_document(settings: Settings) -> str:
    """Build the crawl policy for the paths this deployment actually serves.

    The API and the mirror are disallowed because a crawler that follows them
    would publish other people's repositories under this address.
    """
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /proxy/\n"
        "\n"
        f"Sitemap: {_canonical_base(settings)}/sitemap.xml\n"
    )


def _sitemap_document(settings: Settings, index: Path) -> str:
    """List the two entry points the shell answers, dated by the build itself."""
    base = _canonical_base(settings)
    lastmod = datetime.fromtimestamp(index.stat().st_mtime, tz=UTC).date().isoformat()
    entries = "".join(
        f"  <url><loc>{base}{path}</loc><lastmod>{lastmod}</lastmod></url>\n"
        for path in ("/", "/stats")
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}"
        "</urlset>\n"
    )


def _register_static_assets(application: FastAPI, settings: Settings) -> None:
    """Serve the built single-page app from the paths the build actually owns.

    Only the paths the build owns are registered, so the reverse-proxy
    catch-all registered afterwards can claim every other path. A missing
    build leaves the service in API-only mode.
    """
    static_dir = settings.resolved_static_dir
    index = static_dir / "index.html"
    if not index.is_file():
        logger.info("Static assets are not built at %s; running in API-only mode", static_dir)
        return
    for directory_name in _SPA_DIRECTORIES:
        directory = static_dir / directory_name
        if directory.is_dir():
            application.mount(
                f"/{directory_name}",
                _static_app(directory_name, directory),
                name=f"spa-{directory_name}",
            )
    favicon = static_dir / "favicon.svg"
    _register_crawl_documents(application, settings, static_dir, index)

    @application.get("/", include_in_schema=False)
    @application.get("/stats", include_in_schema=False)
    async def spa_shell() -> FileResponse:
        """Return the single-page shell for either entry point, always revalidated.

        The shell names the content-hashed bundle, so a heuristically cached
        copy silently pins the browser to a previous build. ``no-cache`` forces
        a conditional request on every load -- a cheap 304 against the ETag --
        while the hashed files under ``/assets`` stay freely cacheable. The data
        page is the same bundle, so it reuses this route instead of duplicating
        the header policy.
        """
        return FileResponse(index, headers={"cache-control": "no-cache"})

    @application.get("/favicon.svg", include_in_schema=False)
    async def spa_favicon() -> Response:
        """Return the site icon, or the JSON envelope when the build omitted it."""
        if not favicon.is_file():
            raise StarletteHTTPException(status_code=_NOT_FOUND_STATUS)
        return FileResponse(favicon)


def _register_crawl_documents(
    application: FastAPI,
    settings: Settings,
    static_dir: Path,
    index: Path,
) -> None:
    """Serve the crawl documents and the social preview card.

    They are generated here instead of shipped with the build, so the origin
    they advertise is the one this process was told to publish under. The gate
    policy lists all three as public, because a crawler that meets a 401
    records nothing but an error.
    """
    robots = _robots_document(settings)
    sitemap = _sitemap_document(settings, index)
    preview = static_dir / "og.png"

    @application.get("/robots.txt", include_in_schema=False)
    async def spa_robots() -> Response:
        """Answer crawlers directly, so they never index the mirror instead."""
        return Response(robots, media_type="text/plain", headers=_CRAWL_CACHE_HEADERS)

    @application.get("/sitemap.xml", include_in_schema=False)
    async def spa_sitemap() -> Response:
        """Publish both entry points instead of leaving them to be guessed."""
        return Response(sitemap, media_type="application/xml", headers=_CRAWL_CACHE_HEADERS)

    @application.get("/og.png", include_in_schema=False)
    async def spa_preview() -> Response:
        """Return the social preview card, or the JSON envelope when absent."""
        if not preview.is_file():
            raise StarletteHTTPException(status_code=_NOT_FOUND_STATUS)
        return FileResponse(preview)


def _register_web_proxy(
    application: FastAPI,
    get_web_page_proxy: Callable[[], WebPageProxy],
) -> None:
    """Mirror public github.com pages for every path no other route claimed.

    It is registered last so the API, the file proxy, and the built frontend
    all keep their own handlers.
    """

    @application.api_route(
        "/{path:path}",
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )
    async def github_page(path: str, request: Request) -> Response:
        """Stream one public github.com path through this origin."""
        if path.split("/", 1)[0] in _RESERVED_PATH_PREFIXES:
            raise StarletteHTTPException(status_code=_NOT_FOUND_STATUS)
        return await get_web_page_proxy().handle(
            path,
            request.method,
            request.url.query,
            request.headers,
        )


def create_app(  # noqa: PLR0915
    settings: Settings | None = None,
    *,
    upstream_client: httpx2.AsyncClient | None = None,
    capacity_limiter: anyio.CapacityLimiter | None = None,
    mailer: Mailer | None = None,
    account_store: AccountStore | None = None,
) -> FastAPI:
    """Create an application instance with safe API and proxy routes."""
    app_settings = settings or Settings()
    download_cache = _build_download_cache(app_settings)
    gate_policy = _spa_gate_policy(app_settings)
    # One pool serves the whole process. It is created lazily, because the tests
    # build the application without running the ASGI lifespan, and it is only
    # closed here when this function is what opened it.
    store = account_store if account_store is not None else build_account_store(app_settings)
    owns_store = account_store is None
    # Injected by tests, which must not reach a real mail server; otherwise the
    # configured transport decides whether a message is actually sent.
    mailer_service = mailer if mailer is not None else build_mailer(app_settings)
    proxy_service: ProxyService | None = None
    web_page_proxy: WebPageProxy | None = None

    def current_proxy_service() -> ProxyService:
        service = proxy_service
        if service is None:
            raise _service_unavailable()
        return service

    def current_web_page_proxy() -> WebPageProxy:
        proxy = web_page_proxy
        if proxy is None:
            raise _service_unavailable()
        return proxy

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncGenerator[None]:
        nonlocal proxy_service, web_page_proxy
        client = upstream_client or build_upstream_client(app_settings)
        limiter = capacity_limiter or anyio.CapacityLimiter(app_settings.max_concurrent_proxies)
        await store.initialize()
        proxy_service = ProxyService(
            client,
            limiter,
            download_cache,
            queue_timeout_seconds=app_settings.proxy_queue_timeout_seconds,
        )
        web_page_proxy = WebPageProxy(client)
        try:
            yield
        finally:
            if upstream_client is None:
                await client.aclose()
            proxy_service = None
            web_page_proxy = None
            if owns_store:
                await store.close()

    application = FastAPI(
        title="GitHub Accelerator",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.add_middleware(RequestSizeLimitMiddleware)
    # Sits outside the body limit so an unauthenticated or over-quota request is
    # rejected before any of its body is read, and inside the security-headers
    # layer so its own 401/429/302 responses still carry the policy.
    application.add_middleware(AuthMiddleware, store=store, policy=gate_policy)
    application.add_middleware(SecurityHeadersMiddleware)
    if app_settings.trusted_hosts:
        # Added last so it becomes the outermost layer and rejects a forged Host
        # header before any other middleware or route observes the request.
        application.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=list(app_settings.trusted_hosts),
        )
    _register_request_logging(application)
    _register_exception_handlers(application)
    _register_routes(application, current_proxy_service)
    _register_auth_routes(application, store, app_settings, mailer_service)
    _register_sso_routes(application, store, app_settings)
    _register_stats_routes(application, store)
    _register_static_assets(application, app_settings)
    _register_web_proxy(application, current_web_page_proxy)

    if upstream_client is not None and capacity_limiter is not None:
        # This path exists so tests can drive the application without running
        # the ASGI lifespan; the account store above needs no such treatment
        # because it brings its own pool up on the first request.
        proxy_service = ProxyService(
            upstream_client,
            capacity_limiter,
            download_cache,
            queue_timeout_seconds=app_settings.proxy_queue_timeout_seconds,
        )
        web_page_proxy = WebPageProxy(upstream_client)
    return application


app = create_app()
