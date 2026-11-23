"""Local bootstrap token and setup session for credential endpoints.

The bootstrap token is generated at startup, written only to a private file in the
data directory, valid once, and expires. Exchanging it creates a session cookie
whose value and CSRF token are stored only as SHA-256 digests in memory.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

BOOTSTRAP_TTL = timedelta(minutes=30)
SESSION_TTL = timedelta(hours=8)
SESSION_COOKIE_NAME = "pjf_setup_session"
BOOTSTRAP_FILENAME = "bootstrap-token"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class IssuedSession:
    cookie_value: str
    csrf_token: str
    expires_at: datetime


class SetupSessionStore:
    """In-memory single-user setup sessions. Lost on restart by design."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bootstrap_digest: str | None = None
        self._bootstrap_expires_at: datetime | None = None
        self._sessions: dict[str, tuple[str, datetime]] = {}

    def issue_bootstrap(self, *, now: datetime, data_dir: Path) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._bootstrap_digest = _digest(token)
            self._bootstrap_expires_at = now + BOOTSTRAP_TTL
        _write_private_file(data_dir / "setup" / BOOTSTRAP_FILENAME, token)
        return token

    def exchange_bootstrap(
        self, token: str, *, now: datetime, data_dir: Path
    ) -> IssuedSession | None:
        """Consume the bootstrap token once. Returns None for any mismatch."""

        with self._lock:
            digest = self._bootstrap_digest
            expires_at = self._bootstrap_expires_at
            self._bootstrap_digest = None
            self._bootstrap_expires_at = None
            if digest is None or expires_at is None or now > expires_at:
                return None
            if not _constant_time_equal(_digest(token), digest):
                return None
            cookie = secrets.token_urlsafe(32)
            csrf = secrets.token_urlsafe(32)
            session_expires = now + SESSION_TTL
            self._sessions = {_digest(cookie): (_digest(csrf), session_expires)}
        _remove_file(data_dir / "setup" / BOOTSTRAP_FILENAME)
        return IssuedSession(cookie_value=cookie, csrf_token=csrf, expires_at=session_expires)

    def validate_session(self, cookie: str | None, *, now: datetime) -> str | None:
        """Return the stored CSRF digest for a live session, else None."""

        if not cookie:
            return None
        with self._lock:
            entry = self._sessions.get(_digest(cookie))
            if entry is None:
                return None
            csrf_digest, expires_at = entry
            if now > expires_at:
                self._sessions.pop(_digest(cookie), None)
                return None
            return csrf_digest

    def verify_csrf(self, cookie: str | None, csrf_header: str | None, *, now: datetime) -> bool:
        csrf_digest = self.validate_session(cookie, now=now)
        if csrf_digest is None or not csrf_header:
            return False
        return _constant_time_equal(_digest(csrf_header), csrf_digest)

    def revoke_all(self) -> None:
        with self._lock:
            self._sessions.clear()


def _write_private_file(path: Path, content: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("bootstrap path must not be a symbolic link")
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _remove_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
