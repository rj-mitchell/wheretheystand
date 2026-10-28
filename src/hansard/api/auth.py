"""Invite-only accounts: argon2 password hashes, opaque session cookies."""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from hansard.store.state import StateDB, utcnow

COOKIE = "hgpt_session"
_ph = PasswordHasher()
_DUMMY_HASH = _ph.hash("timing-equaliser")
# each check deliberately takes about 64 MB and a moment of CPU: only a couple at once, so a flood of sign-in
# attempts cannot exhaust the server's memory
_CHECKING = threading.BoundedSemaphore(2)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_user(state: StateDB, email: str, password: str, admin: bool = False) -> int:
    if len(password) < 10:
        raise ValueError("password must be at least 10 characters")
    with state.connect() as con:
        cur = con.execute("INSERT INTO users (email, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?)",
                          (email.strip().lower(), _ph.hash(password), int(admin), utcnow()))
        return int(cur.lastrowid)


def authenticate(state: StateDB, email: str, password: str) -> dict[str, Any] | None:
    with state.connect() as con:
        row = con.execute("SELECT * FROM users WHERE email = ? AND disabled = 0",
                          (email.strip().lower(),)).fetchone()
    if not _CHECKING.acquire(timeout=20):
        return None
    try:
        _ph.verify(row["password_hash"] if row else _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return None
    finally:
        _CHECKING.release()
    return dict(row) if row else None


def create_session(state: StateDB, user_id: int, days: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(UTC) + timedelta(days=days)).isoformat(timespec="seconds")
    with state.connect() as con:
        con.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                    (_token_hash(token), user_id, utcnow(), expires))
    return token


def user_for_token(state: StateDB, token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    with state.connect() as con:
        row = con.execute(
            """SELECT u.* FROM sessions s JOIN users u ON u.user_id = s.user_id
               WHERE s.token_hash = ? AND s.expires_at > ? AND u.disabled = 0""",
            (_token_hash(token), utcnow())).fetchone()
    return dict(row) if row else None


def end_session(state: StateDB, token: str | None) -> None:
    if token:
        with state.connect() as con:
            con.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


class LoginThrottle:
    """At most ``limit`` failed logins per key in ``window`` seconds."""

    def __init__(self, limit: int = 5, window: float = 900.0):
        self.limit, self.window = limit, window
        self.failures: dict[str, deque[float]] = defaultdict(deque)

    def blocked(self, key: str) -> bool:
        q = self.failures[key]
        now = time.monotonic()
        while q and now - q[0] > self.window:
            q.popleft()
        return len(q) >= self.limit

    def fail(self, key: str) -> None:
        now = time.monotonic()
        self.failures[key].append(now)
        if len(self.failures) > 10_000:  # forget keys with no recent failures
            for k in [k for k, q in self.failures.items() if not q or now - q[-1] > self.window]:
                del self.failures[k]

    def reset(self, key: str) -> None:
        self.failures.pop(key, None)
