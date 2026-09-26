"""SQLite-backed accounts, sessions, the per-user access quota, and usage stats.

Every public method offloads its blocking ``sqlite3`` work to a worker thread,
matching how :class:`app.cache.ArchiveCache` keeps disk work off the event
loop. Connections are opened per operation rather than pooled: SQLite already
serializes writers through the file lock, so a pool would only add lifetime
bookkeeping. Passwords are stored as salted ``scrypt`` digests, and both session
cookies and email verification tokens are stored as SHA-256 digests, so a
leaked database file still does not yield a usable password, cookie, or link.
"""

import hashlib
import hmac
import secrets
import sqlite3
import time
from collections.abc import Generator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, cast

import anyio

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16
_SESSION_TOKEN_BYTES = 32
_VERIFICATION_TOKEN_BYTES = 32
_SQLITE_TIMEOUT_SECONDS = 10.0
# ``users`` predates email verification, and ``CREATE TABLE IF NOT EXISTS``
# cannot widen a table that already exists, so an older database gets the two
# columns below through ALTER TABLE before anything reads them.
_USER_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("email", "ALTER TABLE users ADD COLUMN email TEXT"),
    ("email_verified_at", "ALTER TABLE users ADD COLUMN email_verified_at REAL"),
)
# Runs after the migrations, never inside ``_SCHEMA``: the index names a column
# an older table only gains during the migration itself. It is partial because
# plenty of legitimate rows carry no address at all, and NULLs must not collide.
_EMAIL_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS users_email
ON users(email COLLATE NOCASE) WHERE email IS NOT NULL
"""
_SESSION_INSERT = """
INSERT INTO sessions(token_hash, user_id, created_at, expires_at)
VALUES(?, ?, ?, ?)
"""
_SESSION_LOOKUP = """
SELECT users.id, users.username, users.email, users.email_verified_at FROM sessions
JOIN users ON users.id = sessions.user_id
WHERE sessions.token_hash = ? AND sessions.expires_at > ?
"""
_VERIFICATION_LOOKUP = """
SELECT users.id, users.username, users.email FROM email_verifications
JOIN users ON users.id = email_verifications.user_id
WHERE email_verifications.token_hash = ? AND email_verifications.expires_at > ?
"""
_CREDENTIAL_LOOKUP = """
SELECT id, username, email, email_verified_at, password_hash FROM users
WHERE username = ?
"""
_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    email TEXT,
    email_verified_at REAL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user_id ON sessions(user_id);
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    used_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_user_used_at ON usage(user_id, used_at);
-- ``usage`` is pruned as the quota window advances, so it can only ever answer
-- questions about the current window. The published statistics span a month and
-- beyond, so every delivered access is also appended here and never removed.
CREATE TABLE IF NOT EXISTS access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS access_log_created_at ON access_log(created_at);
-- One row per outstanding verification mail. The raw token exists only in the
-- link that was sent, so the table stores its digest and an expiry.
CREATE TABLE IF NOT EXISTS email_verifications (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS email_verifications_user_id ON email_verifications(user_id);
"""

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


@dataclass(frozen=True, slots=True)
class QuotaSnapshot:
    """Current quota standing, without spending anything."""

    limit: int
    remaining: int
    reset_after_seconds: int


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


def _registration_conflict(error: sqlite3.IntegrityError) -> Exception:
    """Map a uniqueness violation to the value that actually collided."""
    if "users.email" in str(error):
        return EmailTakenError()
    return UsernameTakenError()


def _row(
    connection: sqlite3.Connection,
    sql: str,
    parameters: tuple[object, ...],
) -> tuple[object, ...] | None:
    """Run a query and return its first row as a plain object tuple."""
    cursor = connection.execute(sql, parameters)
    return cast("tuple[object, ...] | None", cursor.fetchone())


