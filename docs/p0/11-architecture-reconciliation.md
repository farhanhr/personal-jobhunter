# 11 — P0.1 Architecture Reconciliation

**Version:** 0.1.0  
**Status:** Accepted P0.1 reconciliation (owner-approved 2026-11-13)  
**Supersedes:** Conflicting statements in P0 0.1 documents for the topics listed below.

## 1. Purpose and authority

The P1 Architect review found gaps and contradictions in P0 0.1. This document closes those gaps. The original P0 documents remain historical baseline records; this document and its linked addenda are normative for any conflicting topic after the owner adopts the pack.

Do not silently select a different design. A later change requires a new ADR in `docs/adr/`.

## 2. Search acceptance when some sources are unavailable

A run may be accepted when:
- one to three valid target roles are configured;
- at least one source is enabled, locally configured, and permitted by its source policy;
- no active run exists;
- daily quota and cooldown permit execution;
- the required local operational database is healthy.

It is **not** necessary for every enabled source to be reachable before accepting the run. Source-level failures, access denials, unsupported pagination, and transient outages are reported as skipped/failed-source warnings. A source failure must not cancel successful independent sources.

If no enabled, locally valid, permitted source remains, reject before quota reservation. If at least one source is accepted but all source operations fail after the run starts, the accepted run becomes `failed` and still consumes quota.

The backend must distinguish:
- invalid configuration/no usable configured source: reject before reservation;
- a source failing at runtime: warning or run failure depending on whether any usable source completed;
- Google Sheets canonical snapshot unavailable during preflight: reject before reservation because the read model cannot be reconciled for safe deduplication.

## 3. Ownership of user configuration and company sources

SQLite is authoritative for private/local configuration:
- one to three `TargetRole` records;
- aliases and excluded keywords belonging to each role;
- location filters and remote policy;
- optional employment type/seniority filters;
- `CompanySource` registry and connector configuration;
- per-source enabled state, request budget, timeout, pagination cap, and policy notes.

The local dashboard manages these records. Google Sheets does not store private role preferences or connector credentials.

The MVP does **not** require a `Companies` Google Sheets tab. A future read-only `Companies` mirror may be added, but it must not become a second editable source of truth. The local Companies page reads/writes SQLite.

## 4. Google Sheets canonical records and SQLite read model

Google Sheets remains the canonical, user-facing durable record store for:
- `Opportunities`
- `Job_Sources`
- `Contacts`
- `Run_History`
- `Dashboard` (formulas/charts only)

SQLite stores:
- operational state (runs, quota, locks, idempotency);
- private configuration (roles, locations, company sources);
- a rebuildable local read model mirroring Sheets records for fast dashboard queries and deduplication;
- source response cache/checkpoints;
- durable synchronization outbox.

The SQLite read model is a cache/projection, not a competing canonical record store. It must be rebuildable from Sheets and updated after successful Sheets writes.

### Refresh behavior

- Application startup may load the last local read model, but must not start a search or silently perform job-source discovery.
- `Refresh from Sheets` is an explicit user action that refreshes the local projection from the canonical spreadsheet. It does not consume Auto Search quota.
- Before accepting Auto Search, the backend performs a bounded Sheets read/preflight to reconcile the canonical records into the local read model. If this fails, reject before reserving search quota.
- After acceptance, new/updated records are staged in the SQLite outbox, then written to Sheets in idempotent batches. A failure after acceptance leaves records pending locally.
- The dashboard can show the local read model while indicating its last successful Sheets refresh time.

## 5. Durable outbox and sync retry

Outbox states are:
- `pending`: staged and ready for sync;
- `in_progress`: currently being sent;
- `retry_wait`: transient failure; wait until `next_attempt_at_utc`;
- `blocked`: permanent/configuration/data error requiring user action;
- `synced`: destination confirmed successful.

Required fields include `outbox_id`, `run_id` (nullable for manual imports), `destination`, `entity_type`, `entity_id`, `payload_json`, `payload_hash`, `status`, `attempt_count`, `max_attempts`, `next_attempt_at_utc`, `last_error_code`, `last_error_at_utc`, `created_at_utc`, `updated_at_utc`, and `synced_at_utc`.

Use a unique key on `(destination, entity_type, entity_id)` for the current unsynced projection. A newer payload for the same entity may update/requeue that outbox record; it must not create an unbounded series of duplicate pending rows.

