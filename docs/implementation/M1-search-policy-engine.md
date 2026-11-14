# M1 — Search Policy Engine

**Status:** Implemented  
**Scope:** Domain policy, SQLite persistence, atomic reservation, idempotency, and
interrupted-run recovery  
**Date:** 2026-11-13  
**Architecture:** ADR-019 (SQLAlchemy Core + Alembic)

## 1. What was implemented

M1 answers one question safely: **may a search run start now, and can its quota be
reserved exactly once?**

It contains three layers:

1. **M1A — pure domain policy.** Deterministic functions calculate the operational day,
   cooldown, quota, active-run state, configuration eligibility, blocking reasons, and the
   next eligible timestamp. They perform no I/O and never read the wall clock.
2. **M1B — SQLite persistence.** SQLAlchemy Core tables for `search_runs`, `target_roles`,
   and `companies`, created and changed only through Alembic migrations.
3. **M1C — atomic reservation and recovery.** A short `BEGIN IMMEDIATE` transaction checks
   idempotency, configuration, active runs, quota, and cooldown, then inserts an `accepted`
   run and commits before any execution could begin. Startup recovery converts stale
   `accepted`/`running` rows to `interrupted` without refunding quota.

M1 does **not** implement job connectors, Auto Search orchestration, Google Sheets,
LinkedIn/Gmail integration, a start-run HTTP endpoint, or frontend changes. Only
`GET /api/v1/search-policy` is exposed.

## 2. Files

```text
backend/
  alembic.ini
  migrations/
    env.py
    script.py.mako
    versions/0001_initial_schema.py
    versions/0002_company_policy_status.py
  src/personal_job_finder_api/
    core/config.py                     Data directory and database URL
    core/time.py                       Aware UTC formatting and parsing
    db/engine.py                       Engine pragmas and BEGIN IMMEDIATE helper
    db/migrate.py                      Programmatic Alembic upgrade/downgrade
    db/paths.py                        Private directory and file permissions
    db/tables.py                       SQLAlchemy Core schema
    domain/search_policy.py            Pure policy rules and decision model
    repositories/search_policy.py      Policy-state queries and run persistence
    search/service.py                  Read-only policy evaluation
    search/reservation.py              Atomic reservation and idempotency
    search/recovery.py                 Startup stale-run recovery
    api/v1/search_policy.py            GET /api/v1/search-policy
    main.py                            Lifespan: migrate, recover, dispose
  tests/
    conftest.py                        Temporary database and configuration fixtures
    test_search_policy_domain.py       Pure-domain boundaries
    test_migrations.py                 Schema, constraints, round-trip migration
    test_search_reservation.py         Quota, cooldown, idempotency, concurrency
    test_search_recovery.py            Interrupted-run behavior
    test_search_policy_api.py          Read-only API and startup recovery
```

## 3. Domain policy architecture

`backend/src/personal_job_finder_api/domain/search_policy.py` is dependency-free except
for the Python standard library. It exports:

| Function / type | Responsibility |
|---|---|
| `get_operational_day` | Map an aware instant to a Jakarta operational date |
| `get_next_reset_at` | Return the next 06:00 WIB reset as UTC |
| `get_cooldown_until` | Return `last_accepted_at + 12 hours` |
| `get_cooldown_remaining_seconds` | Whole seconds remaining, rounded up |
| `evaluate_search_policy` | Produce an immutable `SearchPolicyDecision` |
| `RunStatus` | Approved lifecycle statuses and active/terminal sets |
| `BlockingReason` | Stable machine-readable rejection codes |

`SearchPolicyDecision` is a frozen dataclass. Callers cannot accidentally mutate a
decision after evaluation. It carries `allowed`, `operational_day`, `max_runs`,
`accepted_runs`, `remaining_runs`, `last_accepted_at`, `cooldown_until`,
`cooldown_remaining_seconds`, `next_eligible_at`, `next_reset_at`, `has_active_run`, and
`blocking_reasons`.

