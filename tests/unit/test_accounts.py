"""Unit tests for the MySQL account store and its password helpers."""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from app.accounts import (
    AccessKind,
    AccountStore,
    EmailTakenError,
    StorePolicy,
    User,
    UsernameTakenError,
    VerificationThrottledError,
    WindowStats,
    hash_password,
    hash_session_token,
    mysql_target,
    verify_password,
)
from app.config import Settings

from tests.support import opened_store

pytestmark = pytest.mark.asyncio

_LIMIT = 3
_WINDOW = 100.0
_TTL = 1000.0
_VERIFICATION_TTL = 900.0
_PASSWORD = "password-1234"
_EMAIL = "alice@example.com"


def _policy(*, limit: int = _LIMIT, resend: float = 0.0) -> StorePolicy:
    """Build the store policy these unit tests exercise."""
    return StorePolicy(
        limit=limit,
        window_seconds=_WINDOW,
        session_ttl_seconds=_TTL,
        verification_ttl_seconds=_VERIFICATION_TTL,
        verification_resend_seconds=resend,
    )


class _Clock:
    """A hand-cranked stand-in for the ``time`` module the store reads."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now: float = now

    def time(self) -> float:
        return self.now


@pytest.fixture
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


async def test_password_digest_is_salted_and_verifiable() -> None:
    encoded = hash_password(_PASSWORD)

    assert encoded.startswith("scrypt$16384$8$1$")
    assert encoded.count("$") == 5
    assert _PASSWORD not in encoded
    assert verify_password(_PASSWORD, encoded)
    assert not verify_password("wrong-password", encoded)


async def test_two_digests_of_one_password_differ() -> None:
    assert hash_password(_PASSWORD) != hash_password(_PASSWORD)


async def test_verify_password_rejects_foreign_and_malformed_digests() -> None:
    assert not verify_password(_PASSWORD, "md5$1$2$3$4$5")
    assert not verify_password(_PASSWORD, "not-a-digest")
    assert not verify_password(_PASSWORD, "")
    assert not verify_password(_PASSWORD, "scrypt$1$2$3$zz$zz")


async def test_session_digest_is_stable_and_does_not_leak_the_token() -> None:
    digest = hash_session_token("raw-token")

    assert digest == hash_session_token("raw-token")
    assert digest != "raw-token"
    assert len(digest) == 64


async def test_register_assigns_ids_and_rejects_taken_names(store: AccountStore) -> None:
    first = await store.register("alice", _PASSWORD, _EMAIL)
    second = await store.register("bob", _PASSWORD, "bob@example.com")

    assert isinstance(first, User)
    assert first.username == "alice"
    assert second.id > first.id

    # A different address each time, so only the name can be the conflict.
    with pytest.raises(UsernameTakenError):
        _ = await store.register("alice", _PASSWORD, "spare@example.com")
    with pytest.raises(UsernameTakenError):
        _ = await store.register("ALICE", _PASSWORD, "case@example.com")


async def test_authenticate_accepts_only_the_stored_password(store: AccountStore) -> None:
    created = await store.register("alice", _PASSWORD, _EMAIL)

    found = await store.authenticate("alice", _PASSWORD)
    assert found == created

    assert await store.authenticate("ALICE", _PASSWORD) == created
    assert await store.authenticate("alice", "wrong-password") is None
    assert await store.authenticate("nobody", _PASSWORD) is None


async def test_resolve_session_returns_the_owner_until_it_expires(
    store: AccountStore,
    clock: _Clock,
) -> None:
    user = await store.register("alice", _PASSWORD, _EMAIL)
    token = await store.open_session(user.id)

    assert await store.resolve_session(token) == user
    assert await store.resolve_session("unknown-token") is None
    assert await store.resolve_session(None) is None
    assert await store.resolve_session("") is None

    clock.now += _TTL + 1
    assert await store.resolve_session(token) is None


async def test_close_session_revokes_only_the_presented_token(store: AccountStore) -> None:
    user = await store.register("alice", _PASSWORD, _EMAIL)
    revoked = await store.open_session(user.id)
    kept = await store.open_session(user.id)

    await store.close_session(revoked)

    assert await store.resolve_session(revoked) is None
    assert await store.resolve_session(kept) == user


async def test_close_session_tolerates_a_missing_token(store: AccountStore) -> None:
    await store.close_session(None)
    await store.close_session("never-issued")


async def test_opening_a_session_prunes_the_expired_ones(
    store: AccountStore,
    clock: _Clock,
) -> None:
    user = await store.register("alice", _PASSWORD, _EMAIL)
    stale = await store.open_session(user.id)

    clock.now += _TTL + 1
    fresh = await store.open_session(user.id)

    assert await store.resolve_session(stale) is None
    assert await store.resolve_session(fresh) == user


async def test_consume_spends_up_to_the_limit_then_denies_with_retry_after(
    store: AccountStore,
) -> None:
    user = await store.register("alice", _PASSWORD, _EMAIL)

    remaining: list[int] = []
    for _ in range(_LIMIT):
        decision = await store.consume(user.id)
        assert decision.allowed
        assert decision.usage_id is not None
        remaining.append(decision.remaining)
    assert remaining == [2, 1, 0]

    refused = await store.consume(user.id)
    assert not refused.allowed
    assert refused.remaining == 0
    assert refused.limit == _LIMIT
    assert refused.usage_id is None
    assert refused.retry_after_seconds > 0


async def test_consume_reopens_the_window_once_the_oldest_use_expires(
    store: AccountStore,
    clock: _Clock,
) -> None:
    user = await store.register("alice", _PASSWORD, _EMAIL)
    for _ in range(_LIMIT):
        _ = await store.consume(user.id)
    assert not (await store.consume(user.id)).allowed

    clock.now += _WINDOW + 1

    reopened = await store.consume(user.id)
    assert reopened.allowed
    assert reopened.remaining == _LIMIT - 1


async def test_refund_returns_the_reserved_unit(store: AccountStore) -> None:
    user = await store.register("alice", _PASSWORD, _EMAIL)
    decision = await store.consume(user.id)
    assert decision.usage_id is not None
    assert (await store.snapshot(user.id)).remaining == _LIMIT - 1

    await store.refund(decision.usage_id)

    assert (await store.snapshot(user.id)).remaining == _LIMIT


async def test_bonus_units_extend_the_remaining_allowance(store: AccountStore) -> None:
    user = await store.register("alice", _PASSWORD, _EMAIL)

    granted = await store.add_bonus(user.id, 5)

    assert granted == 5
    assert (await store.snapshot(user.id)).remaining == _LIMIT + 5


async def test_consume_falls_back_to_bonus_once_the_window_is_full(
    store: AccountStore,
) -> None:
    user = await store.register("alice", _PASSWORD, _EMAIL)
    for _ in range(_LIMIT):
        _ = await store.consume(user.id)
    assert not (await store.consume(user.id)).allowed

    _ = await store.add_bonus(user.id, 2)

    first = await store.consume(user.id)
    assert first.allowed
    assert first.bonus_spent
    assert first.usage_id is None
    assert first.user_id == user.id

    second = await store.consume(user.id)
    assert second.allowed
    assert second.bonus_spent

    assert not (await store.consume(user.id)).allowed


async def test_refund_bonus_returns_the_reserve_after_a_failed_access(
    store: AccountStore,
) -> None:
    user = await store.register("alice", _PASSWORD, _EMAIL)
    for _ in range(_LIMIT):
        _ = await store.consume(user.id)
    _ = await store.add_bonus(user.id, 1)
    assert (await store.snapshot(user.id)).remaining == 1

    decision = await store.consume(user.id)
    assert decision.allowed
    assert decision.bonus_spent
    assert (await store.snapshot(user.id)).remaining == 0

    await store.refund_bonus(user.id)

    assert (await store.snapshot(user.id)).remaining == 1


async def test_bonus_is_spent_only_after_the_window_slot_is_unavailable(
    store: AccountStore,
) -> None:
    user = await store.register("alice", _PASSWORD, _EMAIL)
    _ = await store.add_bonus(user.id, 5)

    # A window slot exists, so the first consume spends it, not the bonus.
    decision = await store.consume(user.id)
    assert decision.allowed
    assert not decision.bonus_spent
    assert decision.usage_id is not None
    assert (await store.snapshot(user.id)).remaining == _LIMIT - 1 + 5


async def test_reset_usage_clears_the_window_and_reports_unknown_names() -> None:
    async with opened_store(_store(_policy(limit=5))) as store:
        user = await store.register("reset-user", _PASSWORD, _EMAIL)
        _ = await store.consume(user.id)
        _ = await store.consume(user.id)
        assert (await store.snapshot(user.id)).remaining == 3
        reset = await store.reset_usage("reset-user")
        assert reset is not None
        assert reset.removed == 2
        assert (await store.snapshot(user.id)).remaining == 5
        assert await store.reset_usage("nobody") is None


async def test_snapshot_counts_only_the_current_window(
    store: AccountStore,
    clock: _Clock,
) -> None:
    user = await store.register("alice", _PASSWORD, _EMAIL)
    _ = await store.consume(user.id)
    _ = await store.consume(user.id)

    partial = await store.snapshot(user.id)
    assert partial.limit == _LIMIT
    assert partial.remaining == 1
    # The reset time is only meaningful once the window is exhausted.
    assert partial.reset_after_seconds == 0

    _ = await store.consume(user.id)
    exhausted = await store.snapshot(user.id)
    assert exhausted.remaining == 0
    assert exhausted.reset_after_seconds > 0

    clock.now += _WINDOW + 1
    rolled = await store.snapshot(user.id)
    assert rolled.remaining == _LIMIT
    assert rolled.reset_after_seconds == 0


async def test_initialize_tolerates_tables_that_already_exist() -> None:
    # Every store in the run shares these tables, so preparing them through
    # opened_store also empties whatever the tests before this one left behind.
    async with opened_store(_store(_policy())) as first:
        # Empty windows prove the tables exist and that nothing was carried in.
        assert all(window.stats.users == 0 for window in await first.service_stats())
        # The tables are in place by now, so this only passes if the creation
        # path looks for them: MySQL answers an existing table with a warning,
        # and a warning is an error under test.
        second = _store(_policy())
        try:
            await second.initialize()
            assert (await second.register("alice", _PASSWORD, _EMAIL)).id > 0
        finally:
            await second.close()


async def test_service_stats_reports_every_window_shortest_first(store: AccountStore) -> None:
    windows = await store.service_stats()

    assert [window.key for window in windows] == ["hour", "day", "week", "month", "total"]
    assert all(window.stats == WindowStats(downloads=0, pages=0, users=0) for window in windows)


async def test_accesses_are_split_by_kind_and_counted_once_per_user(
    store: AccountStore,
) -> None:
    first = await store.register("first", _PASSWORD, "first@example.com")
    second = await store.register("second", _PASSWORD, "second@example.com")
    await store.record_access(first.id, AccessKind.FILE)
    await store.record_access(first.id, AccessKind.FILE)
    await store.record_access(second.id, AccessKind.PAGE)

    hour = (await store.service_stats())[0]

    assert hour.key == "hour"
    assert hour.stats == WindowStats(downloads=2, pages=1, users=2)


async def test_windows_are_bounded_by_age(store: AccountStore, clock: _Clock) -> None:
    user = await store.register("member", _PASSWORD, "member@example.com")
    await store.record_access(user.id, AccessKind.FILE)

    clock.now += 2 * 24 * 60 * 60  # Two days on: past the day window, inside the week.
    await store.record_access(user.id, AccessKind.PAGE)

    by_key = {window.key: window.stats for window in await store.service_stats()}

    assert by_key["hour"] == WindowStats(downloads=0, pages=1, users=1)
    assert by_key["day"] == WindowStats(downloads=0, pages=1, users=1)
    assert by_key["week"] == WindowStats(downloads=1, pages=1, users=1)
    assert by_key["total"] == WindowStats(downloads=1, pages=1, users=1)


async def test_only_the_total_window_is_unbounded(store: AccountStore, clock: _Clock) -> None:
    user = await store.register("member", _PASSWORD, "member@example.com")
    await store.record_access(user.id, AccessKind.FILE)

    clock.now += 2_592_001.0  # One second past the published month.

    by_key = {window.key: window.stats for window in await store.service_stats()}

    assert by_key["month"] == WindowStats(downloads=0, pages=0, users=0)
    assert by_key["total"] == WindowStats(downloads=1, pages=0, users=1)


async def test_register_stores_an_unverified_address(store: AccountStore) -> None:
    user = await store.register("alice", _PASSWORD, _EMAIL)

    assert user.email == _EMAIL
    assert user.email_verified is False
    assert await store.authenticate("alice", _PASSWORD) == user


async def test_registering_a_taken_address_is_refused(store: AccountStore) -> None:
    _ = await store.register("alice", _PASSWORD, _EMAIL)

    # The index is case-insensitive, so a lookalike spelling collides as well.
    with pytest.raises(EmailTakenError):
        _ = await store.register("other", _PASSWORD, _EMAIL.upper())


async def test_verify_email_marks_the_account_and_retires_the_token(
    store: AccountStore,
) -> None:
    user = await store.register("alice", _PASSWORD, _EMAIL)
    token = await store.open_verification(user.id, _EMAIL)

    verified = await store.verify_email(token)

    assert verified is not None
    assert verified.id == user.id
    assert verified.email == _EMAIL
    assert verified.email_verified is True

    # The link is single use, and the account now reads as verified.
    assert await store.verify_email(token) is None
    found = await store.authenticate("alice", _PASSWORD)
    assert found is not None
    assert found.email_verified is True


async def test_verify_email_rejects_unknown_and_expired_tokens(
    store: AccountStore,
    clock: _Clock,
) -> None:
    user = await store.register("alice", _PASSWORD, _EMAIL)
    token = await store.open_verification(user.id, _EMAIL)

    assert await store.verify_email("never-issued") is None
    assert await store.verify_email("") is None

    clock.now += _VERIFICATION_TTL + 1
    assert await store.verify_email(token) is None


async def test_a_new_link_retires_the_previous_one(store: AccountStore) -> None:
    user = await store.register("alice", _PASSWORD, _EMAIL)
    first = await store.open_verification(user.id, _EMAIL)
    second = await store.open_verification(user.id, _EMAIL)

    assert await store.verify_email(first) is None
    assert await store.verify_email(second) is not None


async def test_rebinding_an_address_clears_the_verified_state(store: AccountStore) -> None:
    user = await store.register("alice", _PASSWORD, _EMAIL)
    _ = await store.verify_email(await store.open_verification(user.id, _EMAIL))

    _ = await store.open_verification(user.id, "newer@example.com")

    found = await store.authenticate("alice", _PASSWORD)

    assert found is not None
    assert found.email == "newer@example.com"
    assert found.email_verified is False


async def test_rebinding_to_a_taken_address_is_refused(store: AccountStore) -> None:
    alice = await store.register("alice", _PASSWORD, _EMAIL)
    bob = await store.register("bob", _PASSWORD, "bob@example.com")

    with pytest.raises(EmailTakenError):
        _ = await store.open_verification(bob.id, _EMAIL)

    # The refusal rolls back, so the other account keeps its address.
    found = await store.authenticate("alice", _PASSWORD)
    assert found is not None
    assert found.email == _EMAIL
    assert alice.id != bob.id


async def test_resending_within_the_cooldown_is_refused() -> None:
    async with opened_store(_store(_policy(resend=60.0))) as built:
        user = await built.register("alice", _PASSWORD, _EMAIL)
        _ = await built.open_verification(user.id, _EMAIL)

        with pytest.raises(VerificationThrottledError) as refusal:
            _ = await built.open_verification(user.id, _EMAIL)

        assert refusal.value.retry_after_seconds > 0


async def test_discard_account_removes_every_trace(store: AccountStore) -> None:
    user = await store.register("alice", _PASSWORD, _EMAIL)
    token = await store.open_verification(user.id, _EMAIL)
    session = await store.open_session(user.id)

    await store.discard_account(user.id)

    assert await store.authenticate("alice", _PASSWORD) is None
    assert await store.resolve_session(session) is None
    assert await store.verify_email(token) is None
    # The freed name and address can be registered again.
    again = await store.register("alice", _PASSWORD, _EMAIL)
    assert again.id != user.id
