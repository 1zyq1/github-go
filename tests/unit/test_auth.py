"""Unit tests for session-cookie authentication and quota metering."""

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import TypedDict, cast

import pytest
import pytest_asyncio
from app.accounts import AccountStore, StorePolicy, User, WindowStats, mysql_target
from app.auth import (
    SESSION_COOKIE_NAME,
    AuthMiddleware,
    Gate,
    GatePolicy,
    current_session_token,
    current_user,
    session_token,
)
from app.config import Settings
from app.errors import ErrorEnvelope
from pydantic import TypeAdapter
from starlette.requests import Request
from starlette.types import Message, Receive, Scope, Send

from tests.support import opened_store

pytestmark = pytest.mark.asyncio

_LIMIT = 1
_WINDOW = 3600.0
_TTL = 3600.0
_PASSWORD = "password-1234"


def _policy(*, limit: int = _LIMIT) -> StorePolicy:
    """Build the store policy these unit tests exercise."""
    return StorePolicy(
        limit=limit,
        window_seconds=_WINDOW,
        session_ttl_seconds=_TTL,
        verification_ttl_seconds=_TTL,
        verification_resend_seconds=0.0,
    )


_POLICY = GatePolicy(
    public_exact=frozenset({"/", "/favicon.svg"}),
    public_prefixes=("/assets/", "/fonts/"),
)


