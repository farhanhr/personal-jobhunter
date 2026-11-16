# Personal Job Hunter

A single-user, local-first job discovery and organization application for Indonesia.
It helps one person find relevant opportunities from curated official company sources
and user-provided LinkedIn hiring posts. It does **not** apply for jobs, message
recruiters, or automate LinkedIn.

**Current status:** Core search, durable opportunity staging, local read APIs, the
Google Sheets authentication/preflight foundation, a durable outbox dispatcher, and the
manual Sync API are implemented. The `Run_History` projection and a dedicated job-feed
screen are still pending.

## Product boundaries

- One to three primary target roles; zero roles is invalid for Auto Search.
- At least one enabled, explicitly permitted company source is required; unreviewed or
  blocked sources do not qualify.
- Indonesia-only location scope, with an explicit remote policy and a review queue for
  unknown locations.
- Auto Search is manual and user-triggered only.
- Maximum two accepted runs per operational day; 12-hour cooldown; 06:00
  `Asia/Jakarta` reset; one active run.
- Google Sheets is the intended canonical user-facing record store; SQLite owns
  operational state, durable staging, and pending sync work. Synchronization is manual
  and never starts on its own.
- No background scheduler, auto-apply, recruiter messaging, or LinkedIn browser
  automation.

## Implemented so far

- **Search policy engine** — enforces the two-run operational-day limit, 12-hour
  cooldown, 06:00 `Asia/Jakarta` reset, and single-active-run lock in the backend with
  atomic SQLite transactions.
- **Target role management** — stores one to three primary roles and their explicit
  aliases in local SQLite.
- **Company source registry** — registers permitted company career sources, records
  review and approval state, and enables or disables a source.
- **Manual search runs** — starts only from an explicit user request, requires an
  idempotency key, and exposes lifecycle status and per-source results.
- **Greenhouse connector** — reads public, unauthenticated Greenhouse job boards with
  bounded timeout and response size. It does not submit applications or use browser
  automation.
- **Durable opportunity staging** — normalizes connector records, preserves source
  provenance and run references, and commits opportunities plus pending-sync outbox
  entries in one SQLite transaction before the run reaches a terminal state. A run with
  unsynced work reports `completed_pending_sync`.
- **Job feed and review API** — serves a paginated main feed and review queue at
  `GET /api/v1/jobs`, with stable opportunity identity, source provenance, safe external
  links, and run association.
- **Google Sheets foundation** — desktop OAuth with the Sheets-only scope, keyring-first
  credential storage with a private-file fallback, a bounded official Sheets API v4
  adapter, and read-only spreadsheet preflight with explicit safe initialization.
- **Durable outbox dispatcher** — projects staged opportunities and source provenance
  into `Opportunities` and `Job_Sources` with atomic claiming, payload-hash fencing,
  idempotent upsert keyed on `opportunity_id` / `source_id`, bounded `RAW` batches,
  exponential backoff, and restart recovery. It is invoked manually and never runs a
  connector or consumes search quota.

- **Manual Sync API** — `GET /api/v1/sync/status`, `POST /api/v1/sync/retry`, and
  `POST /api/v1/sync/outbox/{outbox_id}/requeue` expose and drive the dispatcher over
  loopback, with per-entity counts, blocked-work guidance, and deterministic
  duplicate-trigger handling.

The `Run_History` projection, automatic synchronization, and a dedicated job-feed screen
are **not implemented yet**.

## API surface

All endpoints are local and bound to loopback. Responses that expose mutable state use
`Cache-Control: no-store`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/health` | Liveness and version |
| `GET` | `/api/v1/search-policy` | Quota, cooldown, active-run, and eligibility state |
| `GET` | `/api/v1/settings/search` | Read target roles |
| `PUT` | `/api/v1/settings/search` | Replace target roles atomically |
| `GET` | `/api/v1/companies` | List registered company sources |
| `POST` | `/api/v1/companies` | Register a company source |
| `PATCH` | `/api/v1/companies/{company_id}` | Update policy state or enablement |
| `POST` | `/api/v1/search-runs` | Start one manual search run; requires `Idempotency-Key` |
| `GET` | `/api/v1/search-runs/{run_id}` | Read run status and sanitized source results |
| `GET` | `/api/v1/jobs` | Paginated main feed or review queue (`feed=main` or `review`) |
| `GET` | `/api/v1/sync/status` | Outbox counts by state and entity type; blocked work needing action |
| `POST` | `/api/v1/sync/retry` | Dispatch due `pending`/`retry_wait` projections once |
| `POST` | `/api/v1/sync/outbox/{outbox_id}/requeue` | Re-enable one `blocked` projection |

Example feed request:

```bash
curl "http://127.0.0.1:8000/api/v1/jobs?feed=main&page=1&page_size=25"
```

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, uv, FastAPI, Uvicorn, Pydantic, SQLAlchemy Core, Alembic, pytest, HTTPX, Ruff |
| Frontend | React, TypeScript, Vite, npm, Vitest, Testing Library |
| Network | Loopback only (`127.0.0.1`) |


## Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Node.js 20.19+ or 22.12+ and npm (Vite 8 requirement)
- No Docker, Redis, Celery, or Kubernetes is required.

## Backend — install and run

```bash
cd backend
uv sync
uv run uvicorn personal_job_finder_api.main:app \
  --host 127.0.0.1 --port 8000 --reload
