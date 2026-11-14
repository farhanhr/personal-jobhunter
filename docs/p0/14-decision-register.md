# 14 — P0.1 Decision Register and Remaining Open Items

**Version:** 0.1.0

## 1. Decisions recommended for adoption in P0.1

| ID | Decision | Recommendation |
|---|---|---|
| ADR-013 | Partial source availability | Accept if at least one enabled, valid, permitted source is configured; runtime source failures become warnings |
| ADR-014 | Local configuration ownership | SQLite is authoritative for target roles, search preferences, and company source registry |
| ADR-015 | Sheets canonical records and SQLite read model | Sheets is canonical for opportunities/sources/contacts/run summaries; SQLite holds rebuildable read model and operational state |
| ADR-016 | Durable sync outbox | `pending`, `in_progress`, `retry_wait`, `blocked`, `synced`; explicit retry semantics |
| ADR-017 | Gmail deferred | No Gmail OAuth or Gmail endpoints in MVP; manual LinkedIn post intake only |
| ADR-018 | Job status evidence | No `not_seen`/`possibly_closed`; closure requires direct evidence |
| ADR-019 | SQLite persistence toolkit | SQLAlchemy 2.x Core + Alembic; no ORM-first implementation |

These are recommendations prepared for owner adoption, not silently accepted changes. Once approved, copy the ADRs into `docs/adr/` and update the project instruction file.

## 2. Deliberately deferred

- Gmail alert ingestion and restricted `gmail.readonly` OAuth scope.
- Tailored CV, embeddings, LLM extraction, RAG, and vector database.
- CSV export.
- Automatic stale-job inference.
- Public hosting, multi-user access, Celery/Redis, microservices.
- Read-only Companies mirror tab in Google Sheets.

## 3. Configuration decisions

- One to three primary roles are allowed; zero is invalid for Auto Search.
- Employment type and seniority filters are optional and default to no additional restriction until configured by the user.
- Remote scope values: `indonesia_only`, `worldwide`, `country_restricted`, `unknown`, `not_applicable`.
- Remote `unknown` is placed in review; do not silently treat it as Indonesia-eligible.
- `worldwide` is eligible only when the user's remote setting allows it.

## 4. Remaining owner/environment decisions before P2

1. Repository license: recommend MIT for broad reuse, but the owner must approve the copyright/license choice.
2. Google Sheets OAuth setup: choose the exact spreadsheet and least-privilege scope during local integration setup; spreadsheet ID remains local.
3. Credential storage: prefer OS keyring. Verify the Ubuntu desktop Secret Service backend; if unavailable, decide whether to use a restrictive-permission local token file or defer OAuth setup.
4. Supported Node.js and Python versions: pin after checking official tool requirements at P2.
5. Exact ATS connector pagination and request budgets: finalize per connector from current official docs when that connector is implemented.
6. Initial list of company sources and the three target roles: private local configuration, never public repository content.

## 5. Adoption status

ADR-013 through ADR-019 were approved by the project owner on 2026-11-13. The updated data and API contracts are normative. Implementation may proceed. Any later change requires a new ADR.
