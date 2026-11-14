# ADR-016 — Use a Durable and Explicitly Retryable Sync Outbox

**Status:** Accepted (owner-approved 2026-11-13)  
**Date:** 2026-11-13

## Context

The initial outbox had only `pending`, `synced`, and `failed`, while the retry endpoint only handled pending items. It was unclear how transient and permanent failures, restarts, and repeated updates are handled.

## Options
1. Retry all failed items without classifying errors.
2. Use explicit transient/permanent states and user-controlled requeue for blocked items.

## Decision

Recommend option 2. States: `pending`, `in_progress`, `retry_wait`, `blocked`, `synced`. Store run association, destination, entity identity, payload hash, attempts, max attempts, next retry, sanitized error code, and timestamps. Use a unique current-projection key `(destination, entity_type, entity_id)`.

Retry Sync processes due `pending`/`retry_wait` items only. `blocked` items require correction and explicit per-item requeue. Retry never calls connectors or consumes search quota. Stale `in_progress` items become `retry_wait` after restart; idempotent upsert makes replay safe.

## Consequences

- More reliable recovery from process interruption and Sheets outage.
- UI must display blocked items and actionable error categories.
- Error details must be sanitized; no secrets or raw OAuth/provider headers in logs.

## Verification

Test 429/5xx/timeout, invalid schema, permission failure, restart during sync, duplicate replay, updated payload, manual requeue, and no connector calls during retry.
