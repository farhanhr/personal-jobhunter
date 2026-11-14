# 05 — API Contracts

**Version:** 0.1.0  
**Base path:** `/api/v1`

This is the initial REST contract. Exact Pydantic class names and response fields may evolve, but frontend and backend must agree on the contract before implementation.

## 1. Common conventions

- JSON request/response.
- Timestamps use ISO 8601 UTC (`Z` suffix where serialized).
- UI renders dates/times in `Asia/Jakarta`.
- IDs are opaque strings.
- List endpoints support pagination.
- Errors use one consistent JSON structure.
- Backend validates all configuration; never trust frontend-only validation.

## 2. Health and dashboard

### `GET /health`
Returns local backend readiness.

### `GET /api/v1/dashboard/summary`
Returns:
- Total opportunities.
- New opportunities.
- Opportunities by target role.
- Opportunities by source.
- Opportunities by location.
- Current search policy summary.
- Pending Sheets sync count.
- Latest run status.

## 3. Search policy

### `GET /api/v1/search-policy`

Example response:

```json
{
  "timezone": "Asia/Jakarta",
  "operational_day": "2026-11-12",
  "max_runs": 2,
  "accepted_runs": 1,
  "remaining_runs": 1,
  "last_accepted_at": "2026-11-12T00:00:00Z",
  "cooldown_until": "2026-11-12T12:00:00Z",
  "cooldown_remaining_seconds": 0,
  "next_reset_at": "2026-11-12T23:00:00Z",
  "has_active_run": false,
  "can_start": true,
  "block_reason": null
}
```

The above times are examples only. Backend calculates values using current time and persisted run records.

## 4. Start search run

### `POST /api/v1/search-runs`

- No role/location override in the request body; use saved server-side configuration.
- Require an `Idempotency-Key` header.
- Validate configuration and source availability.
- Reserve quota in SQLite transaction before starting the orchestrator.
- Return HTTP `202 Accepted` when run is persisted and accepted.

Example response:

```json
{
  "run_id": "run_opaque_id",
  "status": "accepted",
  "accepted_at": "2026-11-12T03:00:00Z",
  "operational_day": "2026-11-12",
  "remaining_runs": 1
}
```

### Rejection behavior

| HTTP | Code | Meaning |
|---|---|---|
| 409 | `SEARCH_ALREADY_RUNNING` | Another run is active |
| 409 | `IDEMPOTENCY_KEY_CONFLICT` | Key reused with incompatible request |
| 422 | `SEARCH_CONFIGURATION_INVALID` | No valid roles/sources configured |
| 429 | `DAILY_QUOTA_EXHAUSTED` | Two runs already accepted |
| 429 | `COOLDOWN_ACTIVE` | Twelve-hour cooldown not complete |
| 503 | `INTEGRATIONS_UNAVAILABLE` | Required integration unavailable and no usable source remains |

For 429 cooldown response, include `Retry-After` seconds and a structured `next_eligible_at`. Quota reset alone does not override cooldown.

## 5. Search run status

### `GET /api/v1/search-runs/{run_id}`

Example:

```json
{
  "run_id": "run_opaque_id",
  "status": "running",
  "progress": {
    "stage": "normalizing",
    "completed_sources": 2,
    "total_sources": 4
  },
  "source_results": [
    {
      "source_id": "company_source_1",
      "status": "completed",
      "records_found": 37
    },
    {
      "source_id": "company_source_2",
      "status": "failed",
      "error_code": "SOURCE_TIMEOUT"
    }
  ]
}
```

### `GET /api/v1/search-runs?limit=20&cursor=...`
Returns recent run history.

## 6. Jobs

### `GET /api/v1/jobs`
Supported filters:
- `target_role_id`
- `country`
- `province`
- `city`
- `work_mode`
- `source_type`
- `status`
- `location_status`
- `q` (title/company text)
- pagination

### `GET /api/v1/jobs/{opportunity_id}`
Returns opportunity details and all source records.

## 7. Companies

- `GET /api/v1/companies`
- `POST /api/v1/companies`
- `PATCH /api/v1/companies/{company_id}`
- `DELETE /api/v1/companies/{company_id}` (soft disable preferred)

Company changes must be validated and reflected in the configured source registry.

## 8. Manual LinkedIn post intake

### `POST /api/v1/imports/linkedin-posts`

Example request:

```json
{
  "post_url": "https://www.linkedin.com/posts/example",
  "post_text": "We are hiring a Junior Data Analyst in Jakarta...",
  "author_name": "Example Recruiter",
  "captured_at": "2026-11-12T03:30:00Z"
}
```

Response returns an extraction preview and review status. This endpoint processes user-supplied content and must not fetch the LinkedIn URL.

Manual import does not consume Auto Search quota. It may be saved as `needs_review` until the user confirms extracted fields.

## 9. Sync

### `GET /api/v1/sync/status`
Returns pending, failed, and synced outbox counts.

### `POST /api/v1/sync/retry`
Retries only pending Google Sheets writes. It must not invoke source connectors and must not consume search quota.

## 10. Settings

- `GET /api/v1/settings`
- `PUT /api/v1/settings/search`
- `GET /api/v1/integrations`
- Google OAuth start/callback endpoints as required by installed-app flow.

Settings validation:
- One to three target roles.
- Unique role IDs.
- Aliases bound to a target role.
- Country fixed to Indonesia for MVP.
- Remote eligibility explicitly selected.
- Enabled source list must be non-empty before Auto Search.

## 11. Frontend polling

- After receiving 202, frontend polls `GET /search-runs/{id}`.
- Poll interval is modest (e.g. 2 seconds while active, then stop at terminal state).
- Do not poll when no active run.
- Backend persists status so page refresh can recover current run.
- Polling status does not consume search quota.

## 12. API security

- Localhost binding only by default.
- Validate Host and Origin for write endpoints.
- Do not expose credentials/tokens.
- Do not allow arbitrary URLs to be fetched by the manual post import endpoint (SSRF prevention).
- Validate all source URLs against configured connector/allowlist rules before server-side retrieval.
