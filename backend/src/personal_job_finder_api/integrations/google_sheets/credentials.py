"""Private storage for Google OAuth credentials.

The refresh token is sensitive. The preferred location is the operating-system
keyring; a restrictive-permission file under the private data directory is only
a fallback for machines without a usable Secret Service backend. Tokens never
belong in SQLite, outbox payloads, logs, or the repository.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Protocol

import keyring
import keyring.errors


class CredentialStoreError(Exception):
    """Raised when a credential store cannot read or write safely."""


class CredentialStore(Protocol):
    """Minimal storage boundary used by OAuth and test doubles."""

    def load(self) -> str | None: ...

    def save(self, value: str) -> None: ...

    def delete(self) -> None: ...


class KeyringCredentialStore:
    """Store the serialized Google credentials in the OS keyring."""

    def __init__(
        self,
        *,
        service_name: str,
        username: str,
        keyring_module: Any = keyring,
    ) -> None:
        self._service_name = service_name
        self._username = username
        self._keyring = keyring_module

    def load(self) -> str | None:
        try:
            return self._keyring.get_password(self._service_name, self._username)
        except keyring.errors.KeyringError:
            return None

    def save(self, value: str) -> None:
        try:
            self._keyring.set_password(self._service_name, self._username, value)
        except keyring.errors.KeyringError as exc:
            raise CredentialStoreError(
                "The operating-system keyring rejected the Google credential."
            ) from exc

    def delete(self) -> None:
        try:
            self._keyring.delete_password(self._service_name, self._username)
        except keyring.errors.PasswordDeleteError:
            return
        except keyring.errors.KeyringError as exc:
            raise CredentialStoreError(
                "The operating-system keyring could not delete the Google credential."
            ) from exc


class FileCredentialStore:
    """Store credentials in a private local file with mode ``0600``.

    The parent directory is created with mode ``0700`` and symlinks are refused
    so a local attacker cannot redirect the token write to another file.
    """

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
            # A token file created by an older tool or copied by hand may be
            # group/world readable. Tighten it before reading the secret.
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
            # Tighten only a directory this store creates. An existing directory
            # could be a shared location such as the user's home directory, and
            # silently changing its permissions would be destructive. The token
            # file itself is always written with mode 0600.
            parent_existed = parent.exists()
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not parent_existed:
                os.chmod(parent, 0o700)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=parent,
                prefix=".google-token-",
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


class SecureCredentialStore:
    """Prefer the keyring, falling back to a private local file."""

    def __init__(
        self,
        *,
        keyring_store: CredentialStore,
        file_store: CredentialStore,
    ) -> None:
        self._keyring_store = keyring_store
        self._file_store = file_store

    def load(self) -> str | None:
        try:
            value = self._keyring_store.load()
        except CredentialStoreError:
            value = None
        if value is not None:
            return value
        return self._file_store.load()

    def save(self, value: str) -> None:
        try:
            self._keyring_store.save(value)
            return
        except CredentialStoreError:
            pass
        self._file_store.save(value)

    def delete(self) -> None:
        keyring_error: CredentialStoreError | None = None
        try:
            self._keyring_store.delete()
        except CredentialStoreError as exc:
            keyring_error = exc
        try:
            self._file_store.delete()
        except CredentialStoreError:
            if keyring_error is None:
                raise
        if keyring_error is not None:
            raise keyring_error


def build_credential_store(
    settings: Any,
    *,
    keyring_module: Any = keyring,
) -> SecureCredentialStore:
    """Build the production credential store from local settings."""

    token_file = settings.google_token_file
    if token_file is None:
        raise CredentialStoreError("The private Google credential path is not configured.")
    return SecureCredentialStore(
        keyring_store=KeyringCredentialStore(
            service_name=settings.google_keyring_service_name,
            username="google-oauth-token",
            keyring_module=keyring_module,
        ),
        file_store=FileCredentialStore(Path(token_file)),
    )
