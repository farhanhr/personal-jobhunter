"""Single-process coordination for Google Sheets I/O.

A refresh must not read the spreadsheet while a dispatch is writing it, because
the resulting snapshot could mix pre- and post-write rows. One process-wide lock
serializes both operations. The supported deployment is a single backend
process, so this is deliberately not a distributed lock and never spans a SQLite
transaction: the holder may wait on Google, but no database transaction is open
while it does.

Conflict handling stays with the caller: dispatch fails fast, the manual refresh
endpoint fails fast, and a pre-reservation reconciliation may wait briefly for an
in-flight operation so a start request is not rejected by a momentary overlap.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

DEFAULT_PRE_RESERVATION_WAIT_SECONDS = 30.0

_SHEETS_IO_LOCK = threading.Lock()


class SheetsOperationBusy(RuntimeError):
    """Another refresh or dispatch currently holds the Sheets I/O lock."""


def acquire_sheets_io(*, wait_seconds: float = 0.0) -> None:
    """Acquire the global Sheets I/O lock or raise :class:`SheetsOperationBusy`."""

    if wait_seconds < 0:
        raise ValueError("wait_seconds must not be negative")

    if wait_seconds == 0:
        acquired = _SHEETS_IO_LOCK.acquire(blocking=False)
    else:
        acquired = _SHEETS_IO_LOCK.acquire(timeout=wait_seconds)

    if not acquired:
        raise SheetsOperationBusy("A Google Sheets refresh or synchronization is already running.")


def release_sheets_io() -> None:
    """Release the global Sheets I/O lock."""

    _SHEETS_IO_LOCK.release()


@contextmanager
def sheets_io_guard(*, wait_seconds: float = 0.0) -> Iterator[None]:
    """Hold the global Sheets I/O lock for one operation."""

    acquire_sheets_io(wait_seconds=wait_seconds)
    try:
        yield
    finally:
        release_sheets_io()