```

Alternative (without the reload watcher):

```bash
cd backend
uv run python -m personal_job_finder_api.main
```

Backend URL: <http://127.0.0.1:8000>

On startup the backend creates the local data directory, applies pending Alembic
migrations, and marks stale `accepted`/`running` runs as `interrupted`. No search is
resumed automatically.

Health check:

```bash
curl http://127.0.0.1:8000/api/v1/health
```

Expected:

```json
{
  "status": "ok",
  "service": "personal-job-finder-api",
  "version": "0.1.0"
}
```

Search-policy status:

```bash
curl http://127.0.0.1:8000/api/v1/search-policy
```

It reports the operational day, accepted and remaining runs, cooldown, next eligible
time, active-run state, and stable blocking reasons.

Starting a run requires an explicit user action and a unique idempotency key:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/search-runs \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: 11111111-1111-4111-8111-111111111111' \
  -d '{}'
```

The backend re-checks quota, cooldown, target roles, and usable sources inside one
SQLite transaction before accepting the run. Reusing a key with a different request
body is rejected.

## Local data and migrations

The SQLite operational database defaults to:

```text
~/.local/share/personal-job-finder/personal-job-finder.sqlite3
```

Override the directory for development or tests with `PJF_DATA_DIR`. The database is
private local state and must never be committed.

Apply or inspect migrations from `backend/`:

```bash
cd backend
uv run alembic upgrade head
uv run alembic current
```

## Google Sheets setup

The repository does not contain Google OAuth client credentials or user tokens. The
foundation is optional until the sync dispatcher lands, but these steps prepare the
local connection.

1. In Google Cloud Console, enable the **Google Sheets API**.
2. Configure an OAuth consent screen. For personal use, **External** with publishing
   status **Testing** is sufficient while you are the only test user.
3. Create an OAuth client of type **Desktop app**, then download its JSON file.
4. Store the JSON outside Git, for example:

   ```text
   ~/.local/share/personal-job-finder/credentials/google-oauth-client.json
   ```

   Override the location with `GOOGLE_OAUTH_CLIENT_FILE`.
5. Create or choose a spreadsheet and set its ID from the spreadsheet URL:

   ```bash
   export GOOGLE_SHEETS_SPREADSHEET_ID='<spreadsheet-id>'
   ```

6. From `backend/`, authorize the account through your system browser:

   ```bash
   uv run python -m personal_job_finder_api.integrations.google_sheets.cli auth
   ```

   The browser returns through an ephemeral loopback callback on `127.0.0.1`. Only the
   `https://www.googleapis.com/auth/spreadsheets` scope is requested.

7. Run the read-only preflight:

   ```bash
   uv run python -m personal_job_finder_api.integrations.google_sheets.cli check
   ```

8. If preflight reports missing tabs or empty header rows, explicitly initialize them:

   ```bash
   uv run python -m personal_job_finder_api.integrations.google_sheets.cli init
   ```

   Initialization only creates missing tabs and empty header rows. It refuses to
   overwrite a non-empty header that does not match the accepted schema.

Credentials are stored in the OS keyring through `keyring`. If no keyring backend is
available, the application falls back to a private file at
`~/.local/share/personal-job-finder/credentials/google-token.json` with mode `0600`
and a `0700` directory. Tokens are never written to SQLite, outbox payloads, logs, or
the repository.

To disconnect the account and remove the stored credential:

```bash
cd backend
uv run python -m personal_job_finder_api.integrations.google_sheets.cli disconnect
```

Revoking access from the Google account page also works; the next command then reports
`reconnect_required` and the unusable credential is discarded automatically.

### Manual synchronization

Synchronization is explicit and process-local. It never runs a connector, never consumes
search quota, and never starts automatically. It is available both as a local command and
as the local API below.

```bash
cd backend
uv run python -m personal_job_finder_api.integrations.google_sheets.cli sync
```

Via the API:

```bash
curl http://127.0.0.1:8000/api/v1/sync/status

curl -X POST http://127.0.0.1:8000/api/v1/sync/retry \
  -H 'Origin: http://127.0.0.1:5173'
```

`POST /api/v1/sync/retry` runs one dispatch synchronously in the request, so the response
reports the real outcome rather than an optimistic acceptance. It first returns abandoned
`in_progress` work to the retry queue, then dispatches. When nothing is due it makes no
Google request at all. A second request while a dispatch is running receives
`409 sync_already_running` instead of starting another worker; this is a single-process
guarantee, not a distributed lock.

`GET /api/v1/sync/status` reads only local SQLite state, so it works even when the Google
credential or spreadsheet configuration is broken. It reports counts per state for both
`opportunity` and `job_source` projections, how many are due now, whether user action is
required, and a capped list of blocked projections with their sanitized error code. It
never returns payloads, tokens, spreadsheet IDs, or file paths.

