# 09 — Risks, Assumptions, and Open Decisions

**Version:** 0.1.0

## 1. Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| LinkedIn coverage incomplete | Some hiring posts are missed | Manual capture + optional notification email intake; be transparent about coverage |
| LinkedIn policy restrictions | Account restriction risk | No automated LinkedIn browser access or scraping |
| ATS API changes | Connector breaks | Adapter isolation, fixtures, contract tests, source monitoring when user runs search |
| Company board token unknown | Cannot discover that company's jobs | User adds/validates official career board |
| Google Sheets quota/outage | Data sync delayed | Batch operations, bounded exponential backoff, SQLite outbox |
| Duplicate opportunities | Noisy job feed | Stable source IDs, canonical URLs, review queue for fuzzy matches |
| Incorrect location extraction | Wrong-country jobs enter feed | Indonesia location resolver; unknown => review |
| Remote scope ambiguity | Non-eligible remote role included | Accept only explicitly eligible Indonesia/worldwide remote; ambiguous => review |
| Email extraction false positive | Wrong contact recorded | Keep context, confidence, unverified status; never guess |
| Backend stops during run | Incomplete discovery | Persist run state; mark interrupted; no auto-resume |
| OAuth token exposure | Account data compromise | OS keyring/protected local storage, localhost only, no secrets in Git |
| LLM hallucination (future) | Incorrect job details | Deterministic fields, evidence provenance, evaluation, human review |

## 2. Assumptions

- Application is used by one person on one local machine.
- The user can maintain a curated list of companies/official career boards.
- The application is not a public hosted service.
- User prefers Google Sheets as the human-visible opportunity database.
- User accepts incomplete LinkedIn coverage in exchange for avoiding automated account interaction.
- Auto Search run is initiated only while the local backend is running.
- The user accepts that an accepted run consumes quota even if later interrupted or all sources fail.

## 3. Open decisions to resolve during P1/P2

> **P0.1 update (2026-11-13):** this list is historical. Items 1–8 were resolved or superseded by ADR-013 through ADR-019. Current remaining owner/environment decisions are tracked in `docs/p0/14-decision-register.md` §4.

1. Google Sheets connection method and least-privilege OAuth scopes for an existing spreadsheet.
2. Whether Gmail notification intake is worth enabling given `gmail.readonly` is a restricted scope.
3. Exact Google Sheets tab initialization and whether Companies is managed in Sheets or only through local UI.
4. Exact ATS connector pagination limits after inspecting each API's current documentation.
5. Whether to use SQLAlchemy/Alembic or Python sqlite3 for SQLite persistence. Recommended to keep the initial abstraction small; decide based on migration/testing needs.
6. Whether to use an OS keyring library on the target Ubuntu installation or a protected local token file.
7. Exact supported job types and preferred seniority filters.
8. Whether the dashboard needs export-to-CSV in MVP.

## 4. Explicitly deferred

- Public deployment.
- Multi-user login.
- Background schedule.
- LinkedIn browser automation.
- Auto-apply.
- Tailored CV.
- RAG.
- Vector database.
- Distributed workers.
- Docker/Kubernetes.

## 5. Acceptance gates before implementation

P0 is complete when the user and technical architect agree that:
- Product boundaries are correct.
- Search quota/cooldown/reset rules are unambiguous.
- Data entities and tab schemas are sufficient.
- Frontend/backend responsibility boundaries are clear.
- API contracts cover the MVP workflow.
- Google Sheets/SQLite responsibilities are agreed.
- LinkedIn data-ingestion boundary is explicit.
- Open decisions are recorded rather than silently assumed.
