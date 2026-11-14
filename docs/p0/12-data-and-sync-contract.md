# 12 — P0.1 Data and Synchronization Contract

**Version:** 0.1.0  
**Normative reference:** `docs/p0/11-architecture-reconciliation.md`

## 1. Storage ownership

| Data | Authoritative location | Local/UI behavior |
|---|---|---|
| Opportunities | Google Sheets `Opportunities` | Mirrored into SQLite read model; dashboard queries local model |
| Job sources/provenance | Google Sheets `Job_Sources` | Mirrored into SQLite read model |
| Explicit recruiter contacts | Google Sheets `Contacts` | Mirrored into SQLite read model |
| Run summaries | Google Sheets `Run_History` | Local SQLite has full operational run state |
| Dashboard formulas/charts | Google Sheets `Dashboard` | Formula-only presentation; no backend business logic |
| Target roles and aliases | SQLite `target_roles` | Managed through local Settings UI |
| Location/remote/job-type preferences | SQLite `app_settings` | Managed through local Settings UI |
| Company source registry | SQLite `company_sources` | Managed through local Companies UI |
| Quota, cooldown, active run | SQLite | Never stored/enforced in Sheets |
| Outbox and source cache | SQLite | Internal operational state |

There is no required `Companies` or `Search_Config` tab in the MVP. A later read-only mirror can be added without changing authority.

## 2. Google Sheets tabs

### `Opportunities`

Required fields:
- `opportunity_id` (stable UUID, unique)
- `company_name`
- `job_title_original`
- `job_title_normalized`
- `target_role_id`
- `role_match_type`
- `role_match_reason`
- `location_raw`
- `country`
- `province`
- `city`
- `location_status` (`eligible`, `review`, `excluded`)
- `work_mode` (`onsite`, `hybrid`, `remote`, `unknown`)
- `remote_scope` (`indonesia_only`, `worldwide`, `country_restricted`, `unknown`, `not_applicable`)
- `employment_type` (nullable)
- `experience_level` (nullable)
- `description` (source excerpt/full text according to source policy and size limit)
- `required_skills_json`
- `posted_at` (nullable)
- `deadline` (nullable; explicit evidence only)
- `availability_status` (`open`, `closed_confirmed`, `unknown`)
- `user_status` (`new`, `saved`, `ignored`)
- `first_seen_at_utc`
- `last_seen_at_utc`
- `created_at_utc`
- `updated_at_utc`

Do not include `not_seen` or `possibly_closed` in the MVP enum. Do not calculate candidate-fit score in MVP.

### `Job_Sources`

Required fields:
- `source_id` (stable UUID)
- `opportunity_id`
- `source_type` (`greenhouse`, `lever`, `ashby`, `permitted_career_page`, `linkedin_post_manual`)
- `source_url`
- `external_source_id` (nullable)
- `author_name` (nullable, only from user-supplied/publicly provided text)
- `author_role` (nullable, only if explicit)
- `source_text_excerpt` (nullable)
- `posted_at` (nullable)
- `captured_at_utc`
- `last_seen_at_utc` (nullable)
- `provenance_method` (`ats_api`, `permitted_page`, `manual_user_input`)
- `observation_status` (`seen`, `source_unavailable`, `unknown`)

`source_unavailable` means the connector could not establish an observation; it is not evidence of closure.

### `Contacts`

- `contact_id` (stable UUID)
- `source_id`
- `contact_name` (nullable, only if explicit and recruitment-relevant)
- `email`
- `contact_context`
- `extraction_method` (`regex`, `manual`)
- `confidence` (extraction confidence only)
- `verification_status` (`unverified`, `user_confirmed`, `invalid`)

Never guess an email from a name or company domain.

### `Run_History`

User-facing summary only:
- `run_id`
- `operational_day`
- `accepted_at_utc`
- `started_at_utc`
- `finished_at_utc`
- `status`
- `sources_total`
- `sources_succeeded`
- `sources_failed`
- `sources_skipped`
- `records_found`
- `records_new`
- `records_updated`
- `records_filtered`
- `pending_sync_count`
- `summary`