class AccountStore:
    """Persist users, sessions, quota consumption, and usage in one SQLite file."""

    def __init__(self, db_path: Path, policy: StorePolicy) -> None:
        """Bind the store to a database file and its policy."""
        self._db_path: Path = db_path
        self._limit: int = policy.limit
        self._window: float = policy.window_seconds
        self._session_ttl: float = policy.session_ttl_seconds
        self._verification_ttl: float = policy.verification_ttl_seconds
        self._verification_cooldown: float = policy.verification_resend_seconds
        self._ready: bool = False

    async def initialize(self) -> None:
        """Create the parent directory, tables, and indexes if missing.

        Idempotent and safe to call from every request: the tests build the
        application without running the ASGI lifespan, so the store must be
        able to bring itself up on first use.
        """
        if self._ready:
            return
        await anyio.to_thread.run_sync(self._initialize)
        self._ready = True

    def _initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._open()) as connection:
            _ = connection.executescript(_SCHEMA)
            existing = self._user_columns(connection)
            for column, statement in _USER_MIGRATIONS:
                if column not in existing:
                    _ = connection.execute(statement)
            _ = connection.execute(_EMAIL_INDEX)

    def _user_columns(self, connection: sqlite3.Connection) -> frozenset[str]:
        """Return the column names an already existing ``users`` table has."""
        rows = cast(
            "list[tuple[str]]",
            connection.execute("SELECT name FROM pragma_table_info('users')").fetchall(),
        )
        return frozenset(name for (name,) in rows)

    def _open(self) -> sqlite3.Connection:
        # Autocommit mode: transactions are opened explicitly so the
        # read-count-then-insert sequence in _consume takes a write lock.
        connection = sqlite3.connect(
            self._db_path,
            timeout=_SQLITE_TIMEOUT_SECONDS,
            isolation_level=None,
        )
        _ = connection.execute("PRAGMA journal_mode = WAL")
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _ = connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def _transaction(self) -> Generator[sqlite3.Connection]:
        connection = self._open()
        try:
            _ = connection.execute("BEGIN IMMEDIATE")
            yield connection
            _ = connection.execute("COMMIT")
        except BaseException:
            _ = connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    async def register(self, username: str, password: str, email: str) -> User:
        """Create an unverified account, or raise on a taken name or address."""
        return await anyio.to_thread.run_sync(self._register, username, password, email)

    def _register(self, username: str, password: str, email: str) -> User:
        encoded = hash_password(password)
        now = time.time()
        try:
            with self._transaction() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO users(username, password_hash, created_at, email)
                    VALUES(?, ?, ?, ?)
                    """,
                    (username, encoded, now, email),
                )
                user_id = cursor.lastrowid
        except sqlite3.IntegrityError as error:
            raise _registration_conflict(error) from error
        if user_id is None:
            message = "user insert did not return a row id"
            raise RuntimeError(message)
        return User(user_id, username, email, email_verified=False)

    async def discard_account(self, user_id: int) -> None:
        """Drop an account that could not be taken into use."""
        await anyio.to_thread.run_sync(self._discard_account, user_id)

    def _discard_account(self, user_id: int) -> None:
        with self._transaction() as connection:
            _ = connection.execute("DELETE FROM users WHERE id = ?", (user_id,))

    async def open_verification(self, user_id: int, email: str) -> str:
        """Bind an address to an account and issue one verification token."""
        return await anyio.to_thread.run_sync(self._open_verification, user_id, email)

    def _open_verification(self, user_id: int, email: str) -> str:
        raw_token = secrets.token_urlsafe(_VERIFICATION_TOKEN_BYTES)
        now = time.time()
        try:
            with self._transaction() as connection:
                retry_after = self._verification_retry_after(connection, user_id, now)
                if retry_after > 0:
                    raise VerificationThrottledError(retry_after)
                _ = connection.execute(
                    "UPDATE users SET email = ?, email_verified_at = NULL WHERE id = ?",
                    (email, user_id),
                )
                # One outstanding mail per account: a newer link retires the older.
                _ = connection.execute(
                    "DELETE FROM email_verifications WHERE user_id = ?",
                    (user_id,),
                )
                _ = connection.execute(
                    """
                    INSERT INTO email_verifications(token_hash, user_id, created_at, expires_at)
                    VALUES(?, ?, ?, ?)
                    """,
                    (hash_session_token(raw_token), user_id, now, now + self._verification_ttl),
                )
                _ = connection.execute(
                    "DELETE FROM email_verifications WHERE expires_at <= ?",
                    (now,),
                )
        except sqlite3.IntegrityError as error:
            raise EmailTakenError from error
        return raw_token

    def _verification_retry_after(
        self,
        connection: sqlite3.Connection,
        user_id: int,
        now: float,
    ) -> int:
        """Seconds left before this account may be sent another verification mail."""
        row = _row(
            connection,
            "SELECT MAX(created_at) FROM email_verifications WHERE user_id = ?",
            (user_id,),
        )
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
        return await anyio.to_thread.run_sync(self._verify_email, raw_token)

    def _verify_email(self, raw_token: str) -> User | None:
        now = time.time()
        with self._transaction() as connection:
            row = _row(connection, _VERIFICATION_LOOKUP, (hash_session_token(raw_token), now))
            if row is None:
                return None
            user_id = _as_int(row[0])
            _ = connection.execute("DELETE FROM email_verifications WHERE user_id = ?", (user_id,))
            _ = connection.execute(
                "UPDATE users SET email_verified_at = ? WHERE id = ?",
                (now, user_id),
            )
        return User(user_id, _as_str(row[1]), _as_optional_str(row[2]), email_verified=True)

    async def authenticate(self, username: str, password: str) -> User | None:
        """Return the matching account, or None for any mismatch."""
        return await anyio.to_thread.run_sync(self._authenticate, username, password)

    def _authenticate(self, username: str, password: str) -> User | None:
        with closing(self._open()) as connection:
            row = _row(connection, _CREDENTIAL_LOOKUP, (username,))
        if row is None:
            return None
        if not verify_password(password, _as_str(row[4])):
            return None
        return User(_as_int(row[0]), _as_str(row[1]), _as_optional_str(row[2]), row[3] is not None)

    async def open_session(self, user_id: int) -> str:
        """Create a session row and return the raw cookie value."""
        return await anyio.to_thread.run_sync(self._open_session, user_id)

    def _open_session(self, user_id: int) -> str:
        raw_token = secrets.token_urlsafe(_SESSION_TOKEN_BYTES)
        now = time.time()
        with self._transaction() as connection:
            _ = connection.execute(
                _SESSION_INSERT,
                (hash_session_token(raw_token), user_id, now, now + self._session_ttl),
            )
            _ = connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        return raw_token

    async def resolve_session(self, raw_token: str | None) -> User | None:
        """Return the account behind a live session cookie, or None."""
        if not raw_token:
            return None
        return await anyio.to_thread.run_sync(self._resolve_session, raw_token)

    def _resolve_session(self, raw_token: str) -> User | None:
        with closing(self._open()) as connection:
            row = _row(
                connection,
                _SESSION_LOOKUP,
                (hash_session_token(raw_token), time.time()),
            )
        if row is None:
            return None
        return User(_as_int(row[0]), _as_str(row[1]), _as_optional_str(row[2]), row[3] is not None)

    async def close_session(self, raw_token: str | None) -> None:
        """Delete the session row for a cookie value, if any."""
        if not raw_token:
            return
        await anyio.to_thread.run_sync(self._close_session, raw_token)

    def _close_session(self, raw_token: str) -> None:
        digest = hash_session_token(raw_token)
        with self._transaction() as connection:
            _ = connection.execute("DELETE FROM sessions WHERE token_hash = ?", (digest,))

    async def consume(self, user_id: int) -> QuotaDecision:
        """Spend one unit of quota, or report when the window is exhausted."""
        return await anyio.to_thread.run_sync(self._consume, user_id)

    def _consume(self, user_id: int) -> QuotaDecision:
        now = time.time()
        with self._transaction() as connection:
            _ = connection.execute(
                "DELETE FROM usage WHERE user_id = ? AND used_at <= ?",
                (user_id, now - self._window),
            )
            row = _row(
                connection,
                "SELECT COUNT(*), MIN(used_at) FROM usage WHERE user_id = ?",
                (user_id,),
            )
            if row is None:
                message = "count query returned no row"
                raise RuntimeError(message)
            spent = _as_int(row[0])
            if spent >= self._limit:
                return QuotaDecision(
                    allowed=False,
                    limit=self._limit,
                    remaining=0,
                    retry_after_seconds=self._retry_after(row[1], now),
                    usage_id=None,
                )
            cursor = connection.execute(
                "INSERT INTO usage(user_id, used_at) VALUES(?, ?)",
                (user_id, now),
            )
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
        )

    async def refund(self, usage_id: int) -> None:
        """Give back a reserved unit, used when the access did not succeed."""
        await anyio.to_thread.run_sync(self._refund, usage_id)

    def _refund(self, usage_id: int) -> None:
        with self._transaction() as connection:
            _ = connection.execute("DELETE FROM usage WHERE id = ?", (usage_id,))

    async def snapshot(self, user_id: int) -> QuotaSnapshot:
        """Report current quota standing without spending anything."""
        return await anyio.to_thread.run_sync(self._snapshot, user_id)

    def _snapshot(self, user_id: int) -> QuotaSnapshot:
        now = time.time()
        with closing(self._open()) as connection:
            row = _row(
                connection,
                "SELECT COUNT(*), MIN(used_at) FROM usage WHERE user_id = ? AND used_at > ?",
                (user_id, now - self._window),
            )
        if row is None:
            message = "count query returned no row"
            raise RuntimeError(message)
        spent = _as_int(row[0])
        reset = 0 if spent < self._limit else self._retry_after(row[1], now)
        return QuotaSnapshot(self._limit, max(0, self._limit - spent), reset)

    def _retry_after(self, oldest: object, now: float) -> int:
        """Seconds until the oldest spent unit leaves the window."""
        if oldest is None:
            return 0
        return max(1, int(_as_float(oldest) + self._window - now) + 1)

    async def record_access(self, user_id: int, kind: AccessKind) -> None:
        """Append one delivered access to the permanent statistics log."""
        await anyio.to_thread.run_sync(self._record_access, user_id, kind)

    def _record_access(self, user_id: int, kind: AccessKind) -> None:
        with self._transaction() as connection:
            _ = connection.execute(
                "INSERT INTO access_log(user_id, kind, created_at) VALUES(?, ?, ?)",
                (user_id, kind.value, time.time()),
            )

    async def service_stats(self) -> tuple[StatWindow, ...]:
        """Report aggregate usage for every published window, shortest first."""
        return await anyio.to_thread.run_sync(self._service_stats)

    def _service_stats(self) -> tuple[StatWindow, ...]:
        now = time.time()
        with closing(self._open()) as connection:
            return tuple(
                StatWindow(key, self._window_stats(connection, _cutoff(now, offset)))
                for key, offset in _STAT_WINDOWS
            )

    def _window_stats(self, connection: sqlite3.Connection, cutoff: float) -> WindowStats:
        """Count one window in a single pass, splitting files from pages."""
        row = _row(
            connection,
            """
            SELECT COUNT(*),
                   COALESCE(SUM(CASE WHEN kind = ? THEN 1 ELSE 0 END), 0),
                   COUNT(DISTINCT user_id)
            FROM access_log
            WHERE created_at > ?
            """,
            (AccessKind.FILE.value, cutoff),
        )
        if row is None:
            message = "statistics query returned no row"
            raise RuntimeError(message)
        accesses = _as_int(row[0])
        downloads = _as_int(row[1])
        return WindowStats(
            downloads=downloads,
            pages=accesses - downloads,
            users=_as_int(row[2]),
        )


__all__ = [
    "AccessKind",
    "AccountStore",
    "EmailTakenError",
    "QuotaDecision",
    "QuotaSnapshot",
    "StatWindow",
    "StorePolicy",
    "User",
    "UsernameTakenError",
    "VerificationThrottledError",
    "WindowStats",
    "hash_password",
    "hash_session_token",
    "verify_password",
]
