"""Filesystem helpers for the private local SQLite database."""

from __future__ import annotations

import os
from pathlib import Path

DATA_DIR_MODE = 0o700
DATABASE_FILE_MODE = 0o600


def ensure_private_directory(path: Path) -> None:
    """Create a data directory with private permissions on POSIX.

    An existing directory is left untouched. This avoids changing permissions of a
    shared parent such as ``/tmp`` when a caller overrides ``PJF_DATA_DIR``.
    """

    if path.is_symlink():
        raise ValueError(f"data directory must not be a symlink: {path}")
    if path.exists():
        if not path.is_dir():
            raise ValueError(f"data directory path is not a directory: {path}")
        return

    path.mkdir(mode=DATA_DIR_MODE, parents=True, exist_ok=True)
    if os.name == "posix":
        path.chmod(DATA_DIR_MODE)


def ensure_private_database_file(path: Path) -> None:
    """Create a database file early and give it private permissions on POSIX.

    SQLite normally creates a new database with the process umask, which is often
    world-readable. Creating and tightening the file before the connection makes
    the privacy expectation explicit. Symlinks are rejected so this helper cannot
    modify an unrelated target.
    """

    ensure_private_directory(path.parent)

    if path.is_symlink():
        raise ValueError(f"database path must not be a symlink: {path}")
    if path.exists():
        if not path.is_file():
            raise ValueError(f"database path is not a file: {path}")
        if os.name == "posix" and path.stat().st_uid == os.geteuid():
            path.chmod(DATABASE_FILE_MODE)
        return

    flags = os.O_CREAT | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, DATABASE_FILE_MODE)
    try:
        if os.name == "posix":
            os.fchmod(descriptor, DATABASE_FILE_MODE)
    finally:
        os.close(descriptor)
