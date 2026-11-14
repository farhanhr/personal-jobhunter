---
name: google-sheets-integration
description: Use when designing Google Sheets tabs, OAuth, reading/writing values, batch updates, upsert, spreadsheet synchronization, or Google API error handling.
---

# Google Sheets Integration

- Backend-only integration; never call Google Sheets API directly from React.
- Keep spreadsheet ID and OAuth client/token files in local ignored settings, not source code.
- Request the narrowest feasible OAuth scopes and explain consent implications before enabling them.
- Use the official Google API client and current documentation; do not invent endpoints or scopes.
- Batch reads and writes where possible. Avoid one network request per row.
- Use stable IDs in dedicated columns and an explicit upsert strategy. Sheets does not provide relational unique constraints.
- Preserve source provenance and distinguish `Opportunity` from `JobSource` and `Contact` rows.
- Handle quota/rate-limit responses with bounded exponential backoff and respect provider guidance.
- Keep unsynced records in a local outbox so a Sheets outage does not discard a completed search result.
- Never include real spreadsheet data in tests, screenshots, logs or committed fixtures.
- Do not place secrets in `VITE_*` variables; Vite variables are bundled into browser-visible code.

## Required tests

Use mocked API responses for batch update, partial failure, quota errors, duplicate IDs, expired token, and outbox retry.