`POST /api/v1/sync/outbox/{outbox_id}/requeue` re-enables exactly one blocked projection
after its cause is fixed. There is no bulk retry: silently clearing every blocked error
could resend work whose cause was never resolved. Unknown ids return `404`; a projection
that is not blocked returns `409`.

State-changing routes require an allowed `Origin` header, and all sync responses are
`Cache-Control: no-store`.

The command line remains equivalent:

```bash
uv run python -m personal_job_finder_api.integrations.google_sheets.cli sync
uv run python -m personal_job_finder_api.integrations.google_sheets.cli recover
uv run python -m personal_job_finder_api.integrations.google_sheets.cli requeue \
  --outbox-id outbox_opp_<id>
```

Behavior worth knowing:

- Rows are matched on `opportunity_id` and `source_id`. A retry after a lost response
  updates the existing row instead of appending a duplicate.
- `user_status` is user-owned. An update carries the value currently in the sheet, and a
  brand-new row starts at the contract default `new`.
- Transient failures (timeout, network, 429, 5xx) become `retry_wait` with capped
  exponential backoff; a bounded `Retry-After` hint is honored. Invalid credentials,
  permission errors, a missing spreadsheet, or a header mismatch become `blocked`.
- `recover` returns work abandoned by a crashed run to the retry queue without ever
  marking it synchronized. It only touches SQLite, so it works even when the stored
  Google credential is the problem:

  ```bash
  uv run python -m personal_job_finder_api.integrations.google_sheets.cli recover
  ```

- Every write is re-read before it is settled. `batchUpdate` is not transactional, so a
  partial failure can apply some rows; re-reading means a second attempt finds the row by
  identity instead of appending a duplicate.

- The accepted contract defines no delete or tombstone semantics, and deleting a row
  would destroy a `user_status` the user set. So when a record leaves the feed:
  - a projection that provably never left this machine is withdrawn locally;
  - a projection that may already be in the sheet is rewritten with
    `location_status = excluded`, keeping `user_status` intact;
  - `Job_Sources` rows are provenance and are left in place.

  Only a location-based exclusion has a contract-valid way to say "not in the feed".
  If a record were excluded for another reason, the projection is `blocked` with
  `withdrawal_unsupported` rather than being given a misleading status.

Blocked work is never retried automatically. After fixing the cause, re-enable it:

```bash
uv run python -m personal_job_finder_api.integrations.google_sheets.cli requeue \
  --outbox-id outbox_opp_<id>
```

Manual sorting of the spreadsheet is supported because identities are re-read on every
dispatch. Do not repurpose the reserved identity columns (`opportunity_id`, `source_id`);
they are the only upsert key. Two independent processes syncing to the same spreadsheet at
the same time are not supported, because row positions are not transactional.

External OAuth applications in **Testing** status can receive refresh tokens that expire
after seven days. A weekly reconnect may therefore be necessary until the app is
published or exempted; this is expected Google behavior, not a Sheets integration bug.

| Variable | Default | Purpose |
|---|---|---|
| `GOOGLE_SHEETS_SPREADSHEET_ID` | unset | Spreadsheet used by the sync foundation |
| `GOOGLE_OAUTH_CLIENT_FILE` | `<data-dir>/credentials/google-oauth-client.json` | Desktop OAuth client JSON |
| `GOOGLE_TOKEN_FILE` | `<data-dir>/credentials/google-token.json` | Keyring fallback token file |
| `GOOGLE_SHEETS_TIMEOUT_SECONDS` | `10` | Bounded Sheets request timeout |
| `GOOGLE_SHEETS_MAX_BATCH_RANGES` | `100` | Maximum ranges per value batch |
| `GOOGLE_SHEETS_BATCH_SIZE` | `25` | Maximum rows written per batch |
| `GOOGLE_OAUTH_TIMEOUT_SECONDS` | `300` | Loopback callback wait |
| `GOOGLE_KEYRING_SERVICE_NAME` | `personal-job-finder` | Keyring service label |

All values are local configuration. Never commit the client JSON, token file, or
spreadsheet ID.

## Frontend — install and run

Run the backend first, then:

```bash
cd frontend
npm install
npm run dev
```

Frontend URL: <http://127.0.0.1:5173>

Vite binds to `127.0.0.1:5173`, uses a strict port, and proxies `/api` to
`http://127.0.0.1:8000`. The dashboard fetches the real health endpoint, manages target
roles and company sources, shows current search eligibility, and can start one manual
run and poll its status. It never assumes the backend is healthy.

## Tests and checks

The test suite is fully offline: it uses sanitized fixtures and mocked HTTP responses.
Running it does not contact live job boards, Google Sheets, or LinkedIn.

Backend:

```bash
cd backend
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Frontend:

```bash
cd frontend
npm test
npm run typecheck
npm run build
npm run lint
```

## Continuous integration

`.github/workflows/verify.yml` runs on pushes to `main` and pull requests targeting
`main`. One Ubuntu job installs Python 3.12 with uv and Node.js 22 with npm, then runs
backend pytest, Ruff check/format, and the frontend tests, typecheck, lint, and
production build. The workflow uses no repository secrets and performs no deployment.

## License

MIT. See `LICENSE`.
