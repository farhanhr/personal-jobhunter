# 04 — Data Model and Google Sheets Schema

**Version:** 0.1.0

## 1. Domain entities

Core entities:
- `Company`
- `Opportunity`
- `JobSource`
- `Contact`
- `TargetRole`
- `SearchRun`

Relationships:
- One company can have many opportunities.
- One opportunity can have multiple source records.
- One source can include zero or more explicit recruitment contacts.
- One search run processes multiple sources and produces multiple records.

## 2. Opportunity vs Source

Do not equate one post/listing with one unique job opportunity.

Example:
- Company career page publishes Data Analyst role.
- Recruiter A shares the same opening in a LinkedIn post.
- Recruiter B reposts the opening.

These may refer to one Opportunity and three JobSource records. Preserve all source URLs and author/provenance information.

## 3. Google Sheets tabs

### 3.1 `Opportunities`

Suggested columns:

| Column | Type | Description |
|---|---|---|
| `opportunity_id` | string | Stable application UUID |
| `company_id` | string | Company reference |
| `company_name` | string | Display name |
| `job_title` | string | Original/canonical title |
| `target_role_id` | string | One of configured target roles |
| `role_relevance_score` | number | Deterministic role relevance; not candidate-fit score |
| `role_match_reason` | string | Explainable match reason |
| `location_raw` | string | Original location text |
| `country` | string | Normalized country |
| `province` | string | Normalized Indonesian province when known |
| `city` | string | Normalized city when known |
| `location_status` | string | `eligible`, `review`, `excluded` |
| `work_mode` | string | `onsite`, `hybrid`, `remote`, `unknown` |
| `remote_scope` | string | `indonesia`, `worldwide`, `restricted`, `unknown`, `not_applicable` |
| `employment_type` | string | Full-time, internship, etc. |
| `experience_level` | string | Entry-level, junior, mid, etc. |
| `description` | string | Description or controlled excerpt |
| `required_skills_json` | string | JSON array |
| `posted_at` | datetime/string | Source-posted timestamp if known |
| `deadline` | date/string | Application deadline if explicitly provided |
| `first_seen_at` | datetime | First discovery |
| `last_seen_at` | datetime | Last successful observation |
| `status` | string | `new`, `saved`, `ignored`, `possibly_closed`, `closed`, `unknown` |
| `created_at` | datetime | Record creation |
| `updated_at` | datetime | Last update |

Do not label `role_relevance_score` as a candidate fit score. Without a verified user career profile/CV, the system can assess role relevance but cannot honestly assess candidate fit.

### 3.2 `Job_Sources`

| Column | Description |
|---|---|
| `source_id` | Stable source UUID |
| `opportunity_id` | Related opportunity |
| `source_type` | `greenhouse`, `lever`, `ashby`, `career_page`, `linkedin_post`, `linkedin_alert_email` |
| `source_url` | Original URL |
| `external_source_id` | ID provided by source, if available |
| `author_name` | Public author name if user-supplied/available |
| `author_role` | Role only if explicitly known |
| `source_text` | Text excerpt or supplied post content |
| `posted_at` | Original post time if known |
| `captured_at` | Time captured |
| `last_checked_at` | Last successful source check |
| `provenance_note` | How the content entered the app |

### 3.3 `Contacts`

| Column | Description |
|---|---|
| `contact_id` | Stable contact UUID |
| `source_id` | Source containing the contact |
| `contact_name` | Name only if explicitly available and relevant |
| `email` | Explicitly provided email address |
| `contact_context` | Sentence/context around the email |
| `extraction_method` | `regex`, `manual`, optional future LLM |
| `confidence` | Extraction confidence, not identity verification |
| `verification_status` | `unverified`, `user_confirmed`, `invalid` |

Never infer or guess an email address based on a person's name or company domain. Store only contact details relevant to recruitment and explicitly present in supplied content.

### 3.4 `Companies`

| Column | Description |
|---|---|
| `company_id` | Stable company UUID |
| `company_name` | Company display name |
| `company_website` | Official website |
| `career_url` | Official career page |
| `ats_type` | Greenhouse, Lever, Ashby, custom, none |
| `ats_board_token` | Public board identifier where applicable |
| `enabled` | Whether source is included in Auto Search |
| `access_notes` | Source-specific access/policy notes |
| `created_at` | Creation time |
| `updated_at` | Update time |

