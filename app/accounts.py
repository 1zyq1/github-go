"""MySQL-backed accounts, sessions, the per-user access quota, and usage stats.

The connection pool is created lazily on first use and released by the ASGI
lifespan, so importing the application never requires a reachable database while
any request that actually handles an account fails loudly instead of silently.
Every multi-statement operation runs inside an explicit transaction, because
MySQL enables autocommit where SQLite's ``BEGIN IMMEDIATE`` used to serialize
writers: the quota window, the verification cooldown, and token redemption take
the account row with ``SELECT ... FOR UPDATE``, and registration races are left
to the unique indexes on the username and the address.

Passwords are stored as salted ``scrypt`` digests, and both session cookies and
email verification tokens are stored as SHA-256 digests, so a leaked database
still does not yield a usable password, cookie, or link. Table names carry an
optional prefix because MySQL grants are per database and a deployment may not
be allowed to create a second one; the test suite isolates itself with ``test_``
rather than with a separate schema.
"""

# Every statement below is a template whose only interpolated part is that table
# prefix, which ``Settings`` restricts to ``[a-z0-9_]{0,16}`` before it reaches
# this module, and every value travelling with a statement is a bound parameter.
# S608 cannot see through a template, so it is off for this module alone rather
# than for the project; the prefix pattern and the integration tests are what
# actually keep the identifiers safe.
# ruff: noqa: S608

import hashlib
import hmac
import secrets
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

import aiomysql
import anyio

from app.config import Settings

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16
_SESSION_TOKEN_BYTES = 32
_VERIFICATION_TOKEN_BYTES = 32
_CONNECT_TIMEOUT_SECONDS = 10.0
# Every table is utf8mb4 and InnoDB: the engine is what makes the foreign keys
# and ``SELECT ... FOR UPDATE`` below work, and the charset stores any address a
# visitor may supply. Timestamps are ``DOUBLE`` seconds so the quota arithmetic
# stays in the same units the tests and the statistics windows already use.
_TABLE_OPTIONS = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
# The tables the store owns, in the order ``_build_sql`` creates them: the account
# row comes first because every other table references it. The lookup that decides
# which tables are missing binds one placeholder per name, so the two cannot drift.
_TABLE_NAMES = ("users", "sessions", "usage", "email_verifications", "access_log")
_TABLE_PLACEHOLDERS = ", ".join(["%s"] * len(_TABLE_NAMES))
# Reverse creation order, so emptying the tables never leaves a foreign key
# pointing at a row that is already gone.
_PURGE_ORDER = tuple(reversed(_TABLE_NAMES))

StatKey = Literal["hour", "day", "week", "month", "total"]
# Published shortest first, so the report order never depends on the caller.
_STAT_WINDOWS: tuple[tuple[StatKey, float | None], ...] = (
    ("hour", 3_600.0),
    ("day", 86_400.0),
    ("week", 604_800.0),
    ("month", 2_592_000.0),
    ("total", None),
)


class AccessKind(StrEnum):
    """What one metered access actually delivered."""

    FILE = "file"
    PAGE = "page"


class UsernameTakenError(Exception):
    """Signal that registration collided with an existing username."""


class EmailTakenError(Exception):
    """Signal that an address is already bound to another account."""


class VerificationThrottledError(Exception):
    """Signal that a verification mail was sent too recently to resend."""

    def __init__(self, retry_after_seconds: int) -> None:
        """Carry the remaining cooldown to the ``Retry-After`` response header."""
        super().__init__("a verification mail was sent recently")
        self.retry_after_seconds: int = retry_after_seconds


@dataclass(frozen=True, slots=True)
class User:
    """An account together with the state of the address bound to it."""

    id: int
    username: str
    email: str | None
    email_verified: bool


@dataclass(frozen=True, slots=True)
class QuotaDecision:
    """Outcome of one attempt to spend a unit of quota."""

    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int
    usage_id: int | None
    # Which account this unit belongs to and whether it came from the redeemed
    # bonus reserve (so a failed access can refund it back to that reserve).
    user_id: int | None = None
    bonus_spent: bool = False


@dataclass(frozen=True, slots=True)
class QuotaSnapshot:
    """Current quota standing, without spending anything."""

    limit: int
    remaining: int
    reset_after_seconds: int


@dataclass(frozen=True, slots=True)
class UsageReset:
    """Outcome of clearing the units one account has already spent."""

    username: str
    removed: int


