"""Application settings.

Values that depend on the local machine are resolved here. The SQLite file lives
in the private data directory; the Google Service Account key has exactly one
location, ``<repository>/.local/credentials/google-service-account.json``. Both
are outside Git-tracked paths and are covered by ``.gitignore`` (``.local/``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
SERVICE_ACCOUNT_FILENAME = "google-service-account.json"


def _env_text(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _env_float(name: str, default: float) -> float:
    value = _env_text(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number.") from exc


def _env_int(name: str, default: int) -> int:
    value = _env_text(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc


def default_data_dir() -> Path:
    override = os.environ.get("PJF_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".local" / "share" / "personal-job-finder"


def default_local_dir() -> Path:
    """Canonical credential directory, overridable for isolated verification.

    ADR-028 documents ``PJF_LOCAL_DIR`` as the way to point the credential
    directory somewhere else. Isolated checks (temporary credential, live E2E
    harness) must never write to the user's canonical key file.
    """

    override = os.environ.get("PJF_LOCAL_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return REPOSITORY_ROOT / ".local"


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = "Personal Job Finder API"
    service_name: str = "personal-job-finder-api"
    version: str = "0.1.0"
    api_v1_prefix: str = "/api/v1"
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = field(default_factory=default_data_dir)
    local_dir: Path = field(default_factory=default_local_dir)
    database_filename: str = "personal-job-finder.sqlite3"
    sqlite_busy_timeout_ms: int = 5_000
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "::1")
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    )

    google_sheets_spreadsheet_id: str | None = field(
        default_factory=lambda: _env_text("GOOGLE_SHEETS_SPREADSHEET_ID"),
        repr=False,
    )
    google_sheets_timeout_seconds: float = field(
        default_factory=lambda: _env_float("GOOGLE_SHEETS_TIMEOUT_SECONDS", 10.0)
    )
    google_sheets_max_batch_ranges: int = field(
        default_factory=lambda: _env_int("GOOGLE_SHEETS_MAX_BATCH_RANGES", 100)
    )
    google_sheets_batch_size: int = field(
        default_factory=lambda: _env_int("GOOGLE_SHEETS_BATCH_SIZE", 25)
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_dir", Path(self.data_dir).expanduser().resolve())
        object.__setattr__(self, "local_dir", Path(self.local_dir).expanduser().resolve())

    @property
    def google_service_account_file(self) -> Path:
        """The one canonical credential location for V1."""

        return self.local_dir / "credentials" / SERVICE_ACCOUNT_FILENAME

    @property
    def database_path(self) -> Path:
        return self.data_dir / self.database_filename

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"


settings = Settings()
