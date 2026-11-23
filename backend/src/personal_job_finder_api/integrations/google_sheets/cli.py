"""Local command-line setup for the Google Sheets foundation.

Commands never print credentials, provider response bodies, or spreadsheet IDs.
Only the explicitly typed ``init``, ``metrics-init``, ``dashboard-delete``,
``workspace-repair``, and ``sync`` commands write to the spreadsheet; ``check``
stays read-only.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from zoneinfo import ZoneInfo

from sqlalchemy.exc import SQLAlchemyError

from personal_job_finder_api.core.config import Settings
from personal_job_finder_api.core.time import utc_now
from personal_job_finder_api.db.engine import create_database_engine
from personal_job_finder_api.db.migrate import run_migrations
from personal_job_finder_api.domain.search_policy import JAKARTA_TIMEZONE
from personal_job_finder_api.integrations.google_sheets.credentials import (
    CredentialStoreError,
)
from personal_job_finder_api.integrations.google_sheets.dashboard_verify import (
    verify_dashboard,
)
from personal_job_finder_api.integrations.google_sheets.errors import GoogleSheetsError
from personal_job_finder_api.integrations.google_sheets.preflight import (
    PreflightResult,
)
from personal_job_finder_api.integrations.google_sheets.service import (
    GoogleSheetsFoundation,
    build_google_sheets_foundation,
)
from personal_job_finder_api.integrations.google_sheets.service_account_store import (
    delete_service_account_key,
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


def _run_dashboard_verify(settings: Settings, foundation: GoogleSheetsFoundation) -> int:
    """Read-only comparison of every Dashboard metric with the canonical rows."""

    as_of = utc_now().astimezone(ZoneInfo(JAKARTA_TIMEZONE)).date()
    report = verify_dashboard(
        foundation.client,
        foundation.config.spreadsheet_id,
        as_of=as_of,
    )

    print(f"Locale: {report.locale or 'unknown'} | Time zone: {report.time_zone or 'unset'}")
    for check in report.checks:
        if check.ok:
            continue
        detail = f"{check.address}: expected {check.expected!r}, actual {check.actual!r}"
        if check.error is not None:
            detail += f" (error {check.error})"
        if check.status == "not_a_formula":
            detail += " (cell is a constant, not a formula)"
        if check.status == "missing":
            detail += " (cell is missing)"
        print(detail, file=sys.stderr)
    for problem in report.problems:
        print(problem, file=sys.stderr)
    print(report.summary())
    return 0 if report.ok else 1


def _run_sync(settings: Settings, arguments: argparse.Namespace) -> int:
    """Run one manual dispatch. No scheduler, no background loop."""

    engine = create_database_engine(settings)
    try:
        # The API server migrates on startup; the CLI must do the same, otherwise
        # `sync` on a fresh data directory fails with "no such table" and the
        # operator sees only a generic message.
        try:
            # Alembic logs every applied revision at INFO on stderr. A CLI whose
            # real output is one summary line should not print migration chatter.
            logging.getLogger("alembic").setLevel(logging.WARNING)
            run_migrations(settings.database_url)
        except SQLAlchemyError:
            raise GoogleSheetsError(
                "database_unavailable",
                "The local operational database could not be prepared. Check that "
                f"{settings.data_dir} is writable, then retry.",
            ) from None

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
        # Repair first: a cleared header or a deleted table would otherwise make
        # every projection in this dispatch land in the wrong place.
        repair = foundation.repair_workspace()
        if repair.changed:
            print(f"Workspace repair: {repair.message()}")
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
        description="Configure and inspect the local Google Sheets connection.",
    )
    parser.add_argument(
        "command",
        choices=(
            "check",
            "init",
            "dashboard-delete",
            "dashboard-verify",
            "metrics-init",
            "workspace-repair",
            "disconnect",
            "sync",
            "recover",
            "requeue",
        ),
        help=(
            "check: read-only preflight; "
            "init: create missing canonical tabs and empty headers; "
            "dashboard-delete: delete the retired summary-cards Dashboard tab when "
            "this project owns it; "
            "metrics-init: create or refresh the optional formula-only Metrics board; "
            "dashboard-verify: read-only check that the KPI strip and every metric "
            "agree with the canonical rows; workspace-repair: reconcile headers, the "
            "native table, the KPI strip, and the frozen panes without changing data; "
            "disconnect: remove the stored Service Account key; sync: dispatch due "
            "outbox work; recover: return abandoned in-progress work to the retry "
            "queue; requeue: re-enable one blocked projection after fixing its cause"
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
    parser.add_argument(
        "--force",
        action="store_true",
        help="reapply presentation even when the structure already looks healthy",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    settings = Settings()

    try:
        if arguments.command == "disconnect":
            delete_service_account_key(settings)
            print("Stored Service Account key removed.")
            return 0

        if arguments.command in {"sync", "recover", "requeue"}:
            return _run_sync(settings, arguments)

        foundation = build_google_sheets_foundation(settings)
        if arguments.command == "check":
            result = foundation.check()
            _print_preflight(result)
            return 0 if result.is_ready else 1

        if arguments.command == "dashboard-delete":
            print(foundation.remove_dashboard())
            return 0

        if arguments.command == "metrics-init":
            outcome = foundation.initialize_dashboard(force=arguments.force)
            print(outcome.message)
            return 0

        if arguments.command == "dashboard-verify":
            return _run_dashboard_verify(settings, foundation)

        if arguments.command == "workspace-repair":
            outcome = foundation.repair_workspace(force=arguments.force)
            print(outcome.message())
            return 0

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
