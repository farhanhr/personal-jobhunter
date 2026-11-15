# Personal Job Hunter

A single-user, local-first job discovery and organization application for Indonesia.
It helps one person find relevant opportunities from curated official company sources
and user-provided LinkedIn hiring posts. It does **not** apply for jobs, message
recruiters, or automate LinkedIn.

**Current status:** Core search, durable opportunity staging, local read APIs, and the
Google Sheets authentication/preflight foundation are implemented. The outbox
dispatcher and a dedicated job-feed screen are still pending.

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
  operational state, durable staging, and pending sync work. Authentication and
  spreadsheet preflight exist, but outbox synchronization is not implemented yet.
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

The outbox dispatcher, `Job_Sources` projection, manual Sync action, and automatic
synchronization are **not implemented yet**; staged records currently remain local until
that work is completed. A dedicated job-feed screen in the dashboard is also not
implemented yet.

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
