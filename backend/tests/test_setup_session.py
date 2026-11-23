"""Offline tests for bootstrap exchange, session expiry, and CSRF checks."""

from __future__ import annotations

import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

from personal_job_finder_api.api.setup_session import (
    BOOTSTRAP_FILENAME,
    SetupSessionStore,
)

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def test_bootstrap_file_is_private_and_removed_after_exchange(tmp_path: Path) -> None:
    store = SetupSessionStore()
    token = store.issue_bootstrap(now=NOW, data_dir=tmp_path)

    path = tmp_path / "setup" / BOOTSTRAP_FILENAME
    assert path.read_text(encoding="utf-8") == token
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    issued = store.exchange_bootstrap(token, now=NOW, data_dir=tmp_path)
    assert issued is not None
    assert not path.exists()


def test_bootstrap_token_is_single_use(tmp_path: Path) -> None:
    store = SetupSessionStore()
    token = store.issue_bootstrap(now=NOW, data_dir=tmp_path)

    assert store.exchange_bootstrap(token, now=NOW, data_dir=tmp_path) is not None
    assert store.exchange_bootstrap(token, now=NOW, data_dir=tmp_path) is None


def test_wrong_bootstrap_token_is_rejected_and_burns_the_token(tmp_path: Path) -> None:
    store = SetupSessionStore()
    token = store.issue_bootstrap(now=NOW, data_dir=tmp_path)

    assert store.exchange_bootstrap("wrong", now=NOW, data_dir=tmp_path) is None
    assert store.exchange_bootstrap(token, now=NOW, data_dir=tmp_path) is None


def test_expired_bootstrap_token_is_rejected(tmp_path: Path) -> None:
    store = SetupSessionStore()
    token = store.issue_bootstrap(now=NOW, data_dir=tmp_path)
    later = NOW + timedelta(minutes=31)

    assert store.exchange_bootstrap(token, now=later, data_dir=tmp_path) is None


def test_session_requires_matching_csrf_and_expires(tmp_path: Path) -> None:
    store = SetupSessionStore()
    token = store.issue_bootstrap(now=NOW, data_dir=tmp_path)
    issued = store.exchange_bootstrap(token, now=NOW, data_dir=tmp_path)
    assert issued is not None

    assert store.verify_csrf(issued.cookie_value, issued.csrf_token, now=NOW)
    assert not store.verify_csrf(issued.cookie_value, "not-the-token", now=NOW)
    assert not store.verify_csrf(issued.cookie_value, None, now=NOW)
    assert not store.verify_csrf(None, issued.csrf_token, now=NOW)

    expired = NOW + timedelta(hours=9)
    assert not store.verify_csrf(issued.cookie_value, issued.csrf_token, now=expired)


def test_new_bootstrap_exchange_replaces_previous_session(tmp_path: Path) -> None:
    store = SetupSessionStore()
    first = store.issue_bootstrap(now=NOW, data_dir=tmp_path)
    old = store.exchange_bootstrap(first, now=NOW, data_dir=tmp_path)
    second = store.issue_bootstrap(now=NOW, data_dir=tmp_path)
    store.exchange_bootstrap(second, now=NOW, data_dir=tmp_path)

    assert old is not None
    assert not store.verify_csrf(old.cookie_value, old.csrf_token, now=NOW)