class _Clock:
    """A hand-cranked stand-in for the ``time`` module the store reads."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now: float = now

    def time(self) -> float:
        return self.now


@dataclass(frozen=True, slots=True)
class _Account:
    """A registered member together with one of their raw session tokens."""

    user: User
    token: str


@dataclass(frozen=True, slots=True)
class _ASGIResult:
    """The response the middleware chain produced for one request."""

    status: int
    headers: dict[str, str]
    body: bytes


class _Started(TypedDict):
    type: str
    status: int
    headers: list[tuple[bytes, bytes]]


class _Downstream:
    """An ASGI application that records whether the request reached it."""

    def __init__(self, *, status: int = 200, explode: bool = False) -> None:
        self.status: int = status
        self.explode: bool = explode
        self.reached: bool = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.reached = True
        if self.explode:
            message = "downstream exploded"
            raise RuntimeError(message)
        await send({"type": "http.response.start", "status": self.status, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    frozen = _Clock()
    monkeypatch.setattr("app.accounts.time", frozen)
    return frozen


def _store(policy: StorePolicy) -> AccountStore:
    """Build a store on the shared test tables with an explicit policy."""
    return AccountStore(mysql_target(Settings()), policy)


@pytest_asyncio.fixture(loop_scope="function")
async def store() -> AsyncGenerator[AccountStore]:
    """Return an emptied store on the prefixed test tables."""
    async with opened_store(_store(_policy())) as built:
        yield built


async def _sign_in(store: AccountStore, username: str = "member") -> _Account:
    """Register an account, confirm its address, and open a session for it."""
    email = f"{username}@example.com"
    user = await store.register(username, _PASSWORD, email)
    confirmed = await store.verify_email(await store.open_verification(user.id, email))
    assert confirmed is not None
    return _Account(confirmed, await store.open_session(confirmed.id))


def _scope(
    path: str,
    *,
    method: str = "GET",
    cookie: str | None = None,
    accept: str | None = None,
    state: dict[str, object] | None = None,
) -> Scope:
    headers: list[tuple[bytes, bytes]] = []
    if cookie is not None:
        headers.append((b"cookie", cookie.encode()))
    if accept is not None:
        headers.append((b"accept", accept.encode()))
    scope: dict[str, object] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    if state is not None:
        scope["state"] = state
    return cast("Scope", scope)


def _cookie(token: str) -> str:
    return f"{SESSION_COOKIE_NAME}={token}"


async def _call(
    store: AccountStore,
    scope: Scope,
    *,
    policy: GatePolicy | None = None,
    downstream: _Downstream | None = None,
) -> _ASGIResult:
    app = downstream if downstream is not None else _Downstream()
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    middleware = AuthMiddleware(
        app, store=store, policy=policy if policy is not None else GatePolicy()
    )
    await middleware(scope, receive, send)

    start = cast(
        "_Started | None",
        next((item for item in sent if item["type"] == "http.response.start"), None),
    )
    if start is None:
        return _ASGIResult(0, {}, b"")
    body = b"".join(
        cast("bytes", item.get("body", b""))
        for item in sent
        if item["type"] == "http.response.body"
    )
    return _ASGIResult(
        start["status"],
        {key.decode(): value.decode() for key, value in start["headers"]},
        body,
    )


def _error_code(body: bytes) -> str:
    return TypeAdapter(ErrorEnvelope).validate_json(body)["error"]["code"]


async def test_non_http_scopes_bypass_the_guard(store: AccountStore) -> None:
    downstream = _Downstream()
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:
        sent.append(message)

    await AuthMiddleware(downstream, store=store, policy=_POLICY)(
        cast("Scope", {"type": "lifespan"}),
        receive,
        send,
    )

    assert downstream.reached
    assert sent[0]["type"] == "http.response.start"


@pytest.mark.parametrize(
    "path",
    ["/api/health", "/", "/favicon.svg", "/assets/app.js", "/fonts/inter.woff2"],
)
async def test_public_paths_are_served_without_a_session(store: AccountStore, path: str) -> None:
    downstream = _Downstream()

    result = await _call(store, _scope(path), policy=_POLICY, downstream=downstream)

    assert result.status == 200
    assert downstream.reached


async def test_optional_paths_serve_both_anonymous_and_signed_in_clients(
    store: AccountStore,
) -> None:
    account = await _sign_in(store)

    anonymous = _Downstream()
    assert (
        await _call(store, _scope("/api/auth/me"), policy=_POLICY, downstream=anonymous)
    ).status == 200
    assert anonymous.reached

    signed_in = _Downstream()
    result = await _call(
        store,
        _scope("/api/auth/me", cookie=_cookie(account.token)),
        policy=_POLICY,
        downstream=signed_in,
    )
    assert result.status == 200
    assert signed_in.reached


async def test_the_guard_attaches_the_account_to_the_request_state(
    store: AccountStore,
) -> None:
    account = await _sign_in(store)
    scope = _scope("/api/auth/me", cookie=_cookie(account.token))

    _ = await _call(store, scope, policy=_POLICY)

    state = cast("dict[str, object]", scope["state"])
    assert state["user"] == account.user
    assert state["session_token"] == account.token


async def test_the_guard_reuses_an_existing_state_dictionary(store: AccountStore) -> None:
    scope = _scope("/api/auth/me", state={})

    _ = await _call(store, scope, policy=_POLICY)

    state = cast("dict[str, object]", scope["state"])
    assert state["user"] is None
    assert state["session_token"] is None


async def test_required_api_paths_challenge_anonymous_clients(store: AccountStore) -> None:
    downstream = _Downstream()

    result = await _call(
        store,
        _scope("/api/resolve", method="POST"),
        policy=_POLICY,
        downstream=downstream,
    )

    assert result.status == 401
    assert _error_code(result.body) == "unauthorized"
    assert not downstream.reached


async def test_required_api_paths_are_never_metered(store: AccountStore) -> None:
    account = await _sign_in(store)
    downstream = _Downstream()

    result = await _call(
        store,
        _scope("/api/resolve", cookie=_cookie(account.token)),
        policy=_POLICY,
        downstream=downstream,
    )

    assert result.status == 200
    assert downstream.reached
    assert (await store.snapshot(account.user.id)).remaining == _LIMIT


async def test_mirror_requests_redirect_a_browser_to_the_sign_in_screen(
    store: AccountStore,
) -> None:
    downstream = _Downstream()

    result = await _call(
        store,
        _scope("/acme/widgets", accept="text/html"),
        policy=_POLICY,
        downstream=downstream,
    )

    assert result.status == 302
    assert result.headers["location"] == "/"
    assert not downstream.reached


async def test_mirror_requests_challenge_other_clients_with_json(store: AccountStore) -> None:
    downstream = _Downstream()

    result = await _call(store, _scope("/acme/widgets"), policy=_POLICY, downstream=downstream)

    assert result.status == 401
    assert _error_code(result.body) == "unauthorized"
    assert not downstream.reached


async def test_a_metered_success_spends_one_unit(store: AccountStore) -> None:
    account = await _sign_in(store)
    downstream = _Downstream(status=200)

    result = await _call(
        store,
        _scope("/acme/widgets", cookie=_cookie(account.token)),
        policy=_POLICY,
        downstream=downstream,
    )

    assert result.status == 200
    assert downstream.reached
    assert (await store.snapshot(account.user.id)).remaining == 0


async def test_an_unsuccessful_mirror_refunds_the_reservation(store: AccountStore) -> None:
    account = await _sign_in(store)
    downstream = _Downstream(status=404)

    result = await _call(
        store,
        _scope("/acme/widgets", cookie=_cookie(account.token)),
        policy=_POLICY,
        downstream=downstream,
    )

    assert result.status == 404
    assert (await store.snapshot(account.user.id)).remaining == _LIMIT


@pytest.mark.parametrize("method", ["HEAD", "POST"])
async def test_probes_and_writes_are_not_metered(store: AccountStore, method: str) -> None:
    account = await _sign_in(store)
    downstream = _Downstream(status=200)

    result = await _call(
        store,
        _scope("/acme/widgets", method=method, cookie=_cookie(account.token)),
        policy=_POLICY,
        downstream=downstream,
    )

    assert result.status == 200
    assert downstream.reached
    assert (await store.snapshot(account.user.id)).remaining == _LIMIT


async def test_a_failing_downstream_refunds_the_reservation(store: AccountStore) -> None:
    account = await _sign_in(store)
    downstream = _Downstream(explode=True)

    with pytest.raises(RuntimeError, match="downstream exploded"):
        _ = await _call(
            store,
            _scope("/acme/widgets", cookie=_cookie(account.token)),
            policy=_POLICY,
            downstream=downstream,
        )

    assert (await store.snapshot(account.user.id)).remaining == _LIMIT


async def test_exhausted_quota_is_refused_with_retry_after(store: AccountStore) -> None:
    account = await _sign_in(store)
    cookie = _cookie(account.token)
    first = await _call(store, _scope("/acme/widgets", cookie=cookie), policy=_POLICY)
    assert first.status == 200

    downstream = _Downstream()
    result = await _call(
        store,
        _scope("/acme/widgets", cookie=cookie),
        policy=_POLICY,
        downstream=downstream,
    )

    assert result.status == 429
    assert result.headers["retry-after"] == "3601"
    assert not downstream.reached
    envelope = TypeAdapter(ErrorEnvelope).validate_json(result.body)
    assert envelope["error"]["code"] == "quota_exceeded"
    assert envelope["error"]["message"] == "本小时已用完 1 次访问额度。请在 61 分钟后重试。"
    assert (await store.snapshot(account.user.id)).remaining == 0


async def test_exhausted_quota_sends_a_browser_to_the_limited_page(store: AccountStore) -> None:
    account = await _sign_in(store)
    cookie = _cookie(account.token)
    _ = await _call(store, _scope("/acme/widgets", cookie=cookie), policy=_POLICY)

    downstream = _Downstream()
    result = await _call(
        store,
        _scope("/acme/widgets", accept="text/html", cookie=cookie),
        policy=_POLICY,
        downstream=downstream,
    )

    assert result.status == 302
    assert result.headers["location"] == "/?limited=1"
    assert not downstream.reached


async def test_session_token_is_read_by_cookie_name() -> None:
    assert session_token(_scope("/", cookie=_cookie("abc"))) == "abc"
    assert session_token(_scope("/", cookie=f"other=1; {_cookie('xyz')}")) == "xyz"
    assert session_token(_scope("/")) is None
    assert session_token(_scope("/", cookie="other=1")) is None


@pytest.mark.parametrize("cookie", ["other=1", "bad key=1", "=1", "$foo=1"])
async def test_session_token_ignores_unparseable_cookies(cookie: str) -> None:
    assert session_token(_scope("/", cookie=cookie)) is None


async def test_current_user_and_token_read_the_request_state() -> None:
    account = User(7, "member", "member@example.com", email_verified=True)

    signed_in = Request(
        _scope("/", state={"user": account, "session_token": "raw-token"}),
    )
    assert current_user(signed_in) == account
    assert current_session_token(signed_in) == "raw-token"

    anonymous = Request(_scope("/", state={"user": None, "session_token": None}))
    assert current_user(anonymous) is None
    assert current_session_token(anonymous) is None

    malformed = Request(_scope("/", state={"user": "member", "session_token": 7}))
    assert current_user(malformed) is None
    assert current_session_token(malformed) is None


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/api/health", Gate.PUBLIC),
        ("/", Gate.PUBLIC),
        ("/favicon.svg", Gate.PUBLIC),
        ("/assets/app.js", Gate.PUBLIC),
        ("/fonts/inter.woff2", Gate.PUBLIC),
        ("/api/auth/me", Gate.OPTIONAL),
        ("/api/resolve", Gate.REQUIRED),
        ("/api", Gate.QUOTA),
        ("/acme/widgets", Gate.QUOTA),
    ],
)
async def test_gate_policy_with_a_built_frontend(path: str, expected: Gate) -> None:
    assert _POLICY.classify(path) == expected


@pytest.mark.parametrize("path", ["/", "/favicon.svg", "/assets/app.js"])
async def test_gate_policy_without_a_frontend_meters_owned_paths(path: str) -> None:
    assert GatePolicy().classify(path) == Gate.QUOTA


async def test_a_delivered_access_is_reported_by_resource_kind() -> None:
    # A roomier allowance, because this test delivers two distinct resources.
    async with opened_store(_store(_policy(limit=2))) as store:
        account = await _sign_in(store)

        _ = await _call(store, _scope("/proxy/token-abc", cookie=_cookie(account.token)))
        _ = await _call(store, _scope("/octocat/Hello-World", cookie=_cookie(account.token)))

        hour = (await store.service_stats())[0]

        assert hour.stats == WindowStats(downloads=1, pages=1, users=1)


async def test_a_refunded_access_is_absent_from_the_report(store: AccountStore) -> None:
    account = await _sign_in(store)

    result = await _call(
        store,
        _scope("/proxy/token-abc", cookie=_cookie(account.token)),
        downstream=_Downstream(status=404),
    )

    assert result.status == 404
    assert (await store.snapshot(account.user.id)).remaining == _LIMIT
    assert all(window.stats.downloads == 0 for window in await store.service_stats())


async def test_an_exploding_access_is_absent_from_the_report(store: AccountStore) -> None:
    account = await _sign_in(store)

    with pytest.raises(RuntimeError, match="downstream exploded"):
        _ = await _call(
            store,
            _scope("/proxy/token-abc", cookie=_cookie(account.token)),
            downstream=_Downstream(explode=True),
        )

    assert all(window.stats.downloads == 0 for window in await store.service_stats())


async def test_a_refused_over_quota_access_is_absent_from_the_report(
    store: AccountStore,
) -> None:
    account = await _sign_in(store)
    _ = await _call(store, _scope("/proxy/token-abc", cookie=_cookie(account.token)))

    refused = await _call(store, _scope("/proxy/token-two", cookie=_cookie(account.token)))

    assert refused.status == 429
    hour = (await store.service_stats())[0]
    assert hour.stats.downloads == 1


async def test_a_head_probe_is_never_reported(store: AccountStore) -> None:
    account = await _sign_in(store)

    _ = await _call(
        store,
        _scope("/proxy/token-abc", method="HEAD", cookie=_cookie(account.token)),
    )

    assert all(window.stats.downloads == 0 for window in await store.service_stats())


async def test_an_anonymous_access_is_never_reported(store: AccountStore) -> None:
    blocked = await _call(store, _scope("/proxy/token-abc"))

    assert blocked.status == 401
    assert all(window.stats.downloads == 0 for window in await store.service_stats())
