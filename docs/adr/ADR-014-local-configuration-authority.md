# ADR-014 — Keep Private Search Configuration and Company Sources in SQLite

**Status:** Accepted (owner-approved 2026-11-13)  
**Date:** 2026-11-13

## Context

Target roles, aliases, location filters, and connector configuration are private, user-specific settings. The original schema left their authority ambiguous between local YAML, SQLite, and Google Sheets.

## Options
1. Store configuration in editable Google Sheets tabs.
2. Store configuration in ignored local YAML files.
3. Store configuration in SQLite and manage it through the local UI.

## Decision

Recommend option 3. SQLite is authoritative for one-to-three target roles, approved aliases, location/remote preferences, optional job-type/seniority filters, and the company source registry. The local dashboard manages these settings. Do not require `Search_Config` or `Companies` tabs in the MVP.

## Consequences

- Private preferences are not synced to the spreadsheet or public repository.
- Configuration updates can be validated and versioned locally.
- A `config_snapshot_json` is saved for every accepted run so an in-progress run uses an immutable configuration snapshot.
- A future read-only Companies mirror may be added, but it is not authoritative.

## Verification

Test configuration validation, one-to-three enabled roles, alias ownership, disabled company sources, and changes made during an active run taking effect only on the next run.
