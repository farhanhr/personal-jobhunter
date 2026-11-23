"""``job-hunter`` command-line entry point.

Commands delegate to the same services the Settings UI uses. This module contains
no setup business logic of its own.

    job-hunter setup    validate the key and spreadsheet, then initialize the workspace
    job-hunter start    run the local web server on 127.0.0.1
    job-hunter doctor   read-only readiness report
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import Engine

from personal_job_finder_api.core.config import Settings
from personal_job_finder_api.db.engine import create_database_engine
from personal_job_finder_api.db.migrate import run_migrations
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.integrations.google_sheets.orchestration import (
    SetupResult,
    run_setup,
)
from personal_job_finder_api.integrations.google_sheets.service_account import (
    parse_service_account_key,
)
from personal_job_finder_api.integrations.google_sheets.service_account_store import (
    load_service_account_text,
    save_service_account_key,
)
from personal_job_finder_api.integrations.google_sheets.settings_store import (
    GoogleSheetsSettingsStore,
    effective_spreadsheet,
    environment_spreadsheet_id,
    readiness_for_state,
)

MAX_KEY_BYTES = 64 * 1024


def _print_result(result: SetupResult) -> None:
    if result.ready:
        print("Ready: Google Sheets is connected.")
        if result.service_account_email:
            print(f"Service Account: {result.service_account_email}")
        if result.adopted_existing:
            print("Adopted an existing Personal JobHunter workspace.")
        if result.counts:
            summary = ", ".join(f"{name}={value}" for name, value in result.counts.items())
            print(f"Refreshed local data: {summary}")
        if result.optional_warning:
            print(f"Note: {result.optional_warning}")
        if result.refresh_warning:
            print(f"Warning: {result.refresh_warning}")
        return
    print(f"Setup stopped at stage '{result.stage}': {result.code}", file=sys.stderr)
    print(result.message, file=sys.stderr)
    print(f"Preserved: {result.preserved}", file=sys.stderr)
    print(f"Retry safe: {'yes' if result.retry_safe else 'no'}", file=sys.stderr)


def _install_key(settings: Settings, key_file: str) -> bool:
    """Copy a downloaded Service Account key to the canonical path."""

    try:
        raw = Path(key_file).expanduser().read_bytes()
    except OSError as error:
        print(f"Could not read the key file: {error.strerror or 'read failed'}", file=sys.stderr)
        return False
    if len(raw) > MAX_KEY_BYTES:
        print("The key file is larger than 64 KB.", file=sys.stderr)
        return False
    try:
        key = parse_service_account_key(raw)
        save_service_account_key(settings, key)
    except GoogleSheetsError as error:
        print(f"Key rejected ({error.code}): {error}", file=sys.stderr)
        return False
    print(f"Service Account key installed: {key.client_email}")
    return True


def _setup(settings: Settings, *, key_file: str | None, spreadsheet_url: str | None) -> int:
    if key_file is not None and not _install_key(settings, key_file):
        return 1
    run_migrations(settings.database_url)
    engine = create_database_engine(settings)
    try:
        result = run_setup(
            settings,
            engine,
            spreadsheet_input=spreadsheet_url,
            configured_via="cli",
        )
    finally:
        engine.dispose()
    _print_result(result)
    return 0 if result.ready else 1


def _settings_table_missing(engine: Engine) -> bool:
    """True before migration 0005 exists (fresh or pre-migration database)."""

    from sqlalchemy import inspect

    return not inspect(engine).has_table("google_sheets_settings")


def _doctor(settings: Settings) -> int:
    key_present = load_service_account_text(settings) is not None
    print(f"Service Account key: {'present' if key_present else 'missing'}")
    print(f"Key location: {settings.google_service_account_file}")
    engine = create_database_engine(settings)
    try:
        if _settings_table_missing(engine):
            # Read-only diagnostic: never migrate from `doctor`. Point the user
            # at the command that both migrates and completes setup.
            print("Database: migrations pending (settings table not created yet)")
            print("Workspace ready: no")
            print("Run `job-hunter setup` to create the database and finish setup.")
            return 1
        effective = effective_spreadsheet(engine, settings)
        saved = GoogleSheetsSettingsStore(engine).load_state()
    except GoogleSheetsError as error:
        print(f"Spreadsheet configured: no ({error.code})")
        print(str(error))
        print("Workspace ready: no")
        print("Fix the spreadsheet configuration in `.env` or run `job-hunter setup`.")
        return 1
    finally:
        engine.dispose()
    ready = readiness_for_state(
        saved,
        environment_id=environment_spreadsheet_id(settings),
    )
    print(
        f"Spreadsheet configured: {'yes' if effective.spreadsheet_id else 'no'} "
        f"(source: {effective.source})"
    )
    print(f"Workspace ready: {'yes' if ready else 'no'}")
    if saved is not None and saved.configured_via is not None:
        print(f"Configured via: {saved.configured_via}")
    return 0 if key_present and effective.spreadsheet_id and ready else 1


def _start(settings: Settings) -> int:
    import uvicorn

    from personal_job_finder_api.main import create_app

    uvicorn.run(create_app(issue_bootstrap_token=True), host="127.0.0.1", port=settings.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="job-hunter",
        description="Personal JobHunter local tools.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser(
        "setup",
        help="validate Google Sheets access and initialize the workspace",
    )
    setup.add_argument(
        "--key-file",
        default=None,
        help="Service Account JSON key to install at the canonical local path",
    )
    setup.add_argument(
        "--spreadsheet-url",
        default=None,
        help="Google Sheets URL to validate and adopt as the active spreadsheet",
    )
    commands.add_parser("start", help="run the local web application on 127.0.0.1")
    commands.add_parser("doctor", help="show read-only setup readiness")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    settings = Settings()
    if arguments.command == "setup":
        return _setup(
            settings,
            key_file=arguments.key_file,
            spreadsheet_url=arguments.spreadsheet_url,
        )
    if arguments.command == "doctor":
        return _doctor(settings)
    return _start(settings)


if __name__ == "__main__":
    raise SystemExit(main())