SQLite remains authoritative for quota and lifecycle details.

### `Dashboard`

Formula/chart-only summary. Do not implement filtering, matching, quota, or status logic through spreadsheet formulas.

## 3. SQLite schema responsibilities

Minimum tables:

### `search_runs`

`run_id`, `idempotency_key` (unique), `request_hash`, `operational_day`, `status`, `accepted_at_utc`, `started_at_utc`, `finished_at_utc`, `config_snapshot_json`, `progress_json`, `source_summary_json`, `error_code`, `error_summary`.

### `search_policy_state`

A singleton/controlled row for `last_accepted_at_utc`, `active_run_id`, and current operational policy version. Accepted run count is derivable by `operational_day` from `search_runs`; do not maintain two independently mutable counters.

### `target_roles`

`role_id`, `canonical_name`, `aliases_json`, `excluded_terms_json`, `enabled`, `sort_order`, `created_at_utc`, `updated_at_utc`. Enforce 1–3 enabled primary roles for Auto Search.

### `app_settings`

Key/value JSON for country (fixed to Indonesia in MVP), provinces/cities, remote eligibility policy, optional employment type/seniority filters, UI preferences, and schema/config version.

### `company_sources`

`company_source_id`, `company_name`, `official_company_url`, `career_url`, `source_type`, `board_identifier`, `enabled`, `access_policy_url`, `policy_notes`, `max_pages`, `max_requests_per_run`, `timeout_seconds`, `created_at_utc`, `updated_at_utc`.

### Read-model tables

`opportunities_read_model`, `job_sources_read_model`, `contacts_read_model`, plus a `sheets_sync_state` row with `last_full_refresh_at_utc`, `spreadsheet_schema_version`, and refresh outcome. They mirror Sheets rows and can be rebuilt from the canonical tabs.

### `sync_outbox`

`outbox_id`, `run_id` (nullable for manual imports), `destination`, `entity_type`, `entity_id`, `payload_json`, `payload_hash`, `status`, `attempt_count`, `max_attempts`, `next_attempt_at_utc`, `last_error_code`, `last_error_at_utc`, `created_at_utc`, `updated_at_utc`, `synced_at_utc`.

Unique current-projection key: `(destination, entity_type, entity_id)`.

### `source_cache`

`cache_key`, `source_id`, `payload_json` or a safe local payload reference, `etag`, `last_modified`, `fetched_at_utc`, `expires_at_utc`.

### Optional `manual_imports`

Tracks manual LinkedIn post intake and review status. It must store supplied text and URL as provenance but never fetch the URL.

## 4. Read-model refresh

- Dashboard list/detail endpoints query SQLite read-model tables.
- On application startup, display last cached data; do not make a search request or perform silent job discovery.
- `Refresh from Sheets` explicitly refreshes the local projection and does not consume search quota.
- Auto Search performs a bounded canonical Sheets read/preflight before quota reservation. If canonical data cannot be read, reject before quota reservation.
- Successful Sheets writes update both canonical tabs and local read model.
- Manual spreadsheet edits become visible after `Refresh from Sheets` or the next Auto Search preflight.
- Display `last_full_refresh_at_utc` and a stale-data indicator.

## 5. Outbox state machine

```text
pending -> in_progress -> synced
                    \-> retry_wait -> in_progress
                    \-> blocked

stale in_progress after restart -> retry_wait
blocked -> explicit user requeue after cause is corrected -> pending
```

Transient errors: 429, 5xx, timeouts, connection failures. Use bounded exponential backoff and provider guidance.

Permanent/configuration errors: invalid range/schema, missing tab, revoked permission, missing scope. Mark `blocked`; surface a clear action and sanitized error code.

Retry endpoints never run source connectors and never consume Auto Search quota.

## 6. Manual import and run relationship

Manual LinkedIn import is separate from Auto Search. It requires URL + text supplied by the user, runs local parsing, shows a review step, and writes accepted records via outbox. It consumes no Auto Search quota. It must not fetch or preview the LinkedIn URL.

