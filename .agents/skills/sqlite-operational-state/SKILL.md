---
name: sqlite-operational-state
description: Use when implementing search quota, cooldown, active-run locking, run history, sync outbox, cache, transaction boundaries, or recovery after application restart.
---

# SQLite Operational State

## Source of authority

SQLite is authoritative for local execution controls; Google Sheets is not the quota/lock database.

## Search policy

- Maximum two accepted runs per operational day.
- Cooldown of at least 12 hours between accepted runs.
- Operational day: 06:00 to 05:59:59 in `Asia/Jakarta`.
- Quota reset does not bypass a still-active cooldown.
- Only one active run at a time.
- A single accepted run covers all selected roles and enabled sources.
- Reserve quota and create the run atomically in one transaction.

## Concurrency and transactions

- Use a transaction strategy that serializes competing run-start attempts (evaluate `BEGIN IMMEDIATE` for SQLite).
- Check active run, operational day count and cooldown inside the same transaction that reserves the run.
- Persist UTC instants and derive operational date with a timezone-aware `ZoneInfo('Asia/Jakarta')` clock.
- Inject a clock in tests; never make quota tests depend on wall-clock sleeps.
- Use idempotency keys to prevent the same client request from creating multiple runs.
- Do not trust a frontend disabled button.

## Failure and restart

- Accepted run consumes quota even if a connector partially fails; this prevents retry loops.
- Record per-source outcomes and a terminal run state.
- Persist staged records or an outbox before syncing to Google Sheets.
- If the process exits while a run is active, mark it `interrupted` on startup; do not auto-resume in MVP.
- Keep sync retries separate from search-run quota. A sync retry must not repeat source discovery.

## Required tests

Test 05:59:59/06:00:00 boundaries, cooldown crossing reset, two runs/day, third run rejection, two simultaneous start requests, duplicate idempotency key, restart recovery, and pending sync recovery.