Every public function rejects naive datetimes and normalizes aware inputs to UTC before
arithmetic. `now` is always injected; `datetime.now()` never appears in the domain module.

### 3.1 Operational day

```python
def get_operational_day(now, timezone="Asia/Jakarta", reset_hour=6):
    local_now = now.astimezone(ZoneInfo(timezone))
    return (local_now - timedelta(hours=reset_hour)).date()
```

Subtracting the reset hour moves times before 06:00 into the previous calendar date. For
example, 12 November 05:59:59 WIB belongs to 11 November; 12 November 06:00:00 WIB belongs
to 12 November.

### 3.2 Cooldown and quota

Cooldown is measured from the latest **accepted** timestamp, not from completion:

```text
cooldown_until = last_accepted_at + 12 hours
remaining      = max(0, ceil(cooldown_until - now))
```

- Fewer than 12 hours: `cooldown_active`.
- Exactly 12 hours or later: cooldown cleared.
- Resetting the operational day does not clear cooldown.

Quota counts rows accepted for the current operational day. Failed, interrupted, and
otherwise terminal accepted runs still count. Rejected requests create no row and consume
nothing. A third accepted run in one operational day is blocked until the next reset.

### 3.3 Blocking reasons and next eligible time

Reasons are emitted in stable order:

1. `no_target_roles`
2. `too_many_target_roles`
3. `no_usable_source`
4. `active_run_exists`
5. `quota_exhausted`
6. `cooldown_active`

`next_eligible_at` is:

- `now` when allowed;
- the later of the next reset and cooldown end when time-based blockers apply;
- `None` when an active run or invalid configuration must change first, because no
  deterministic time can resolve those conditions.

A usable source is an enabled company with `policy_status = permitted` and either:

- `ats_type` in `greenhouse`, `lever`, `ashby` and a non-empty board token; or
- `ats_type = custom` and a non-empty career URL.

`policy_status = unreviewed` is the safe default. `blocked` records a source that must not
be used. `ats_type = none` and disabled companies never count. This gate is deliberately
explicit: a configured URL alone is not proof that its terms permit automated access.

## 4. SQLite schema

The default database is:

```text
~/.local/share/personal-job-finder/personal-job-finder.sqlite3
```

Override the data directory with `PJF_DATA_DIR`. The database is never created inside the
repository.

### 4.1 `search_runs`

| Column | Purpose |
|---|---|
| `run_id` | Primary key, `run_<uuid hex>` |
| `idempotency_key` | Unique UUID supplied by the client |
| `request_fingerprint` | SHA-256 of canonical request payload |
| `operational_day` | `YYYY-MM-DD` Jakarta operational date |
| `status` | Constrained lifecycle status |
| `accepted_at_utc` | UTC string with `Z` suffix |
| `started_at_utc` | Nullable UTC string |
| `finished_at_utc` | Nullable UTC string |
| `config_snapshot_json` | Immutable configuration used by the run |
| `error_summary` | Sanitized failure or interruption summary |
| `progress_json` | Nullable future progress data |
| `source_summary_json` | Nullable future source summary |

Constraints:

- unique `run_id` (primary key);
- unique `idempotency_key`;
- `status IN (...)` for the seven approved statuses;
- expression unique index `ux_search_runs_single_active`:
  `(CASE WHEN status IN ('accepted', 'running') THEN 1 END)`.

The expression index allows many terminal rows but at most one active row, even if two
connections bypass the service layer.

### 4.2 `target_roles`

Stores one to three enabled roles with `role_id`, canonical name, aliases JSON, excluded
terms JSON, optional seniority JSON, enabled flag, and UTC created/updated timestamps.
`canonical_name` is unique and `enabled` is constrained to `0`/`1`.

### 4.3 `companies`