@dataclass(frozen=True, slots=True)
class WindowStats:
    """Aggregate usage inside one time window."""

    downloads: int
    pages: int
    users: int


@dataclass(frozen=True, slots=True)
class StatWindow:
    """One published time window together with its aggregates."""

    key: StatKey
    stats: WindowStats


@dataclass(frozen=True, slots=True)
class StorePolicy:
    """The numbers that govern quota, sessions, and email verification."""

    limit: int
    window_seconds: float
    session_ttl_seconds: float
    verification_ttl_seconds: float
    verification_resend_seconds: float


@dataclass(frozen=True, slots=True)
class MySqlTarget:
    """How to reach the accounts database, and which tables to use there."""

    host: str
    port: int
    user: str
    password: str
    database: str
    table_prefix: str
    min_size: int
    max_size: int


def hash_password(password: str) -> str:
    """Derive a self-describing salted scrypt digest for storage."""
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    fields = ("scrypt", str(_SCRYPT_N), str(_SCRYPT_R), str(_SCRYPT_P), salt.hex(), derived.hex())
    return "$".join(fields)


def verify_password(password: str, encoded: str) -> bool:
    """Check a password against a stored digest in constant time."""
    try:
        scheme, n_value, r_value, p_value, salt_hex, hash_hex = encoded.split("$")
        if scheme != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n_value),
            r=int(r_value),
            p=int(p_value),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived, expected)


