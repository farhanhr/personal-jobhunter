"""Application settings.

Values that depend on the local machine are resolved here. The real SQLite file
must stay outside the repository; tests override the data directory explicitly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


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

    @property
    def database_path(self) -> Path:
        return self.data_dir / self.database_filename

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"


settings = Settings()
