"""Tests for private OAuth credential storage."""

from __future__ import annotations

import stat
from pathlib import Path

import keyring.errors
import pytest

from personal_job_finder_api.integrations.google_sheets.credentials import (
    CredentialStoreError,
    FileCredentialStore,
    KeyringCredentialStore,
    SecureCredentialStore,
)


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.values.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.values[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        if (service_name, username) not in self.values:
            raise keyring.errors.PasswordDeleteError("missing")
        del self.values[(service_name, username)]


class BrokenKeyring:
    def get_password(self, service_name: str, username: str) -> str | None:
        raise keyring.errors.NoKeyringError("no backend")

    def set_password(self, service_name: str, username: str, password: str) -> None:
        raise keyring.errors.NoKeyringError("no backend")

    def delete_password(self, service_name: str, username: str) -> None:
        raise keyring.errors.NoKeyringError("no backend")


def test_keyring_store_round_trips_without_touching_the_file_system(tmp_path: Path) -> None:
    fake = FakeKeyring()
    store = KeyringCredentialStore(
        service_name="personal-job-finder-tests",
        username="oauth-token",
        keyring_module=fake,
    )

    store.save('{"refresh_token":"synthetic"}')

    assert store.load() == '{"refresh_token":"synthetic"}'
    assert fake.values == {
        ("personal-job-finder-tests", "oauth-token"): '{"refresh_token":"synthetic"}'
    }
    store.delete()
    assert store.load() is None


def test_file_store_uses_private_directory_and_file_permissions(tmp_path: Path) -> None:
    path = tmp_path / "private" / "credentials" / "token.json"
    store = FileCredentialStore(path)

    store.save('{"refresh_token":"synthetic"}')

    assert store.load() == '{"refresh_token":"synthetic"}'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_file_store_rejects_a_symlink_target(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("unchanged", encoding="utf-8")
    link = tmp_path / "token.json"
    link.symlink_to(target)
    store = FileCredentialStore(link)

    with pytest.raises(CredentialStoreError):
        store.save('{"refresh_token":"synthetic"}')

    assert target.read_text(encoding="utf-8") == "unchanged"


def test_file_store_delete_is_idempotent(tmp_path: Path) -> None:
    store = FileCredentialStore(tmp_path / "token.json")

    store.delete()
    store.delete()

    assert store.load() is None


def test_secure_store_falls_back_to_the_file_when_keyring_is_unavailable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "credentials" / "token.json"
    store = SecureCredentialStore(
        keyring_store=KeyringCredentialStore(
            service_name="personal-job-finder-tests",
            username="oauth-token",
            keyring_module=BrokenKeyring(),
        ),
        file_store=FileCredentialStore(path),
    )

    store.save('{"refresh_token":"synthetic"}')

    assert store.load() == '{"refresh_token":"synthetic"}'
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_secure_store_prefers_keyring_and_does_not_write_a_token_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "credentials" / "token.json"
    fake = FakeKeyring()
    store = SecureCredentialStore(
        keyring_store=KeyringCredentialStore(
            service_name="personal-job-finder-tests",
            username="oauth-token",
            keyring_module=fake,
        ),
        file_store=FileCredentialStore(path),
    )

    store.save('{"refresh_token":"synthetic"}')

    assert store.load() == '{"refresh_token":"synthetic"}'
    assert not path.exists()


def test_secure_store_loads_a_legacy_file_when_keyring_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "token.json"
    FileCredentialStore(path).save('{"refresh_token":"synthetic"}')
    store = SecureCredentialStore(
        keyring_store=KeyringCredentialStore(
            service_name="personal-job-finder-tests",
            username="oauth-token",
            keyring_module=FakeKeyring(),
        ),
        file_store=FileCredentialStore(path),
    )

    assert store.load() == '{"refresh_token":"synthetic"}'


def test_loading_a_loosely_permissioned_token_file_tightens_it(tmp_path: Path) -> None:
    path = tmp_path / "token.json"
    path.write_text('{"refresh_token":"synthetic"}', encoding="utf-8")
    path.chmod(0o644)
    store = FileCredentialStore(path)

    assert store.load() == '{"refresh_token":"synthetic"}'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