def hash_session_token(raw_token: str) -> str:
    """Return the storage digest for a session cookie or verification token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _as_int(value: object) -> int:
    if not isinstance(value, int):
        message = f"expected an integer column, got {type(value).__name__}"
        raise TypeError(message)
    return value


def _as_str(value: object) -> str:
    if not isinstance(value, str):
        message = f"expected a text column, got {type(value).__name__}"
        raise TypeError(message)
    return value


def _as_optional_str(value: object) -> str | None:
    """Read a nullable text column."""
    if value is None:
        return None
    return _as_str(value)


def _as_float(value: object) -> float:
    if not isinstance(value, (int, float)):
        message = f"expected a numeric column, got {type(value).__name__}"
        raise TypeError(message)
    return float(value)


def _cutoff(now: float, offset: float | None) -> float:
    """Return the oldest timestamp a window includes; ``None`` means all time."""
    return 0.0 if offset is None else now - offset


def _registration_conflict(error: aiomysql.IntegrityError, prefix: str) -> Exception:
    """Map a uniqueness violation to the value that actually collided.

    MySQL reports the offending index by name, which is the only reliable way to
    tell the username index from the address index without a second query.
    """
    if f"{prefix}users_email" in str(error):
        return EmailTakenError()
    return UsernameTakenError()


@dataclass(frozen=True, slots=True)
class _Sql:
    """Every statement the store runs, rendered against one table prefix."""

    existing_tables: str
    existing_users_columns: str
    alter_add_bonus: str
    purge: tuple[str, ...]
    schema: tuple[str, ...]
    insert_user: str
    insert_sso_user: str
    delete_user: str
    credential_lookup: str
    user_by_name: str
    user_by_email: str
    lock_account: str
    bonus_read: str
    add_bonus_units: str
    spend_bonus_unit: str
    bind_email: str
    mark_verified: str
    latest_verification: str
    delete_account_verifications: str
    insert_verification: str
    prune_verifications: str
    verification_lookup: str
    insert_session: str
    session_lookup: str
    delete_session: str
    prune_sessions: str
    prune_usage: str
    count_usage: str
    count_window_usage: str
    insert_usage: str
    delete_usage: str
    clear_usage: str
    insert_access: str
    window_stats: str


def _build_sql(prefix: str) -> _Sql:
    """Render every statement for one prefix, which is empty in production.

    Table names are quoted because ``usage`` is a reserved word in MySQL, so an
    unprefixed schema cannot even be created without the backticks.
    """

    def quoted(name: str) -> str:
        return f"`{prefix}{name}`"

    users = quoted("users")
    sessions = quoted("sessions")
    usage = quoted("usage")
    verifications = quoted("email_verifications")
    access_log = quoted("access_log")
    return _Sql(
        existing_tables=f"""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = DATABASE()
              AND table_name IN ({_TABLE_PLACEHOLDERS})
            """,
        existing_users_columns="""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = %s
            """,
        alter_add_bonus=f"ALTER TABLE {users} ADD COLUMN bonus_units INT NOT NULL DEFAULT 0",
        purge=tuple(f"DELETE FROM {quoted(name)}" for name in _PURGE_ORDER),
        schema=(
            f"""
            CREATE TABLE IF NOT EXISTS {users} (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                username VARCHAR(32) COLLATE utf8mb4_unicode_ci NOT NULL,
                email VARCHAR(254) COLLATE utf8mb4_unicode_ci NULL,
                password_hash VARCHAR(255) NOT NULL,
                created_at DOUBLE NOT NULL,
                email_verified_at DOUBLE NULL,
                bonus_units INT NOT NULL DEFAULT 0,
                PRIMARY KEY (id),
                UNIQUE KEY {prefix}users_username (username),
                UNIQUE KEY {prefix}users_email (email)
            ) {_TABLE_OPTIONS}
            """,
            f"""
            CREATE TABLE IF NOT EXISTS {sessions} (
                token_hash CHAR(64) NOT NULL,
                user_id BIGINT UNSIGNED NOT NULL,
                created_at DOUBLE NOT NULL,
                expires_at DOUBLE NOT NULL,
                PRIMARY KEY (token_hash),
                KEY {prefix}sessions_user_id (user_id),
                CONSTRAINT {prefix}sessions_user_id_fk
                    FOREIGN KEY (user_id) REFERENCES {users} (id) ON DELETE CASCADE
            ) {_TABLE_OPTIONS}
            """,
            f"""
            CREATE TABLE IF NOT EXISTS {usage} (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                user_id BIGINT UNSIGNED NOT NULL,
                used_at DOUBLE NOT NULL,
                PRIMARY KEY (id),
                KEY {prefix}usage_user_id_used_at (user_id, used_at),
                CONSTRAINT {prefix}usage_user_id_fk
                    FOREIGN KEY (user_id) REFERENCES {users} (id) ON DELETE CASCADE
            ) {_TABLE_OPTIONS}
            """,
            f"""
            CREATE TABLE IF NOT EXISTS {verifications} (
                token_hash CHAR(64) NOT NULL,
                user_id BIGINT UNSIGNED NOT NULL,
                created_at DOUBLE NOT NULL,
                expires_at DOUBLE NOT NULL,
                PRIMARY KEY (token_hash),
                KEY {prefix}verifications_user_id (user_id),
                CONSTRAINT {prefix}verifications_user_id_fk
                    FOREIGN KEY (user_id) REFERENCES {users} (id) ON DELETE CASCADE
            ) {_TABLE_OPTIONS}
            """,
            f"""
            CREATE TABLE IF NOT EXISTS {access_log} (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                user_id BIGINT UNSIGNED NOT NULL,
                kind VARCHAR(16) NOT NULL,
                created_at DOUBLE NOT NULL,
                PRIMARY KEY (id),
                KEY {prefix}access_log_created_at (created_at),
                CONSTRAINT {prefix}access_log_user_id_fk
                    FOREIGN KEY (user_id) REFERENCES {users} (id) ON DELETE CASCADE
            ) {_TABLE_OPTIONS}
            """,
        ),
        insert_user=f"""
            INSERT INTO {users}(username, password_hash, created_at, email)
            VALUES(%s, %s, %s, %s)
            """,
        insert_sso_user=f"""
            INSERT INTO {users}(username, email, password_hash, created_at, email_verified_at)
            VALUES(%s, %s, %s, %s, %s)
            """,
        delete_user=f"DELETE FROM {users} WHERE id = %s",
        credential_lookup=f"""
            SELECT id, username, email, email_verified_at, password_hash
            FROM {users}
            WHERE username = %s
            """,
        user_by_name=f"SELECT id FROM {users} WHERE username = %s",
        user_by_email=f"""
            SELECT id, username, email, email_verified_at
            FROM {users}
            WHERE email = %s
            """,
        lock_account=f"SELECT id FROM {users} WHERE id = %s FOR UPDATE",
        bonus_read=f"SELECT bonus_units FROM {users} WHERE id = %s",
        add_bonus_units=f"UPDATE {users} SET bonus_units = bonus_units + %s WHERE id = %s",
        spend_bonus_unit=(
            f"UPDATE {users} SET bonus_units = bonus_units - 1 WHERE id = %s AND bonus_units > 0"
        ),
        bind_email=f"UPDATE {users} SET email = %s, email_verified_at = NULL WHERE id = %s",
        mark_verified=f"UPDATE {users} SET email_verified_at = %s WHERE id = %s",
        latest_verification=f"""
            SELECT MAX(created_at) FROM {verifications} WHERE user_id = %s
            """,
        delete_account_verifications=f"""
            DELETE FROM {verifications} WHERE user_id = %s
            """,
        insert_verification=f"""
            INSERT INTO {verifications}(token_hash, user_id, created_at, expires_at)
            VALUES(%s, %s, %s, %s)
            """,
        prune_verifications=f"""
            DELETE FROM {verifications} WHERE expires_at <= %s
            """,
        verification_lookup=f"""
            SELECT u.id, u.username, u.email
            FROM {verifications} AS v
            JOIN {users} AS u ON u.id = v.user_id
            WHERE v.token_hash = %s
              AND v.expires_at > %s
            FOR UPDATE
            """,
        insert_session=f"""
            INSERT INTO {sessions}(token_hash, user_id, created_at, expires_at)
            VALUES(%s, %s, %s, %s)
            """,
        session_lookup=f"""
            SELECT u.id, u.username, u.email, u.email_verified_at
            FROM {sessions} AS s
            JOIN {users} AS u ON u.id = s.user_id
            WHERE s.token_hash = %s AND s.expires_at > %s
            """,
        delete_session=f"DELETE FROM {sessions} WHERE token_hash = %s",
        prune_sessions=f"DELETE FROM {sessions} WHERE expires_at <= %s",
        prune_usage=f"""
            DELETE FROM {usage} WHERE user_id = %s AND used_at <= %s
            """,
        count_usage=f"""
            SELECT COUNT(*), MIN(used_at) FROM {usage} WHERE user_id = %s
            """,
        count_window_usage=f"""
            SELECT COUNT(*), MIN(used_at) FROM {usage}
            WHERE user_id = %s AND used_at > %s
            """,
        insert_usage=f"INSERT INTO {usage}(user_id, used_at) VALUES(%s, %s)",
        delete_usage=f"DELETE FROM {usage} WHERE id = %s",
        clear_usage=f"DELETE FROM {usage} WHERE user_id = %s",
        insert_access=f"""
            INSERT INTO {access_log}(user_id, kind, created_at)
            VALUES(%s, %s, %s)
            """,
        window_stats=f"""
            SELECT COUNT(*),
                   CAST(COALESCE(SUM(kind = %s), 0) AS SIGNED),
                   COUNT(DISTINCT user_id)
            FROM {access_log}
            WHERE created_at > %s
            """,
    )


class AccountStore:
    """Persist users, sessions, quota consumption, and usage in MySQL."""

    def __init__(self, target: MySqlTarget, policy: StorePolicy) -> None:
        """Bind the store to a database target and its policy."""
        self._target: MySqlTarget = target
        self._sql: _Sql = _build_sql(target.table_prefix)
        self._limit: int = policy.limit
        self._window: float = policy.window_seconds
        self._session_ttl: float = policy.session_ttl_seconds
        self._verification_ttl: float = policy.verification_ttl_seconds
        self._verification_cooldown: float = policy.verification_resend_seconds
        self._pool: aiomysql.Pool | None = None
        self._init_lock: anyio.Lock | None = None

    async def initialize(self) -> None:
        """Create the pool and the tables if they are missing.

        Idempotent and safe to call from every request: the tests build the
        application without running the ASGI lifespan, so the store has to be
        able to bring itself up on first use. The lock is created here rather
        than in ``__init__`` so a store can also be built outside a running
        event loop, and a failed startup closes the pool it opened so a later
        request can retry instead of inheriting a broken one.
        """
        if self._pool is not None:
            return
        lock = self._init_lock
        if lock is None:
            lock = anyio.Lock()
            self._init_lock = lock
        async with lock:
            if self._pool is None:
                await self._open_pool()

    async def _open_pool(self) -> None:
        target = self._target
        pool = await aiomysql.create_pool(
            host=target.host,
            port=target.port,
            user=target.user,
            password=target.password,
            db=target.database,
            charset="utf8mb4",
            autocommit=True,
            minsize=target.min_size,
            maxsize=target.max_size,
            connect_timeout=_CONNECT_TIMEOUT_SECONDS,
        )
        try:
            await self._create_schema(pool)
        except BaseException:
            pool.close()
            await pool.wait_closed()
            raise
        self._pool = pool

    async def _create_schema(self, pool: aiomysql.Pool) -> None:
        """Create whichever tables the prefix does not have yet.

        The names are looked up first rather than relying on ``CREATE TABLE IF
        NOT EXISTS``, which answers an existing table with a warning, and a
        warning is an error in this project's test configuration.
        """
        names = tuple(f"{self._target.table_prefix}{name}" for name in _TABLE_NAMES)
        async with pool.acquire() as connection, connection.cursor() as cursor:
            _ = await cursor.execute(self._sql.existing_tables, names)
            existing = {_as_str(row[0]) for row in await cursor.fetchall()}
            for name, statement in zip(names, self._sql.schema, strict=True):
                if name in existing:
                    continue
                _ = await cursor.execute(statement)
            # A `users` table created before the redeemed-bonus reserve still needs
            # the column; fresh installs already have it (CREATE TABLE sets DEFAULT 0).
            users_table = f"{self._target.table_prefix}users"
            _ = await cursor.execute(self._sql.existing_users_columns, (users_table,))
            columns = {_as_str(column[0]) for column in await cursor.fetchall()}
            if "bonus_units" not in columns:
                _ = await cursor.execute(self._sql.alter_add_bonus)

    async def close(self) -> None:
        """Release the pool; safe to call when it was never opened."""
        pool = self._pool
        if pool is None:
            return
        self._pool = None
        pool.close()
        await pool.wait_closed()

    async def purge(self) -> None:
        """Delete every row this store owns.

        Accounts live in the shared product database rather than one of their
        own, so the test suite isolates itself with a table prefix and empties
        those tables instead of starting from an empty file. Deletion follows
        the reverse of the creation order, so no foreign key is ever left
        pointing at a row that is already gone.
        """
        _ = self._require_pool()
        async with self._transaction() as cursor:
            for statement in self._sql.purge:
                _ = await cursor.execute(statement)

    def _require_pool(self) -> aiomysql.Pool:
        """Return the pool, refusing to run before ``initialize`` succeeded."""
        pool = self._pool
        if pool is None:
            message = "the account store was used before initialize()"
            raise RuntimeError(message)
        return pool

    @asynccontextmanager
    async def _transaction(self) -> AsyncGenerator[aiomysql.Cursor]:
        """Yield a cursor inside one explicit transaction, on a pooled connection.

        A cursor rather than a connection, so a caller cannot start a nested
        transaction by accident. Committing here rather than in each caller also
        means a failure anywhere in the body rolls the whole sequence back.
        """
        pool = self._require_pool()
        async with pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    yield cursor
            except BaseException:
                await connection.rollback()
                raise
            await connection.commit()

    @asynccontextmanager
    async def _reading(self) -> AsyncGenerator[aiomysql.Cursor]:
        """Yield a cursor for a read that needs no transaction of its own."""
        pool = self._require_pool()
        async with pool.acquire() as connection, connection.cursor() as cursor:
            yield cursor

    async def _lock_account(self, cursor: aiomysql.Cursor, user_id: int) -> None:
        """Take the account row for this transaction, serializing its writers."""
        _ = await cursor.execute(self._sql.lock_account, (user_id,))
        _ = await cursor.fetchone()

    async def register(self, username: str, password: str, email: str) -> User:
        """Create an unverified account, or raise on a taken name or address."""
        # scrypt is deliberately slow, so the digest stays off the event loop.
        encoded = await anyio.to_thread.run_sync(hash_password, password)
        now = time.time()
        try:
            async with self._transaction() as cursor:
                _ = await cursor.execute(
                    self._sql.insert_user,
                    (username, encoded, now, email),
                )
                user_id = cursor.lastrowid
        except aiomysql.IntegrityError as error:
            raise _registration_conflict(error, self._target.table_prefix) from error
        if user_id is None:
            message = "user insert did not return a row id"
            raise RuntimeError(message)
        return User(user_id, username, email, email_verified=False)

    async def discard_account(self, user_id: int) -> None:
        """Drop an account that could not be taken into use."""
        async with self._transaction() as cursor:
            _ = await cursor.execute(self._sql.delete_user, (user_id,))

    async def open_verification(self, user_id: int, email: str) -> str:
        """Bind an address to an account and issue one verification token."""
        raw_token = secrets.token_urlsafe(_VERIFICATION_TOKEN_BYTES)
        now = time.time()
        try:
            async with self._transaction() as cursor:
                await self._lock_account(cursor, user_id)
                retry_after = await self._verification_retry_after(cursor, user_id, now)
                if retry_after > 0:
                    raise VerificationThrottledError(retry_after)
                _ = await cursor.execute(self._sql.bind_email, (email, user_id))
                # One outstanding mail per account: a newer link retires the older.
                _ = await cursor.execute(self._sql.delete_account_verifications, (user_id,))
                _ = await cursor.execute(
                    self._sql.insert_verification,
                    (hash_session_token(raw_token), user_id, now, now + self._verification_ttl),
                )
                _ = await cursor.execute(self._sql.prune_verifications, (now,))
        except aiomysql.IntegrityError as error:
            raise EmailTakenError from error
        return raw_token

    async def _verification_retry_after(
        self,
        cursor: aiomysql.Cursor,
        user_id: int,
        now: float,
    ) -> int:
        """Seconds left before this account may be sent another verification mail."""
        _ = await cursor.execute(self._sql.latest_verification, (user_id,))
        row = await cursor.fetchone()
        if row is None or row[0] is None:
            return 0
        remaining = _as_float(row[0]) + self._verification_cooldown - now
        if remaining <= 0:
            return 0
        return max(1, int(remaining) + 1)

    async def verify_email(self, raw_token: str) -> User | None:
        """Mark the address behind a token verified, or return None."""
        if not raw_token:
            return None
        now = time.time()
        async with self._transaction() as cursor:
            _ = await cursor.execute(
                self._sql.verification_lookup,
                (hash_session_token(raw_token), now),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            user_id = _as_int(row[0])
            _ = await cursor.execute(self._sql.delete_account_verifications, (user_id,))
            _ = await cursor.execute(self._sql.mark_verified, (now, user_id))
        return User(user_id, _as_str(row[1]), _as_optional_str(row[2]), email_verified=True)

    async def authenticate(self, username: str, password: str) -> User | None:
        """Return the matching account, or None for any mismatch."""
        async with self._reading() as cursor:
            _ = await cursor.execute(self._sql.credential_lookup, (username,))
            row = await cursor.fetchone()
        if row is None:
            return None
        encoded = _as_str(row[4])
        # scrypt is deliberately slow, so the check stays off the event loop.
        if not await anyio.to_thread.run_sync(verify_password, password, encoded):
            return None
        verified = row[3] is not None
        return User(_as_int(row[0]), _as_str(row[1]), _as_optional_str(row[2]), verified)

    async def find_by_email(self, email: str) -> User | None:
        """Return the account bound to an address, or None."""
        async with self._reading() as cursor:
            _ = await cursor.execute(self._sql.user_by_email, (email,))
            row = await cursor.fetchone()
        if row is None:
            return None
        return User(_as_int(row[0]), _as_str(row[1]), _as_optional_str(row[2]), row[3] is not None)

    async def open_sso_account(self, email: str, base_username: str) -> User:
        """Find an account by address or create a verified one for SSO sign-in.

        A partner vouches for the address, so a linked account is treated as
        verified even when it still had to confirm locally. ``base_username`` must
        already satisfy this service's username rules; a taken name gets a numeric
        suffix until one is free.
        """
        existing = await self.find_by_email(email)
        if existing is not None:
            if not existing.email_verified:
                now = time.time()
                async with self._transaction() as cursor:
                    _ = await cursor.execute(self._sql.mark_verified, (now, existing.id))
                existing = User(existing.id, existing.username, existing.email, email_verified=True)
            return existing

        # scrypt is deliberately slow, so the digest stays off the event loop.
        encoded = await anyio.to_thread.run_sync(hash_password, secrets.token_urlsafe(24))
        now = time.time()
        for attempt in range(20):
            candidate = base_username if attempt == 0 else f"{base_username}_{attempt + 1}"
            try:
                async with self._transaction() as cursor:
                    _ = await cursor.execute(self._sql.user_by_name, (candidate,))
                    if await cursor.fetchone() is not None:
                        continue
                    _ = await cursor.execute(
                        self._sql.insert_sso_user,
                        (candidate, email, encoded, now, now),
                    )
                    user_id = cursor.lastrowid
            except aiomysql.IntegrityError as error:
                if f"{self._target.table_prefix}users_email" in str(error):
                    # A concurrent SSO sign-in created the account between the
                    # lookup above and this insert; re-read the winner.
                    winner = await self.find_by_email(email)
                    if winner is not None:
                        return winner
                # A username collision raced us; try the next candidate.
                continue
            if user_id is None:
                message = "user insert did not return a row id"
                raise RuntimeError(message)
            return User(user_id, candidate, email, email_verified=True)
        message = "could not allocate a unique username"
        raise RuntimeError(message)

    async def open_session(self, user_id: int) -> str:
        """Create a session row and return the raw cookie value."""
        raw_token = secrets.token_urlsafe(_SESSION_TOKEN_BYTES)
        now = time.time()
        async with self._transaction() as cursor:
            _ = await cursor.execute(
                self._sql.insert_session,
                (hash_session_token(raw_token), user_id, now, now + self._session_ttl),
            )
            _ = await cursor.execute(self._sql.prune_sessions, (now,))
        return raw_token

    async def resolve_session(self, raw_token: str | None) -> User | None:
        """Return the account behind a live session cookie, or None."""
        if not raw_token:
            return None
        async with self._reading() as cursor:
            _ = await cursor.execute(
                self._sql.session_lookup,
                (hash_session_token(raw_token), time.time()),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        verified = row[3] is not None
        return User(_as_int(row[0]), _as_str(row[1]), _as_optional_str(row[2]), verified)

    async def close_session(self, raw_token: str | None) -> None:
        """Delete the session row for a cookie value, if any."""
        if not raw_token:
            return
        digest = hash_session_token(raw_token)
        async with self._transaction() as cursor:
            _ = await cursor.execute(self._sql.delete_session, (digest,))

    async def consume(self, user_id: int) -> QuotaDecision:
        """Spend one unit of quota: a window slot first, then a redeemed bonus unit."""
        now = time.time()
        async with self._transaction() as cursor:
            await self._lock_account(cursor, user_id)
            _ = await cursor.execute(self._sql.prune_usage, (user_id, now - self._window))
            _ = await cursor.execute(self._sql.count_usage, (user_id,))
            row = await cursor.fetchone()
            if row is None:
                message = "count query returned no row"
                raise RuntimeError(message)
            spent = _as_int(row[0])
            if spent < self._limit:
                _ = await cursor.execute(self._sql.insert_usage, (user_id, now))
                usage_id = cursor.lastrowid
                if usage_id is None:
                    message = "usage insert did not return a row id"
                    raise RuntimeError(message)
                return QuotaDecision(
                    allowed=True,
                    limit=self._limit,
                    remaining=self._limit - spent - 1,
                    retry_after_seconds=0,
                    usage_id=usage_id,
                    user_id=user_id,
                    bonus_spent=False,
                )
            # The window is full: fall back to a redeemed bonus unit, if any.
            _ = await cursor.execute(self._sql.bonus_read, (user_id,))
            bonus_row = await cursor.fetchone()
            bonus = _as_int(bonus_row[0]) if bonus_row is not None else 0
            if bonus > 0:
                affected = await cursor.execute(self._sql.spend_bonus_unit, (user_id,))
                if affected > 0:
                    return QuotaDecision(
                        allowed=True,
                        limit=self._limit,
                        remaining=bonus - 1,
                        retry_after_seconds=0,
                        usage_id=None,
                        user_id=user_id,
                        bonus_spent=True,
                    )
            return QuotaDecision(
                allowed=False,
                limit=self._limit,
                remaining=0,
                retry_after_seconds=self._retry_after(row[1], now),
                usage_id=None,
            )

    async def add_bonus(self, user_id: int, units: int) -> int:
        """Add redeemed bonus units to an account; return the new reserve.

        Bonus units are spent only once the sliding window is full, so this is
        how a blog-credit redemption becomes extra usable attempts.
        """
        async with self._transaction() as cursor:
            _ = await cursor.execute(self._sql.add_bonus_units, (units, user_id))
            _ = await cursor.execute(self._sql.bonus_read, (user_id,))
            row = await cursor.fetchone()
        return _as_int(row[0]) if row is not None else 0

    async def refund_bonus(self, user_id: int) -> None:
        """Return one bonus unit after a failed access, mirroring ``refund``."""
        _ = await self.add_bonus(user_id, 1)

    async def refund(self, usage_id: int) -> None:
        """Give back a reserved unit, used when the access did not succeed."""
        async with self._transaction() as cursor:
            _ = await cursor.execute(self._sql.delete_usage, (usage_id,))

    async def snapshot(self, user_id: int) -> QuotaSnapshot:
        """Report current quota standing (window remaining + bonus) without spending."""
        now = time.time()
        async with self._reading() as cursor:
            _ = await cursor.execute(
                self._sql.count_window_usage,
                (user_id, now - self._window),
            )
            row = await cursor.fetchone()
            _ = await cursor.execute(self._sql.bonus_read, (user_id,))
            bonus_row = await cursor.fetchone()
        if row is None:
            message = "count query returned no row"
            raise RuntimeError(message)
        spent = _as_int(row[0])
        bonus = _as_int(bonus_row[0]) if bonus_row is not None else 0
        reset = 0 if spent < self._limit else self._retry_after(row[1], now)
        return QuotaSnapshot(self._limit, max(0, self._limit - spent) + bonus, reset)

    def _retry_after(self, oldest: object, now: float) -> int:
        """Seconds until the oldest spent unit leaves the window."""
        if oldest is None:
            return 0
        return max(1, int(_as_float(oldest) + self._window - now) + 1)

    async def reset_usage(self, username: str) -> UsageReset | None:
        """Clear every unit one account has spent, or report an unknown name.

        Only the sliding window table is touched. The permanent statistics log
        keeps the accesses that were already delivered, so handing one account a
        fresh allowance never rewrites the published numbers. The account row is
        locked first, so a reset cannot interleave with a concurrent spend.
        """
        async with self._transaction() as cursor:
            _ = await cursor.execute(self._sql.user_by_name, (username,))
            row = await cursor.fetchone()
            if row is None:
                return None
            user_id = _as_int(row[0])
            await self._lock_account(cursor, user_id)
            _ = await cursor.execute(self._sql.clear_usage, (user_id,))
            removed = cursor.rowcount
        return UsageReset(username=username, removed=removed)

    async def record_access(self, user_id: int, kind: AccessKind) -> None:
        """Append one delivered access to the permanent statistics log."""
        async with self._transaction() as cursor:
            _ = await cursor.execute(
                self._sql.insert_access,
                (user_id, kind.value, time.time()),
            )

    async def service_stats(self) -> tuple[StatWindow, ...]:
        """Report aggregate usage for every published window, shortest first."""
        now = time.time()
        windows: list[StatWindow] = []
        async with self._reading() as cursor:
            for key, offset in _STAT_WINDOWS:
                stats = await self._window_stats(cursor, _cutoff(now, offset))
                windows.append(StatWindow(key, stats))
        return tuple(windows)

    async def _window_stats(self, cursor: aiomysql.Cursor, cutoff: float) -> WindowStats:
        """Count one window in a single pass, splitting files from pages."""
        _ = await cursor.execute(self._sql.window_stats, (AccessKind.FILE.value, cutoff))
        row = await cursor.fetchone()
        if row is None:
            message = "statistics query returned no row"
            raise RuntimeError(message)
        accesses = _as_int(row[0])
        downloads = _as_int(row[1])
        return WindowStats(downloads=downloads, pages=accesses - downloads, users=_as_int(row[2]))


def mysql_target(settings: Settings) -> MySqlTarget:
    """Return the connection target the configured database describes."""
    return MySqlTarget(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        table_prefix=settings.mysql_table_prefix,
        min_size=settings.mysql_pool_min_size,
        max_size=settings.mysql_pool_max_size,
    )


def build_account_store(settings: Settings) -> AccountStore:
    """Build the store for the configured database, pool, quota, and mail policy."""
    return AccountStore(
        mysql_target(settings),
        StorePolicy(
            limit=settings.usage_limit,
            window_seconds=float(settings.usage_window_seconds),
            session_ttl_seconds=float(settings.session_ttl_seconds),
            verification_ttl_seconds=float(settings.verification_ttl_seconds),
            verification_resend_seconds=float(settings.verification_resend_seconds),
        ),
    )


__all__ = [
    "AccessKind",
    "AccountStore",
    "EmailTakenError",
    "MySqlTarget",
    "QuotaDecision",
    "QuotaSnapshot",
    "StatWindow",
    "StorePolicy",
    "UsageReset",
    "User",
    "UsernameTakenError",
    "VerificationThrottledError",
    "WindowStats",
    "build_account_store",
    "hash_password",
    "hash_session_token",
    "mysql_target",
    "verify_password",
]
