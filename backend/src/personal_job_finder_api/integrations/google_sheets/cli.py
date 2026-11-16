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
from personal_job_finder_api.core.time import utc_now
from personal_job_finder_api.db.engine import create_database_engine
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
from personal_job_finder_api.repositories.sync_outbox import (
    STALE_IN_PROGRESS_AFTER,
    OutboxRepository,
)
from personal_job_finder_api.sync.dispatcher import SyncDispatcher


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


def _run_sync(settings: Settings, arguments: argparse.Namespace) -> int:
    """Run one manual dispatch. No scheduler, no background loop."""

    engine = create_database_engine(settings)
    try:
        if arguments.command == "recover":
            # Recovery only touches SQLite, so it must work even when the stored
            # Google credential is the reason work is stuck.
            recovered = OutboxRepository(engine).recover_stale_in_progress(
                now=utc_now(),
                stale_before=utc_now() - STALE_IN_PROGRESS_AFTER,
            )
            print(f"Recovered {recovered} interrupted projection(s).")
            return 0

        if arguments.command == "requeue":
            requeued = OutboxRepository(engine).requeue_blocked(
                outbox_id=arguments.outbox_id,
                now=utc_now(),
            )
            if not requeued:
                print(
                    f"No blocked projection with id {arguments.outbox_id}.",
                    file=sys.stderr,
                )
                return 1
            print(f"Requeued projection {arguments.outbox_id}.")
            return 0

        foundation = build_google_sheets_foundation(settings)
        dispatcher = SyncDispatcher(
            engine,
            transport=foundation.transport,
            spreadsheet_id=foundation.config.spreadsheet_id,
            batch_size=foundation.config.batch_size,
            max_batch_ranges=foundation.config.max_batch_ranges,
        )
        if arguments.recover_first:
            recovered = dispatcher.recover()
            print(f"Recovered {recovered} interrupted projection(s).")

        outcome = dispatcher.dispatch_due()
        print(
            "Sync finished: "
            f"synced={outcome.synced} "
            f"failed={outcome.failed} "
            f"blocked={outcome.blocked} "
            f"remaining={outcome.remaining}"
        )
        if outcome.remaining > 0:
            print(
                "Some projections are still unsent. Inspect them, then run "
                "`recover` for interrupted work or `requeue --outbox-id ...` "
                "after fixing a blocked cause.",
                file=sys.stderr,
            )
        return 0 if outcome.failed == 0 and outcome.blocked == 0 and outcome.remaining == 0 else 1
    finally:
        engine.dispose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m personal_job_finder_api.integrations.google_sheets.cli",
        description="Authorize and inspect the local Google Sheets connection.",
    )
    parser.add_argument(
        "command",
        choices=(
            "auth",
            "check",
            "init",
            "disconnect",
            "sync",
            "recover",
            "requeue",
        ),
        help=(
            "auth: connect a Google account; check: read-only preflight; "
            "init: create missing tabs and empty headers; disconnect: remove the "
            "stored Google credential; sync: dispatch due outbox work; recover: "
            "return abandoned in-progress work to the retry queue; requeue: "
            "re-enable one blocked projection after fixing its cause"
        ),
    )
    parser.add_argument(
        "--recover-first",
        action="store_true",
        help="recover abandoned in-progress work before dispatching",
    )
    parser.add_argument(
        "--outbox-id",
        default="",
        help="projection id used by the requeue command",
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

        if arguments.command in {"sync", "recover", "requeue"}:
            return _run_sync(settings, arguments)

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
