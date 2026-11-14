"""Tests for private data-directory and database-file handling."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from personal_job_finder_api.db.paths import (
    ensure_private_database_file,
    ensure_private_directory,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_new_directory_and_database_are_private(tmp_path) -> None:
    data_dir = tmp_path / "new" / "private-data"
    database_path = data_dir / "personal-job-finder.sqlite3"

    ensure_private_database_file(database_path)

    assert data_dir.is_dir()
    assert database_path.is_file()
    assert _mode(data_dir) == 0o700
    assert _mode(database_path) == 0o600


def test_existing_directory_permissions_are_not_changed(tmp_path) -> None:
    shared_dir = tmp_path / "shared"
    shared_dir.mkdir()
    shared_dir.chmod(0o755)

    ensure_private_directory(shared_dir)

    assert _mode(shared_dir) == 0o755


def test_existing_database_file_is_tightened(tmp_path) -> None:
    database_path = tmp_path / "existing.sqlite3"
    database_path.write_text("", encoding="utf-8")
    database_path.chmod(0o644)

    ensure_private_database_file(database_path)

    assert _mode(database_path) == 0o600


def test_symlinked_directory_is_rejected(tmp_path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "data-link"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        ensure_private_directory(link)


def test_symlinked_database_is_rejected(tmp_path) -> None:
    target = tmp_path / "target.sqlite3"
    target.write_text("", encoding="utf-8")
    link = tmp_path / "database-link.sqlite3"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="symlink"):
        ensure_private_database_file(link)
