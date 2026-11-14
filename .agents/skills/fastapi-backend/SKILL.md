---
name: fastapi-backend
description: Use when implementing FastAPI routes, request/response models, dependencies, error handling, middleware, startup/shutdown, or backend service boundaries.
---

# FastAPI Backend Engineering

- Keep API route handlers thin: parse/validate request, call a service, return a response model.
- Put business rules in services/domain modules, not in route functions.
- Use Pydantic models for request and response contracts.
- Use dependency injection for repositories, settings, and connector factories.
- Set explicit timeouts on all external network calls.
- Use structured HTTP errors with stable application error codes; do not expose stack traces or tokens.
- Use `202 Accepted` when a search run is accepted and continues in-process; return a run ID and let the client poll status.
- Use idempotency keys for the start-run endpoint so a retried client request does not create duplicate runs.
- Do not create a hidden periodic scheduler or auto-resume a process after restart.
- Bind locally to `127.0.0.1` by default. Do not set `0.0.0.0` without explicit approval.
- Manage in-process task lifetime and shutdown behavior explicitly. On restart, reconcile stale `running` records to `interrupted` unless a tested recovery design exists.
- Keep OpenAPI schemas accurate; update API docs and frontend types with each contract change.

## Testing

Use FastAPI's test client and dependency overrides. Do not require live Google, Gmail, ATS or LinkedIn access in unit tests.
