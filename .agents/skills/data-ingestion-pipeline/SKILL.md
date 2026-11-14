---
name: data-ingestion-pipeline
description: Use when validating, parsing, normalizing, filtering, deduplicating, staging, or syncing job records from multiple sources.
---

# Data Ingestion Pipeline

## Pipeline stages

1. Acquire source data through an approved connector or explicit user import.
2. Validate structure and required fields.
3. Preserve raw source values and provenance.
4. Normalize to canonical domain records.
5. Resolve target role and Indonesian location eligibility.
6. Deduplicate opportunities while preserving all source links.
7. Stage accepted records locally.
8. Upsert to Google Sheets in bounded batch operations.
9. Record run-level and per-source outcomes.

## Reliability principles

- Each stage should be independently testable and preferably deterministic.
- Ingestion must be idempotent: processing the same source record twice must not create duplicate opportunities or sources.
- Use stable IDs from providers when available; otherwise define a documented deterministic identity/fingerprint strategy.
- Never merge two opportunities solely because company and title text match; include location and source evidence, and retain uncertain matches for review.
- Do not drop malformed records silently. Report validation failures with safe, non-sensitive diagnostics.
- Distinguish found, new, updated, duplicate, filtered, invalid and failed counts.
- Do not equate `not seen in one scan` with `closed`; source completeness and expiry must be verified.
- Use fixtures for missing fields, encoding issues, inconsistent dates, repeated jobs, and incomplete descriptions.
