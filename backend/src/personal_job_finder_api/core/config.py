"""Application settings.

Values that depend on the local machine are resolved here. The real SQLite file
must stay outside the repository; tests override the data directory explicitly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


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


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = "Personal Job Finder API"
    service_name: str = "personal-job-finder-api"
    version: str = "0.1.0"
    api_v1_prefix: str = "/api/v1"
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = field(default_factory=default_data_dir)
    database_filename: str = "personal-job-finder.sqlite3"
    sqlite_busy_timeout_ms: int = 5_000
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "::1")
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    )

    # Google Sheets configuration is local private state. The spreadsheet ID
    # and OAuth files are never committed or included in public error output.
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
    google_oauth_client_file: Path | None = None
    google_token_file: Path | None = None
    google_keyring_service_name: str = field(
        default_factory=lambda: _env_text("GOOGLE_KEYRING_SERVICE_NAME") or "personal-job-finder"
    )
    google_oauth_timeout_seconds: float = field(
        default_factory=lambda: _env_float("GOOGLE_OAUTH_TIMEOUT_SECONDS", 300.0)
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "data_dir",
            Path(self.data_dir).expanduser().resolve(),
        )
        object.__setattr__(
            self,
            "google_oauth_client_file",
            self._resolve_google_path(
                explicit=self.google_oauth_client_file,
                environment_variable="GOOGLE_OAUTH_CLIENT_FILE",
                filename="google-oauth-client.json",
            ),
        )
        object.__setattr__(
            self,
            "google_token_file",
            self._resolve_google_path(
                explicit=self.google_token_file,
                environment_variable="GOOGLE_TOKEN_FILE",
                filename="google-token.json",
            ),
        )

    def _resolve_google_path(
        self,
        *,
        explicit: Path | None,
        environment_variable: str,
        filename: str,
    ) -> Path:
        if explicit is not None:
            return Path(explicit).expanduser()
        override = _env_text(environment_variable)
        if override:
            return Path(override).expanduser()
        return self.data_dir / "credentials" / filename

    @property
    def database_path(self) -> Path:
        return self.data_dir / self.database_filename

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"


settings = Settings()
