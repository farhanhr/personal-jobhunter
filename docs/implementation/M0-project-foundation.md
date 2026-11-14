# M0 — Project Foundation

**Status:** Implemented  
**Scope:** Minimal full-stack foundation only  
**Date:** 2026-11-13  
**License decision:** MIT with copyright holder `farhanhr`, approved by the project owner on 2026-11-13 (see `LICENSE`).

## 1. What was implemented

M0 creates the smallest working vertical slice of the application:

- A FastAPI backend with a versioned health endpoint.
- A React + TypeScript + Vite dashboard that calls that endpoint through a dev proxy.
- Backend tests, linting, and formatting checks.
- Frontend component tests, TypeScript checking, and a production build.
- Loopback-only binding on both sides.

M0 deliberately does **not** implement SQLite schema, search quota or cooldown, job
connectors, Google Sheets, LinkedIn import, AI/LLM, embeddings, RAG, or Auto Search
business logic. Gmail is deferred from the MVP per ADR-017. Those belong to later
milestones or are out of MVP scope.

Tailwind CSS, shadcn/ui, and TanStack Query are part of the accepted long-term stack but
are intentionally not installed in M0. This screen makes one request and has no complex
server-state cache; adding those libraries now would add configuration without exercising
their benefits. They should arrive with the first data-driven UI.

## 2. Files and structure

```text
backend/
  pyproject.toml
  uv.lock
  .python-version
  src/personal_job_finder_api/
    __init__.py
    main.py                  Application factory and entrypoint
    core/config.py            Small typed settings object
    api/v1/health.py          Health route and response model
    api/v1/router.py          API v1 router aggregation
  tests/test_health.py

frontend/
  package.json
  package-lock.json
  vite.config.ts              Loopback host, port, strict port, /api proxy
  index.html
  src/
    main.tsx
    App.tsx                   Dashboard shell and connection states
    App.test.tsx              Loading / connected / unavailable tests
    api/health.ts             Typed health client and payload guard
    setupTests.ts
```

## 3. Architecture and data flow

```text
Browser (127.0.0.1:5173)
  |
  |  GET /api/v1/health
  v
Vite dev proxy
  |
  |  http://127.0.0.1:8000/api/v1/health
  v
FastAPI (127.0.0.1:8000)
  |
  |  api/v1/router.py -> api/v1/health.py
  v
HealthResponse { status, service, version }
```

The backend owns the contract. The frontend does not import backend code, does not
read credentials, and does not assume success. It renders one of three explicit
states based on the actual request result:

1. `loading` — request still in flight.
2. `connected` — HTTP success and a validated payload.
3. `unavailable` — network failure, non-2xx response, or malformed payload.

`src/api/health.ts` validates the payload at the boundary. A response such as
`{"status":"ok"}` is rejected instead of being treated as healthy.

## 4. Why these technologies

**FastAPI + Pydantic.** FastAPI gives typed routing, automatic request/response
validation, and an OpenAPI schema with little ceremony. Pydantic models make the
external contract explicit, which matters when the frontend and backend are separate
projects.

**uv.** uv manages the Python interpreter, virtual environment, dependency resolution,
lockfile, and command execution. `uv.lock` makes installs reproducible.

**React + TypeScript + Vite.** React is appropriate for a local SPA dashboard. Vite
provides a fast dev server and a small production build. TypeScript strict mode is
enabled in both `tsconfig.app.json` and `tsconfig.node.json`, so contract mistakes are
caught at compile time.

**Vitest + Testing Library.** They test behavior the user sees (loading, connected,
unavailable) rather than implementation details. Tests mock `fetch`, so they do not
depend on a running backend.

**Vitest configuration inside `vite.config.ts`.** One config controls the dev server,
proxy, version define, and tests. Fewer moving parts for a beginner.

## 5. Frontend/backend communication

During development:

- Vite runs at `127.0.0.1:5173`.
- FastAPI runs at `127.0.0.1:8000`.
- Vite proxies every `/api` request to `http://127.0.0.1:8000`.
- The frontend calls the relative path `/api/v1/health`, so no backend host is
  hardcoded in browser code.

This relative-path approach also works when FastAPI serves the built frontend from a
single origin in normal local use.

## 6. How to run

Requires Python 3.12, uv, Node.js 20.19+ or 22.12+, and npm.

Backend:

```bash
cd backend
uv sync
uv run uvicorn personal_job_finder_api.main:app \
  --host 127.0.0.1 --port 8000 --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

URLs:

- Frontend: <http://127.0.0.1:5173>
- Backend health: <http://127.0.0.1:8000/api/v1/health>

## 7. How to test

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

The backend tests assert the exact health JSON and that no extra fields are exposed.
The frontend tests assert the request path and headers, the loading state, successful
connection, network failure, non-2xx response, malformed payload handling, and a non-`ok`
health status.

## 8. What remains for M1

M1 should introduce the first piece of real domain behavior, not more scaffolding.
The recommended next slice is the pure search-policy domain:

- operational day calculation for `Asia/Jakarta` with the 06:00 boundary;
- 12-hour cooldown calculation;
- quota eligibility rules;
- boundary tests at 05:59:59, 06:00:00, exactly 12 hours, and reset while cooldown is
  active.

Database schema, atomic reservation, connectors, and Sheets sync follow after that.
