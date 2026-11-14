# 07 — Architecture Decision Records (ADR)

**Version:** 0.1.0  
**Status:** Baseline decisions; revisit only with explicit evidence/requirement

## ADR-001 — Use a modular monolith

**Status:** Accepted

Context: Single-user local app, manual search execution, small number of integrations.

Decision: One FastAPI backend organized into explicit modules.

Consequences:
- Simpler development and debugging.
- No distributed deployment or service-to-service communication.
- Boundaries must be maintained in code to prevent a monolith becoming tangled.

## ADR-002 — Use React + TypeScript + Vite for frontend

**Status:** Accepted

Context: User wants more UI flexibility than Streamlit and expects future evolution.

Decision: React SPA with Vite.

Consequences:
- Frontend separated from backend.
- Requires JavaScript/TypeScript learning.
- Static build can be served by FastAPI locally.
- SSR/SEO is not a current requirement, so Next.js is deferred.

## ADR-003 — Use FastAPI for backend

**Status:** Accepted

Context: Python is preferred for data processing and future AI integration.

Decision: FastAPI owns REST endpoints, business rules, connectors, normalization, and integration services.

Consequences:
- Pydantic schemas can validate API contracts.
- Python ecosystem available for data/AI.
- Frontend consumes explicit HTTP contracts.

## ADR-004 — Search is user-triggered only

**Status:** Accepted

Context: Laptop is not always powered on; user wants control over when external discovery happens.

Decision:
- No APScheduler or automatic source polling.
- Auto Search is a dashboard action.
- One action starts one complete run across all configured roles/sources.

Consequences:
- No missed scheduled run while laptop is off.
- No hidden background job.
- Search runs only while backend is running.

## ADR-005 — Search policy: 2 runs/day, 12-hour cooldown, 06:00 WIB reset

**Status:** Accepted

Decision:
- Two accepted runs per operational day.
- Twelve hours minimum between accepted runs.
- Operational day begins at 06:00 `Asia/Jakarta`.
- One active run maximum.
- Backend/SQLite enforces policy atomically.
- Accepted run consumes quota even if it later fails or is interrupted.

Consequences:
- Need accurate timezone handling and transaction tests.
- Reset does not bypass cooldown.
- Manual import and retry-sync do not count as search runs.

## ADR-006 — No automated LinkedIn browser access

**Status:** Accepted

Context: User wants to minimize risk of LinkedIn account restrictions.

Decision:
- No LinkedIn scraping, browser automation, session/cookie access, stealth, or bypass.
- Use user-supplied post URL/text and optional notification email intake.

Consequences:
- Not every LinkedIn post will be discovered automatically.
- System prioritizes account-risk reduction over full coverage.
- LinkedIn content provenance remains recorded.

## ADR-007 — Curated permitted company sources

**Status:** Accepted

Decision:
- User maintains a registry of official company career sources.
- Initial adapter candidates are public Greenhouse, Lever, and Ashby posting interfaces.
- Custom connectors only where access method is permitted.
- Source-specific limits and policies are respected.

Consequences:
- System does not search every company on the internet.
- Adding a company requires its official career source/board information.

## ADR-008 — Google Sheets for user-visible job records; SQLite for operational control

**Status:** Accepted

Decision:
- Google Sheets contains opportunities, job sources, contacts, companies, and run summaries.
- SQLite contains quota/run state, cache/checkpoints, and durable sync outbox.

Consequences:
- Stable IDs and idempotent sync are required.
- Sheets is not used for atomic quota locking.
- Pending records remain locally staged if Sheets is unavailable.

## ADR-009 — No LLM/RAG in MVP

**Status:** Accepted

Decision:
- Start with deterministic parsing, role aliases, location rules, and explainable relevance.
- Add embeddings/LLM only after a labeled evaluation dataset and baseline exist.
- RAG is deferred until a concrete knowledge-retrieval use case such as tailored CV.

Consequences:
- Lower cost and simpler debugging.
- AI features can be measured against a deterministic baseline.
- No vector database required initially.

## ADR-010 — Localhost-only deployment for MVP

**Status:** Accepted

Decision:
- FastAPI binds to loopback only.
- No public hosting.
- Backend serves frontend production assets.
- Development may use Vite dev server and FastAPI separately.

Consequences:
- No multi-user authentication in MVP.
- Local machine security and OAuth token protection remain important.

## ADR-011 — No distributed job queue in MVP

**Status:** Accepted

Decision:
- One managed in-process orchestrator.
- Persist run state in SQLite.
- Interrupted runs are not auto-resumed.
- Celery/Redis or another durable worker is deferred.

Consequences:
- Simple operation for one user.
- Long-running work ends if backend process stops.
- Outbox protects data already staged before shutdown.

## ADR-012 — Use HTTP polling for run progress

**Status:** Accepted

Decision:
- `POST` run returns 202.
- Frontend polls run status endpoint while a run is active.
- WebSockets/SSE are deferred.

Consequences:
- Easier to implement and debug.
- Slightly less immediate than push updates, but adequate for local MVP.