Stores `company_id`, name, website, career URL, ATS type, board token, enabled flag,
`policy_status`, access notes, and UTC timestamps. `ats_type` is constrained to
`greenhouse`, `lever`, `ashby`, `custom`, or `none`. `policy_status` is constrained to
`unreviewed`, `permitted`, or `blocked`; new rows default to `unreviewed`.

### 4.4 Private filesystem permissions

On POSIX, a newly created data directory gets mode `0700` and a new SQLite file gets mode
`0600`. Existing directories are left untouched so an override such as `PJF_DATA_DIR=/tmp`
cannot change permissions of a shared parent; an existing database file owned by the
current user is tightened to `0600`. Symlinked paths are rejected instead of followed. The
Alembic environment applies the same policy before opening the database, so a fresh
`PJF_DATA_DIR` works for both CLI migrations and application startup. This prevents other
local users from reading run history or private configuration.

Timestamps are stored as fixed-width ISO-8601 UTC text such as
`2026-11-12T00:00:00.000000Z`. This keeps lexicographic ordering identical to chronological
ordering in SQLite.

## 5. Atomic reservation

`SearchReservationService.reserve()` opens a connection in AUTOCOMMIT mode and issues
`BEGIN IMMEDIATE`. The write lock is acquired before any read that matters, so two
competing requests cannot both observe the same free slot.

Inside the transaction:

1. Validate and normalize the idempotency key as a UUID.
2. Compute the SHA-256 request fingerprint.
3. Read an existing run by idempotency key.
4. Same key + same fingerprint: return the existing run (`replayed=True`).
5. Same key + different fingerprint: raise `IdempotencyKeyConflict`.
6. Load enabled role count, usable source count, accepted count for the operational day,
   latest accepted timestamp, and active-run existence.
7. Evaluate the pure policy.
8. If blocked, raise `ReservationRejected` with the structured decision. No row is written.
9. Otherwise load the configuration snapshot.
10. Insert an `accepted` run with a stable run ID and UTC timestamp.
11. Commit before returning.

No network request occurs inside this transaction. The service returns a persisted run and
the decision; an orchestrator does not exist yet.

### 5.1 Idempotency semantics

| Request | Result |
|---|---|
| Same key, same payload | Replay the original run |
| Same key, different payload | Conflict |
| Missing, blank, or non-UUID key | Rejected |
| New key, policy blocked | Rejected; no row |
| New key, policy allowed | New accepted run |

The fingerprint uses sorted JSON keys and compact separators, so semantically identical
payloads produce the same digest. The database unique constraint remains the final
authority even if application logic is bypassed.

## 6. Startup recovery

`recover_interrupted_runs()` runs during the FastAPI lifespan, before the application
serves requests. It updates every row still in `accepted` or `running` to `interrupted`,
sets `finished_at_utc` to the recovery time, and writes a sanitized explanation.

- No run is automatically resumed.
- The original `accepted_at_utc` is preserved.
- No quota is refunded; interrupted rows still count for their operational day.
- Terminal runs are untouched.
- The application is single-process, so every active row at startup is stale by
  definition. A future multi-process design would need process ownership metadata.

## 7. Read-only API

`GET /api/v1/search-policy` returns:

```json
{
  "timezone": "Asia/Jakarta",
  "operational_day": "2026-11-12",
  "max_runs": 2,
  "accepted_runs": 1,
  "remaining_runs": 1,
  "last_accepted_at": "2026-11-12T00:00:00Z",
  "cooldown_until": "2026-11-12T12:00:00Z",
  "cooldown_remaining_seconds": 3600,
  "next_eligible_at": "2026-11-12T12:00:00Z",
  "next_reset_at": "2026-11-12T23:00:00Z",
  "has_active_run": false,
  "can_start": false,
  "block_reason": "cooldown_active",
  "blocking_reasons": ["cooldown_active"]
}
```

`block_reason` mirrors the first blocking reason for simple clients;
`blocking_reasons` is the complete list. There is intentionally no
`POST /api/v1/search-runs` in M1.