Treat the ATS board token as configuration, not necessarily a secret. Never store API credentials in this tab.

### 3.5 `Run_History`

| Column | Description |
|---|---|
| `run_id` | Search run UUID |
| `operational_day` | Jakarta operational date |
| `accepted_at` | Accepted time |
| `started_at` | Actual start |
| `finished_at` | Finish |
| `status` | Terminal/current status |
| `sources_total` | Enabled source count |
| `sources_succeeded` | Successful source count |
| `sources_failed` | Failed source count |
| `records_found` | Raw records |
| `records_new` | New opportunities |
| `records_updated` | Updated opportunities |
| `records_ignored` | Filtered/duplicate count |
| `pending_sync_count` | Pending Sheets writes |
| `summary` | Human-readable run summary |

`Run_History` is a user-facing projection. SQLite remains authoritative for quota and active execution state.

### 3.6 `Dashboard`

Optional formula/chart sheet summarizing:
- Opportunities by target role.
- Opportunities by province/city.
- Opportunities by source type.
- New records by date.
- Saved/ignored/possibly closed status.
- Hiring posts with explicit recruitment email.

Do not make spreadsheet formulas part of backend business logic.

## 4. Local SQLite operational schema

Minimum tables:

### `search_runs`
- `run_id` primary key
- `idempotency_key` unique
- `operational_day`
- `status`
- `accepted_at_utc`
- `started_at_utc`
- `finished_at_utc`
- `error_summary`
- `progress_json`
- `source_summary_json`

### `sync_outbox`
- `outbox_id` primary key
- `entity_type`
- `entity_id`
- `payload_json`
- `status` (`pending`, `synced`, `failed`)
- `attempt_count`
- `last_error`
- `created_at_utc`
- `synced_at_utc`

### `source_cache`
- `cache_key` primary key
- `payload_json` or local payload reference
- `etag`
- `last_modified`
- `fetched_at_utc`
- `expires_at_utc`

### `app_settings`
- `key` primary key
- `value_json`
- `updated_at_utc`

Optional tables:
- `manual_imports`
- `gmail_checkpoints`
- `company_source_cache`
- `merge_review_queue`

OAuth tokens should not be stored in ordinary application tables unless encrypted and deliberately designed. Prefer OS credential storage; if unavailable, use a protected local token file outside the repository.

## 5. Stable IDs and deduplication

- `opportunity_id` is a generated stable UUID, persisted after first creation.
- `source_id` is a generated stable UUID linked to an opportunity.
- For repeat ingestion, identify source records by `(source_type, external_source_id)` when available, otherwise normalized canonical source URL.
- Exact duplicate source records update the existing source.
- Cross-source opportunity matching uses company, normalized title, location, and description similarity.
- Automatically merge only high-confidence exact matches.
- Ambiguous candidates go to a review queue; do not silently merge different vacancies.
- Preserve multiple source records when the same opportunity appears in multiple places.

## 6. Location model

Normalize separately:
- `country`
- `province`
- `city`
- `work_mode`
- `remote_scope`
- `location_status`

Do not infer job location from recruiter location. Unknown locations are review items. Remote global and remote Indonesia-specific are separate classifications.

## 7. Job status and stale records

A single unsuccessful fetch must not mark every missing job as closed.

Suggested behavior:
- A source is only considered successfully checked when its full fetch/pagination completed.
- If a job is absent from one successful complete scan, mark it `not_seen` in source metadata.
- After repeated successful complete scans, it may become `possibly_closed`.
- Mark `closed` only with direct evidence, such as source status or explicit deadline/closure information.
- A connector failure must never count as evidence that a job closed.

## 8. Google Sheets sync strategy

- Normalize and stage payloads in SQLite outbox first.
- Upsert by stable IDs.
- Batch read existing IDs and batch update/append where possible.
- Avoid one request per row.
- Record the result for every outbox item.
- Retry transient 429/5xx/network errors with bounded exponential backoff.
- A permanent permission/schema error remains visible for user action.
- Retry Sync never re-runs connectors or consumes search quota.
