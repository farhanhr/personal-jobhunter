"""Regression tests for private credential file safety."""

from __future__ import annotations

import stat
from pathlib import Path

from personal_job_finder_api.integrations.google_sheets.credentials import (
    FileCredentialStore,
)


def test_saving_into_an_existing_directory_does_not_change_its_permissions(
    tmp_path: Path,
) -> None:
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)

    FileCredentialStore(shared / "token.json").save('{"client_email":"synthetic@example.test"}')

    assert stat.S_IMODE(shared.stat().st_mode) == 0o755
    assert stat.S_IMODE((shared / "token.json").stat().st_mode) == 0o600
