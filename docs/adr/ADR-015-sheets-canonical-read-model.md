# ADR-015 — Google Sheets Canonical Records with a SQLite Read Model

**Status:** Accepted (owner-approved 2026-11-13)  
**Date:** 2026-11-13

## Context

Google Sheets is required as the user's spreadsheet database/UI, while the local dashboard and deduplication need predictable, queryable data. The original P0 described SQLite as operational storage but did not define the read model behind `GET /jobs` and dashboard aggregates.

## Options
1. Query Google Sheets on every dashboard request.
2. Make SQLite canonical and treat Sheets as an export/mirror.
3. Keep Sheets canonical for user-facing job records and maintain a rebuildable SQLite materialized read model.

## Decision

Recommend option 3. Sheets is canonical for Opportunities, Job_Sources, Contacts, and Run_History. SQLite contains a rebuildable projection of those rows for dashboard queries and deduplication, plus operational state and outbox. `Dashboard` is a formula/chart tab. Target roles and company source configuration remain local SQLite data (ADR-014).

`Refresh from Sheets` is explicit. Auto Search performs a bounded Sheets read/preflight before quota reservation. If canonical data cannot be read for safe reconciliation, reject the search before consuming quota. After acceptance, new records are staged to the outbox before Sheets write.

## Consequences

- Dashboard reads are local and fast.
- User edits made directly in Sheets become visible after explicit refresh or the next Auto Search preflight.
- Search requires a successful canonical snapshot before acceptance.
- SQLite cache can be rebuilt; it is not a second editable master.
- Outbox protects accepted search results during post-acceptance Sheets outages.

## Verification

Test full refresh, partial refresh failure, stale-cache banner, manual sheet edits becoming visible, dedup after refresh, and post-acceptance Sheets outage.
