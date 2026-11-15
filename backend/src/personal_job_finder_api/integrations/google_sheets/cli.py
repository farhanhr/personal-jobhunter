"""Local command-line setup for the Google Sheets foundation.

Commands never print credentials, OAuth response bodies, or spreadsheet IDs.
The ``init`` command is the only path that writes to the spreadsheet, and it
only runs after an explicitly typed command.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from personal_job_finder_api.core.config import Settings
from personal_job_finder_api.integrations.google_sheets.credentials import (
    CredentialStoreError,
    build_credential_store,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.integrations.google_sheets.oauth import (
    build_google_oauth_authorizer,
)
from personal_job_finder_api.integrations.google_sheets.preflight import (
    PreflightResult,
)
from personal_job_finder_api.integrations.google_sheets.service import (
    build_google_sheets_foundation,
)


def _print_preflight(result: PreflightResult) -> None:
    for title, tab in result.tabs.items():
        if not tab.exists:
            print(f"{title}: missing")
        elif tab.header_matches:
            suffix = (
                f" (extra columns: {', '.join(tab.unexpected_headers)})"
                if tab.unexpected_headers
                else ""
            )
            print(f"{title}: ready{suffix}")
        elif tab.empty_header_row:
            print(f"{title}: empty header row (run init)")
        else:
            print(f"{title}: incompatible header")
    print("Spreadsheet is ready." if result.is_ready else "Spreadsheet is not ready.")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m personal_job_finder_api.integrations.google_sheets.cli",
        description="Authorize and inspect the local Google Sheets connection.",
    )
    parser.add_argument(
        "command",
        choices=("auth", "check", "init", "disconnect"),
        help=(
            "auth: connect a Google account; check: read-only preflight; "
            "init: create missing tabs and empty headers; disconnect: remove "
            "the stored Google credential"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    settings = Settings()

    try:
        if arguments.command == "auth":
            build_google_oauth_authorizer(settings).authorize()
            print("Google Sheets authorization saved.")
            return 0

        if arguments.command == "disconnect":
            build_credential_store(settings).delete()
            print("Stored Google Sheets credential removed.")
            return 0

        foundation = build_google_sheets_foundation(settings)
        if arguments.command == "check":
            result = foundation.check()
            _print_preflight(result)
            return 0 if result.is_ready else 1

        result = foundation.initialize()
        _print_preflight(result)
        return 0 if result.is_ready else 1
    except GoogleSheetsError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 1
    except CredentialStoreError as exc:
        print(f"credentials_storage_failed: {exc}", file=sys.stderr)
        return 1
    except Exception:
        # Unexpected library failures can carry provider response bodies.
        # Report a stable code and never print a traceback.
        print(
            "google_sheets_error: an unexpected failure occurred; no credentials "
            "or response details were printed.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
