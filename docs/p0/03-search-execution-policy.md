# 03 — Search Execution Policy

**Version:** 0.1.0  
**Timezone:** Asia/Jakarta  
**Trigger:** Explicit user action only

## 1. Policy constants

```yaml
search_policy:
  trigger: manual
  max_runs_per_operational_day: 2
  cooldown_hours: 12
  reset_time: "06:00"
  timezone: "Asia/Jakarta"
  max_concurrent_runs: 1
  max_concurrent_source_requests: 2
  interrupted_runs_auto_resume: false
```

These are application-level safeguards, not a claim that a website permits a particular access pattern. Each source's documented terms, rate limits, and access controls remain authoritative.

## 2. Operational day

An operational day starts at 06:00 WIB and ends immediately before 06:00 WIB on the following calendar day.

Given an aware UTC timestamp:
1. Convert it to `Asia/Jakarta`.
2. If local time is before 06:00, operational date is the previous calendar date.
3. Otherwise operational date is the current local calendar date.

Equivalent conceptual formula:

```python
local_now = now_utc.astimezone(ZoneInfo("Asia/Jakarta"))
operational_date = (local_now - timedelta(hours=6)).date()
```

Store event timestamps in UTC. Derive operational day using the Asia/Jakarta timezone.

## 3. Eligibility conditions

A new run is accepted only when all conditions hold:
1. No run is currently `accepted` or `running`.
2. Fewer than two runs have been accepted for the current operational date.
3. At least twelve hours have elapsed since the last accepted run.
4. One to three valid target roles are configured.
5. At least one permitted source is enabled.
6. Required integrations are configured for the sources selected.

A validation rejection before reservation does not consume quota.

Once a run is accepted and persisted, it consumes quota even if:
- no new jobs are found;
- one or more sources fail;
- all source requests fail after acceptance;
- the backend is interrupted;
- Google Sheets is temporarily unavailable.

This avoids unlimited retry loops after a failed run. Pending Google Sheets synchronization can be retried separately without consuming search quota.

## 4. Atomic quota reservation

Quota enforcement must happen in the backend inside one SQLite write transaction.

Recommended approach:
- Begin an immediate write transaction.
- Check active-run state.
- Count accepted runs for current operational date.
- Read latest accepted timestamp.
- Check cooldown.
- Insert the run with a unique request/idempotency key.
- Commit.
- Only after commit, start the orchestrator task.

The transaction must be short. Do not hold the SQLite write lock while calling external APIs.

SQLite `BEGIN IMMEDIATE` obtains a reserved write lock early and prevents another writer from concurrently reserving a run; implement a bounded busy timeout and test concurrent requests.

The frontend's disabled button is UX only. The backend is the enforcement boundary.

## 5. Cooldown and quota formula

```text
quota_remaining = max(0, 2 - accepted_runs_for_operational_day)

cooldown_until = latest_accepted_at + 12 hours

can_start =
    no_active_run
    AND quota_remaining > 0
    AND now >= cooldown_until
    AND valid_search_configuration
```

At 06:00 WIB, quota count moves to the new operational date. The cooldown does not reset at 06:00; it remains based on the last accepted run.

## 6. Examples

| Accepted run | Next eligible time (assuming quota available) |
|---|---|
| 07:00 WIB | 19:00 WIB |
| 19:00 WIB | 07:00 WIB next calendar day |
| 23:00 WIB | 11:00 WIB next calendar day |
| 05:00 WIB | 17:00 WIB same calendar day |

If quota is exhausted, eligibility also waits until the next operational-day reset at 06:00 WIB.

## 7. Example API policy response

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

The timestamp values above are illustrative.

## 8. Run statuses

| Status | Meaning |
|---|---|
| `accepted` | Run quota reserved and persisted |
| `running` | One or more source tasks are executing |
| `completed` | All enabled source tasks completed successfully and data synced |
| `completed_with_warnings` | At least one source succeeded, but one or more sources had errors/partial results |
| `completed_pending_sync` | Results are safely staged but Google Sheets sync remains pending |
| `failed` | Run accepted but no usable source operation completed or a fatal pipeline failure occurred |
| `interrupted` | Backend stopped before terminal completion |

A run with zero new jobs can still be `completed` if all sources completed successfully.

## 9. Connector policy

- Only user-enabled and curated company sources are queried.
- One Auto Search run covers all target roles; do not create a separate run per role.
- Prefer one board fetch followed by local filtering if the source returns a complete job board.
- Use provider pagination only as documented and within connector budgets.
- Configure per-connector timeout, maximum pages, and request budget.
- Cache source responses where appropriate; respect cache validators if supported.
- Stop on access denied/HTTP 403.
- Handle HTTP 429 with bounded backoff; do not retry aggressively.
- No proxy rotation, session reuse, stealth, CAPTCHA bypass, or identity rotation.
- LinkedIn browser requests are always zero.

## 10. LinkedIn policy

Allowed:
- User manually supplies LinkedIn URL + text.
- Optional reading of notification emails with explicit user authorization and appropriate Google OAuth configuration.
- Parsing and classifying user-supplied content locally.

Not allowed:
- Automated LinkedIn browser search or page scraping.
- Programmatic access to LinkedIn login/session cookies.
- Automated browsing/scrolling/profile crawling.
- Automated posting, messaging, reactions, or applying.

Manual post import is not a discovery run and does not consume search quota because the user supplies the content. It must not trigger a hidden network fetch to LinkedIn.

## 11. Shutdown and restart

- No scheduled job is registered.
- No search starts on application startup.
- When backend starts, unfinished previous runs are marked `interrupted`.
- Interrupted runs are not resumed.
- The original accepted timestamp continues to govern cooldown.
- Any pending sync outbox is displayed and may be retried via a separate sync action.
- If user closes only the browser tab while backend remains open, the backend may complete the current run.

## 12. Required tests

- Boundary at 05:59:59 WIB and 06:00:00 WIB.
- First and second accepted runs.
- Third attempt in same operational day.
- Exactly twelve hours and one second before cooldown.
- Quota reset while cooldown still active.
- Concurrent double POST with different idempotency keys.
- Repeated POST with the same idempotency key.
- Active run blocks another run.
- Backend restart marks stale run interrupted.
- Failed/interrupted accepted run still consumes quota.
- Manual post import does not consume quota.
- Retry sync does not consume quota.