Transient 429, 5xx, connection reset, and timeout errors move to `retry_wait` with bounded exponential backoff. Invalid payload/schema, missing spreadsheet/tab, and permission/scope errors move to `blocked` and must not be retried aggressively.

`POST /api/v1/sync/retry` retries due `pending`/`retry_wait` records only. A blocked record requires correction and an explicit per-item requeue action. Sync retry must never call job connectors and must never consume search quota.

On startup, an outbox record stuck in `in_progress` is returned to `retry_wait` after a conservative stale-lock check. Idempotent Sheets upsert makes replay safe if the prior request succeeded but the local process stopped before recording success.

## 6. Gmail is deferred from MVP

The MVP does not integrate Gmail API, does not poll Gmail, and does not include a Gmail import endpoint. This avoids adding a restricted OAuth scope and verification burden to the first release.

LinkedIn hiring posts are imported through a manual form requiring user-provided post URL and text. The backend parses only supplied text and stores the URL as provenance. It must not fetch, preview, expand, or otherwise make a network request to the LinkedIn URL.

Gmail notification intake can be reconsidered in a future ADR after a separate OAuth/privacy review. It is not implied by the current LinkedIn integration boundary.

## 7. Job observation and availability semantics

Remove `not_seen` and `possibly_closed` from MVP status enums.

Absence from a source response is not evidence that a job is closed. A connector failure, partial pagination, rate limit, or source outage must never change availability status.

Use two separate concepts:
- `user_status`: `new`, `saved`, `ignored` (user workflow);
- `availability_status`: `open`, `closed_confirmed`, `unknown` (source evidence).

`closed_confirmed` is set only when the source explicitly reports closure, a verified official page states it is closed, or another direct evidence rule approved for that connector is met. Otherwise preserve `unknown` or the last supported state and record `last_seen_at` only after successful observation.

## 8. Role matching and deduplication for MVP

Role matching remains deterministic and limited to the configured one-to-three primary roles and their explicit aliases.

Do not invent an arbitrary numeric threshold before an evaluation dataset exists. Store:
- `target_role_id`;
- `role_match_type`: `canonical_title`, `approved_alias`, `description_keyword`, `needs_review`;
- `role_match_reason`.

Canonical title and approved alias matches may enter the main matching feed. Description-only or ambiguous matches go to review. Unrelated roles are excluded. A numeric relevance score may be introduced later with a labeled test set and a documented scoring formula.

Deduplication:
- same `(source_type, external_source_id)` or canonical source URL updates the same JobSource;
- cross-source auto-linking is allowed only for a deterministic high-confidence fingerprint requiring normalized company + title + job location + normalized description hash;
- near matches are candidates for manual review, not auto-merged;
- missing company/location or materially different descriptions must not be silently merged.

## 9. API and implementation boundaries

The API contract addendum in `docs/p0/13-api-contract-addendum.md` is normative for missing fields/endpoints, idempotency, pagination, and error responses.

No Gmail routes are part of MVP. Company source configuration and target roles are local SQLite-backed settings. Sheets remains the canonical store for opportunity records.

## 10. Persistence recommendation

Use SQLAlchemy 2.x Core (not ORM-first) plus Alembic for SQLite schema and migrations. This gives explicit table definitions, typed query construction, transaction control, and repeatable schema evolution while keeping SQL visible to the learner.

The quota reservation path must still use an explicit SQLite `BEGIN IMMEDIATE` transaction and must commit before any external connector/Sheets network operation. Do not hold the write lock during network I/O.

## 11. Supersession map

| Original document | Original section | P0.1 authority |
|---|---|---|
| P0 02 | §3.3 connectors/integrations | Gmail removed from MVP; see §6 here |
| P0 02 | §8 Sheets/SQLite responsibilities | Replaced by §4 here and P0 12 |
| P0 02 | §9 sync reliability | Replaced by §5 here and P0 12 |
| P0 03 | §3 eligibility | Replaced by §2 here |
| P0 04 | §3.4 Companies | SQLite local registry; no required Companies tab |
| P0 04 | §4 SQLite schema | Extended by P0 12 |
| P0 04 | §7 stale records | Replaced by §7 here |
| P0 04 | §8 sync strategy | Replaced by §5 here |
| P0 05 | API endpoint omissions | Extended by P0 13 |
| P0 09 | §3 open decisions | See P0 14 |

