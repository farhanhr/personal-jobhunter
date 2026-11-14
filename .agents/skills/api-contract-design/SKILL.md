---
name: api-contract-design
description: Use when designing or changing REST endpoints, request/response schemas, status codes, validation errors, polling, idempotency, or frontend-backend integration.
---

# API Contract Design

- Treat the API as a contract between independently testable frontend and backend applications.
- Use `/api/v1` for application endpoints.
- Use resource-oriented routes and correct HTTP methods.
- Define Pydantic request/response models and matching TypeScript types.
- Return stable machine-readable error codes and actionable human messages.
- Return `202 Accepted` for accepted long-running search runs; do not keep the HTTP request open for the entire search.
- Include `run_id`, status, accepted timestamp, and a status URL or documented polling endpoint.
- Require an idempotency key for start-run requests; persist the key/result so retries are safe.
- Use `409 Conflict` for active-run or state conflicts, and `429 Too Many Requests` for cooldown/quota rejection with a clear `next_eligible_at` where applicable.
- Do not trust frontend countdowns or remaining quota; the backend is authoritative.
- Document pagination, sorting, filtering, timezone formats, and nullability.
- Avoid leaking raw provider responses, tokens, internal filesystem paths, or stack traces.

## Required tests

Contract tests for valid payloads, invalid payloads, missing fields, null fields, duplicate idempotency keys, and policy rejection.
