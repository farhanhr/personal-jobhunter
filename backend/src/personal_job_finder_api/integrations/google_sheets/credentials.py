"""Private local file storage for the Google Service Account key.

The key is written with mode ``0600`` into a ``0700`` directory, replaced
atomically, and symlinks are refused so a write cannot be redirected elsewhere.
Key material never belongs in SQLite, outbox payloads, logs, or the repository.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


class CredentialStoreError(Exception):
    """Raised when the credential file cannot be read or written safely."""


class FileCredentialStore:
    """Store a credential in a private local file with mode ``0600``."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> str | None:
        if self._path.is_symlink():
            raise CredentialStoreError("The Google credential file must not be a symbolic link.")
        try:
            stat_result = self._path.stat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise CredentialStoreError("The Google credential file could not be read.") from exc
        if stat_result.st_mode & 0o077:
            try:
                os.chmod(self._path, 0o600)
            except OSError as exc:
                raise CredentialStoreError(
                    "The Google credential file permissions could not be restricted."
                ) from exc
        try:
            return self._path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise CredentialStoreError("The Google credential file could not be read.") from exc

    def save(self, value: str) -> None:
        if self._path.is_symlink():
            raise CredentialStoreError("The Google credential file must not be a symbolic link.")
        parent = self._path.parent
        if parent.is_symlink():
            raise CredentialStoreError(
                "The Google credential directory must not be a symbolic link."
            )
        try:
            parent_existed = parent.exists()
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not parent_existed:
                os.chmod(parent, 0o700)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=parent,
                prefix=".google-service-account-",
                suffix=".tmp",
            )
            temporary_path = Path(temporary_name)
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(value)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, self._path)
            except BaseException:
                temporary_path.unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise CredentialStoreError("The Google credential file could not be written.") from exc

    def delete(self) -> None:
        try:
            self._path.unlink(missing_ok=True)
        except OSError as exc:
            raise CredentialStoreError("The Google credential file could not be deleted.") from exc