## 8. Migrations

Migrations `0001_initial_schema` and `0002_company_policy_status` create the tables and add
the explicit source-policy gate. Apply from `backend/`:

```bash
cd backend
uv run alembic upgrade head
uv run alembic current
```

Use a temporary data directory for experiments before touching real data:

```bash
PJF_DATA_DIR=/tmp/pjf-example uv run alembic upgrade head
```

A downgrade is destructive: it drops columns or tables and deletes local rows. For a
disposable test database only, roll back one revision and re-apply:

```bash
PJF_DATA_DIR=/tmp/pjf-example uv run alembic downgrade -1
PJF_DATA_DIR=/tmp/pjf-example uv run alembic upgrade head
```

Do not run `alembic downgrade base` against the real database unless you intend to erase
all M1 data. Back up the database first if the data matters.

Starting the backend also applies pending migrations automatically before recovery:

```bash
cd backend
uv run uvicorn personal_job_finder_api.main:app \
  --host 127.0.0.1 --port 8000 --reload
```

## 9. Inspecting the local database

Read-only inspection with the SQLite CLI:

```bash
sqlite3 ~/.local/share/personal-job-finder/personal-job-finder.sqlite3 \
  ".tables"

sqlite3 ~/.local/share/personal-job-finder/personal-job-finder.sqlite3 \
  "SELECT run_id, operational_day, status, accepted_at_utc FROM search_runs ORDER BY accepted_at_utc DESC LIMIT 5;"
```

Do not paste real query output into issues, screenshots, commits, or documentation. The
database is private local state.

## 10. Testing strategy

Tests use temporary SQLite files created under pytest's `tmp_path`; no test touches the real
database, job boards, Google APIs, or LinkedIn.

| Area | Examples |
|---|---|
| Operational day | 05:59:59, 06:00:00, 23:59:59, previous-day carry-over |
| Cooldown | 11:59:59 elapsed, exactly 12 hours, later than 12 hours |
| Quota | first, second, third rejected, failed runs still count |
| Active run | accepted blocks, running blocks, terminal does not block |
| Configuration | zero/too many roles, disabled, unreviewed, blocked, or unusable sources |
| Idempotency | replay, payload conflict, missing/invalid key |
| Concurrency | different-key reservation and same-key replay/conflict |
| Recovery | accepted/running become interrupted, quota retained after actual recovery |
| Database | status/policy checks, unique idempotency key, unique active index, pragmas |
| Migrations | upgrade, downgrade by one, re-upgrade, metadata drift, legacy checks/indexes, missing nested directory |
| API | eligibility payload, no-store cache header, startup migration/recovery |
| Privacy | new directory/file modes, existing directory untouched, symlink rejection |

Concurrency tests use independent SQLAlchemy engines on the same temporary database file
and a `threading.Barrier`, so both attempts overlap. Assertions verify exactly one accepted
row and the expected rejection reason afterward.

## 11. Concepts the learner should understand

- **Pure functions.** Policy rules depend only on arguments. That makes every boundary
  case reproducible without sleeping, mocking a clock, or opening a database.
- **Injected clock.** The caller supplies `now`; tests can jump to 05:59:59 or forward
  12 hours instantly.
- **Immutable decisions.** A frozen dataclass prevents partial mutation across layers.
- **Aware datetimes.** Storing UTC instants and converting only for operational-day
  calculations avoids ambiguous local times and daylight-saving assumptions.
- **`BEGIN IMMEDIATE`.** SQLite's default deferred transaction acquires a write lock only
  when it writes. Two readers could both decide a slot is free. `BEGIN IMMEDIATE`
  serializes writers from the start.
- **Database as final authority.** A disabled button is UX, not enforcement. The unique
  active-run index and transaction are enforcement.
- **Idempotency vs. deduplication.** Idempotency replays the same logical request; it does
  not merge different requests that happen to look similar.
