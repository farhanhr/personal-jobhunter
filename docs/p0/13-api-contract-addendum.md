# 13 — P0.1 API Contract Addendum

**Version:** 0.1.0  
**Base path:** `/api/v1`  
**Normative reference:** P0 05 plus P0.1 reconciliation.

## 1. Common pagination

List endpoints use:
- `page` (default 1, minimum 1)
- `page_size` (default 25, maximum 100)
- stable ordering (`first_seen_at_utc DESC, opportunity_id ASC` for jobs)

Response envelope:

```json
{
  "items": [],
  "page": 1,
  "page_size": 25,
  "total_items": 0,
  "total_pages": 0
}
```

## 2. Common error envelope

```json
{
  "error": {
    "code": "search_cooldown_active",
    "message": "A search run is not currently eligible.",
    "details": {
      "next_eligible_at": "2026-11-13T12:00:00Z"
    },
    "request_id": "req_..."
  }
}
```

Never return stack traces, OAuth tokens, API keys, raw provider headers, or unredacted upstream payloads.

## 3. Search policy and idempotency

### `GET /api/v1/search-policy`

Must include:
- `timezone`
- `operational_day`
- `max_runs`
- `accepted_runs`
- `remaining_runs`
- `last_accepted_at`
- `cooldown_until`
- `cooldown_remaining_seconds`
- `next_eligible_at`
- `next_reset_at`
- `has_active_run`
- `can_start`
- `block_reason`

### `POST /api/v1/search-runs`

Require `Idempotency-Key` header containing a UUID. Missing key returns `400 missing_idempotency_key`.

Behavior:
- Same key + same request hash: return the original run representation; do not reserve quota again.
- Same key + different request hash: return `409 idempotency_key_reuse`.
- Different key while active/cooldown/quota exhausted: reject with a typed `409` response; do not consume quota.
- Invalid target roles/no usable configured source: reject before reservation.
- Canonical Sheets snapshot/preflight failure: reject before reservation.
- Once reservation commits, quota is consumed even if later connectors fail, the process stops, or Sheets sync fails.

Accepted response is HTTP `202`:

```json
{
  "run_id": "run_...",
  "status": "accepted",
  "accepted_at_utc": "...Z",
  "operational_day": "YYYY-MM-DD",
  "remaining_runs": 1,
  "status_url": "/api/v1/search-runs/run_..."
}
```

## 4. Search run endpoints

- `GET /api/v1/search-runs` — paginated history.
- `GET /api/v1/search-runs/{run_id}` — run status and progress.
- `GET /api/v1/search-runs/{run_id}/sources` — per-source result/status.

Terminal statuses: `completed`, `completed_with_warnings`, `completed_pending_sync`, `failed`, `interrupted`.

## 5. Jobs and contacts

- `GET /api/v1/jobs` — paginated read-model list; supports `target_role_id`, `province`, `city`, `work_mode`, `user_status`, `availability_status`, `source_type`, `q`.
- `GET /api/v1/jobs/{opportunity_id}` — detail plus source references.
- `PATCH /api/v1/jobs/{opportunity_id}/user-status` — update `new|saved|ignored`; writes through outbox to canonical Sheets and updates the local projection after success.
- `GET /api/v1/contacts` — paginated explicit recruitment contacts.
- `GET /api/v1/contacts/{contact_id}` — contact and source context.
- `PATCH /api/v1/contacts/{contact_id}/verification` — set `user_confirmed|invalid`; never infer identity verification from email syntax alone.

## 6. Local configuration and company sources

- `GET /api/v1/settings/search` — target roles and search preferences.
- `PUT /api/v1/settings/search` — replace validated settings; enforce one to three enabled primary roles and explicit aliases.
- `GET /api/v1/company-sources` — local SQLite source registry.
- `POST /api/v1/company-sources` — add a curated source.
- `PATCH /api/v1/company-sources/{source_id}` — edit/enable/disable.
- `DELETE /api/v1/company-sources/{source_id}` — remove local configuration; preserve already discovered records.

Settings changes during an active run apply to the next run only. Each accepted run stores immutable `config_snapshot_json`.

## 7. Manual LinkedIn post intake

- `POST /api/v1/imports/linkedin-posts` accepts `source_url` and `source_text` supplied by the user.
- `GET /api/v1/imports/linkedin-posts/{import_id}` returns extracted preview and review state.
- `POST /api/v1/imports/linkedin-posts/{import_id}/confirm` confirms selected fields and stages records for Sheets sync.

This import does not consume search quota. The backend must not issue HTTP requests to `source_url`, follow redirects, fetch OpenGraph metadata, or render a preview from LinkedIn.

No Gmail endpoints are included in MVP.

## 8. Synchronization endpoints

- `GET /api/v1/sync/status` — counts by outbox state and last successful sync.
- `POST /api/v1/sync/retry` — retries due `pending`/`retry_wait` records only.
- `POST /api/v1/sync/outbox/{outbox_id}/requeue` — explicit user action after a `blocked` cause is resolved.
- `POST /api/v1/sheets/refresh` — explicit refresh of canonical Sheets tabs into the local read model; does not consume Auto Search quota.

Retry/refresh endpoints never call job source connectors and never consume Auto Search quota.

## 9. Health and dashboard

- `GET /api/v1/health` — local backend health and SQLite readiness; do not expose secrets.
- `GET /api/v1/dashboard/summary` — totals and grouping from local read model plus operational counters.

## 10. State-changing request security

- Loopback only by default.
- Validate Host and Origin for state-changing endpoints.
- Use CSRF protections appropriate for a local browser application.
- Reject arbitrary external URL fetch requests; only the user-supplied LinkedIn URL is stored as text and never fetched.
- Never send Google tokens or secrets to the browser.

