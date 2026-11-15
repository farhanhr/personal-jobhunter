# Personal Job Hunter

A single-user, local-first job discovery and organization application for Indonesia.
It helps one person find relevant opportunities from curated official company sources
and user-provided LinkedIn hiring posts. It does **not** apply for jobs, message
recruiters, or automate LinkedIn.

**Current status:** Core search, durable opportunity staging, and local read APIs are
implemented. Google Sheets synchronization and a dedicated job-feed screen are still
pending.

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
  operational state, durable staging, and pending sync work. Sheets synchronization is
  not implemented yet.
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

The Google Sheets transport and sync worker are **not implemented yet**; staged records
currently remain local until that work is completed. A dedicated job-feed screen in the
dashboard is also not implemented yet.

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
  -H 'Idempotency-Key: local-demo-001' \
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

## License

MIT. See `LICENSE`.