- **Quota is consumed at acceptance.** A later failure cannot restore it, because allowing
  refunds would make the daily limit meaningless.
- **Recovery is not resumption.** Converting stale rows to `interrupted` makes state
  truthful; it does not continue abandoned work.

## 12. Verification

Executed on 2026-11-13 from `backend/`:

```text
uv run pytest                    -> 84 passed, 1 warning
uv run ruff check .              -> All checks passed
uv run ruff format --check .     -> 34 files already formatted
```

The remaining warning is from the installed Starlette test client and does not affect
production code. Alembic CLI upgrade, downgrade by one revision, and re-upgrade succeeded
on a temporary data directory that did not exist beforehand. The resulting tables were
`alembic_version`, `companies`, `search_runs`, and `target_roles`, with the active-run
expression index present, all three `companies` check constraints intact, and directory
mode `0700` / database mode `0600`.

## 13. Known M1 limitations and P0.1 alignment

M1 follows the owner's milestone brief, which names `companies` and
`request_fingerprint`. P0.1 12 uses the names `company_sources` and `request_hash` and
lists additional future fields. The deliberate differences are:

- **Derived policy state.** There is no mutable `search_policy_state` counter row. The
  latest accepted time, active run, and daily count are derived atomically from
  `search_runs`. This avoids two counters that can disagree.
- **Reduced company registry.** `companies` stores the M1 source identity and
  `policy_status`; per-source request budgets, pagination caps, timeout fields, and
  `access_policy_url` belong to the connector milestone. `policy_status` prevents an
  unreviewed source from satisfying the M1 acceptance gate.
- **Manual permission marking.** Migration `0002` marks every existing company
  `unreviewed`, so a previously usable database needs an explicit local decision before a
  run is accepted. There is no management UI in M1; use the local SQLite file directly,
  for example `UPDATE companies SET policy_status = 'permitted' WHERE company_id = '...';`.
  Never paste real company or configuration data into public issues.
- **Snapshot is an audit record.** `config_snapshot_json` lists all enabled companies,
  including ineligible ones, with `policy_status` and `access_notes`. A future orchestrator
  must re-check the same usable-source predicate before contacting any source; it must not
  assume every entry in the snapshot is fetchable.
- **Deferred fields.** `target_roles.sort_order`, `search_runs.error_code`, `app_settings`,
  and the full `company_sources` budget columns are deferred to the milestone that uses
  them. Adding unused columns now would create schema without behavior.
- **Single-process assumption.** Startup recovery treats every active row as stale. Do not
  run multiple backend workers against one database. Multi-process support requires an
  owner/lease design first.
- **No start endpoint yet.** The reservation service is tested directly, but no HTTP route
  accepts a run until an orchestrator exists.
- **No Sheets preflight.** P0.1 requires a bounded canonical Sheets read before Auto Search
  reservation. That belongs to the Sheets integration milestone; M1's reservation service
  must not be wired to HTTP before then.
- **Source-checkout packaging.** Alembic assets live beside the source tree. Running from
  the repository works; installing the backend as a wheel with migrations is not yet
  packaged.
- **Future HTTP hardening.** There is no state-changing endpoint, so no Host/Origin guard
  is active yet. It must be added with `POST /search-runs`, together with a stable mapping
  from `BlockingReason` to API error codes.
- **Custom URL validation is deferred.** `permitted` currently means the operator approved
  that exact source. M1 has no fetcher, but the connector milestone must validate custom
  career URLs before any request (require `https`, reject loopback/private/link-local
  hosts, embedded credentials, and non-HTTP schemes) and re-check the source policy at
  fetch time.

## 14. What M1 deliberately leaves out

- No connector, scheduler, or orchestrator.
- No `POST /search-runs`, because accepting work without an executor would be misleading.
- No Sheets synchronization, Gmail scope, LinkedIn fetch, LLM, embeddings, or RAG.
- No React dashboard changes.
- No automatic recovery of an interrupted run.
