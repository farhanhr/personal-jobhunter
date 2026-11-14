# ADR-017 — Defer Gmail Notification Ingestion from MVP

**Status:** Accepted (owner-approved 2026-11-13)  
**Date:** 2026-11-13

## Context

The original architecture treated Gmail notification ingestion as optional but referenced it in requirements without an API endpoint. Gmail `gmail.readonly` is a restricted OAuth scope and adds consent, verification, token-storage, and privacy considerations.

## Options
1. Include Gmail read-only integration in MVP.
2. Defer Gmail; support user-provided LinkedIn URL + post text through a manual form.

## Decision

Recommend option 2. MVP does not request Gmail OAuth scopes, poll Gmail, or expose Gmail endpoints. LinkedIn post intake is manual and local: the user supplies text and URL; the server never fetches the supplied LinkedIn URL.

## Consequences

- Lower OAuth/security complexity.
- LinkedIn post coverage is incomplete and depends on user capture.
- Gmail can be reconsidered through a new ADR after separate OAuth and privacy analysis.

## Verification

Test that LinkedIn import parses supplied text, stores URL provenance, does not fetch the URL, and consumes no Auto Search quota.
