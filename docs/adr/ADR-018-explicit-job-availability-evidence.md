# ADR-018 — Do Not Infer Closure from a Missing Search Result

**Status:** Accepted (owner-approved 2026-11-13)  
**Date:** 2026-11-13

## Context

The initial schema mentioned `not_seen` and `possibly_closed` but did not consistently define source-level status or the required number of successful complete scans. Absence may be caused by pagination, filtering, provider changes, or a transient error.

## Options
1. Mark a job `not_seen` after one successful scan and `possibly_closed` after repeated misses.
2. Keep explicit availability states and require direct evidence for closure.

## Decision

Recommend option 2 for MVP. Use `availability_status` = `open`, `closed_confirmed`, or `unknown`, separate from `user_status` = `new`, `saved`, or `ignored`. Do not use `not_seen` or `possibly_closed`. A missing record does not change availability. Mark `closed_confirmed` only with direct source evidence or a connector-specific verified closure rule.

## Consequences

- Fewer false closed/expired labels.
- Some stale jobs remain `unknown` until directly verified.
- Connector `last_seen_at` is observation metadata, not proof of continued availability.

## Verification

Test that failed/partial scans never change availability, explicit closure evidence does, and missing records remain unchanged.
