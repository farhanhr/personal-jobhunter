# Personal Job Hunter

A single-user, local-first job discovery and organization application for Indonesia.
It helps one person find relevant opportunities from curated official company sources
and user-provided LinkedIn hiring posts. It does **not** apply for jobs, message
recruiters, or automate LinkedIn.

**Current status:** In development

## Product boundaries

- One to three primary target roles; zero roles is invalid for Auto Search.
- At least one enabled, explicitly permitted company source is required; unreviewed or
  blocked sources do not qualify.
- Indonesia-only location scope, with an explicit remote policy and a review queue for
  unknown locations.
- Auto Search is manual and user-triggered only.
- Maximum two accepted runs per operational day; 12-hour cooldown; 06:00
  `Asia/Jakarta` reset; one active run.
- Google Sheets is the canonical user-facing record store; SQLite owns operational
  state and private settings.
- No background scheduler, auto-apply, recruiter messaging, or LinkedIn browser
  automation.

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

Search-policy status (read-only; no start-run endpoint exists in M1):

```bash
curl http://127.0.0.1:8000/api/v1/search-policy
```

It reports the operational day, accepted and remaining runs, cooldown, next eligible
time, active-run state, and stable blocking reasons.

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
`http://127.0.0.1:8000`. The dashboard fetches the real health endpoint and shows a
loading, connected, or unavailable state. It never assumes the backend is healthy.

## Tests and checks

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
