# 01 — Product Requirements Document

**Version:** 0.1.0  
**Status:** P0 architecture baseline

## 1. Product overview

Personal Job Finder is a single-user, local-first application for discovering and organizing relevant job opportunities in Indonesia. The user defines up to three primary target roles and configures location preferences and a curated set of company career sources.

The user explicitly triggers each discovery cycle by pressing **Auto Search** in the local web dashboard. The system gathers available listings from enabled, permitted company career sources, processes eligible LinkedIn notification emails if that optional integration is enabled, normalizes records, filters relevance/location, deduplicates, and syncs records to Google Sheets.

LinkedIn posts from recruiters and talent acquisition professionals are important sources. The application does not scrape LinkedIn or automate a logged-in LinkedIn browser. Such posts can enter the system through manual URL/text capture and, optionally, authorized notification email ingestion.

## 2. Primary user and product objective

- Primary user: one person operating the application locally.
- Primary objective: discover relevant opportunities, not maximize request volume or automate applications.
- Primary output: a reliable, searchable list of job opportunities with original source links and relevant recruiter contact details when explicitly provided.

## 3. In-scope capabilities

### 3.1 Target role configuration

- User can configure one to three primary target roles.
- Each role has a canonical name, user-approved aliases/keywords, optional excluded terms, and optional seniority preferences.
- Search must not silently invent new target roles or expand into unrelated occupations.
- The same Auto Search run processes all configured roles; quota is consumed per run, not per role.

### 3.2 Indonesia location filtering

- Indonesia is the default and required country scope.
- User may select all Indonesia or selected provinces/cities.
- Work mode may be configured: on-site, hybrid, remote.
- Remote opportunities are accepted only when the source indicates eligibility for Indonesia or worldwide access, according to the user's settings.
- Ambiguous/unknown location is placed in a review queue, not silently assumed to be Indonesia.
- Recruiter location must never be substituted for job location.

### 3.3 Company career sources

- User maintains a curated list of enabled companies/boards.
- Initial connector candidates: Greenhouse, Lever, and Ashby public job-posting interfaces.
- Custom career-page connectors are allowed only when the source permits the access method.
- Each connector returns a common normalized job schema.
- Requests respect provider documentation, source terms, rate limits, pagination, and access controls.
- HTTP 403/429 responses are handled conservatively; no identity rotation, proxy evasion, stealth browser, or bypass attempts.

### 3.4 LinkedIn source intake

Allowed:
- User manually pastes a public post URL and its text into the local application.
- The application parses user-provided text locally.
- Optional Gmail integration can read relevant user-authorized notification emails, subject to Google OAuth scope and verification requirements.
- Original LinkedIn URL is retained as provenance.

Not allowed:
- LinkedIn browser automation or scraping.
- Reading LinkedIn cookies, session storage, or authentication tokens.
- Automated LinkedIn searches, profile crawling, scrolling, or page extraction.
- Automated reactions, messaging, applying, or other account actions.

Manual post import is data processing of content supplied by the user; it does not initiate LinkedIn access and does not consume the Auto Search quota. If email intake is enabled, it runs only during a user-triggered Auto Search cycle or an explicit email-import action; it never polls in the background.

### 3.5 Job processing

- Validate incoming source records.
- Normalize company, job title, location, work mode, employment type, timestamps, and skills where available.
- Classify target-role relevance against the configured maximum three roles.
- Filter location eligibility.
- Detect duplicate source records and candidate cross-source duplicates.
- Preserve every original source URL and provenance record.
- Extract email addresses only when explicitly present in supplied post/email content and relevant to recruitment instructions.
- Never guess recruiter email addresses.
- Preserve uncertain extraction/location outcomes for review.

### 3.6 Storage and user interface

- Google Sheets stores user-visible opportunities, sources, contacts, companies, and run summaries.
- Local SQLite stores operational control data, search run state, deduplication/cache metadata as needed, and a durable sync outbox.
- React dashboard provides search control, job feed, detail views, post intake, contacts, company sources, settings, and search history.
- Local dashboard is served only on loopback by default.

## 4. Out of scope

- Auto-apply or auto-submit.
- Automated recruiter messaging.
- LinkedIn browser scraping or browser extension automation.
- Public multi-user SaaS.
- Cloud-hosted always-on scheduler.
- Scheduled discovery independent of user action.
- Tailored CV generation in MVP.
- RAG, vector database, and LLM dependency in MVP.
- Microservices, Kubernetes, Redis, Celery, and distributed job queues in MVP.

## 5. Functional requirements

| ID | Requirement |
|---|---|
| FR-01 | User can configure at most three primary target roles. |
| FR-02 | User can configure Indonesia location preferences and remote eligibility. |
| FR-03 | User can manage enabled company career sources. |
| FR-04 | User can initiate discovery only through the dashboard Auto Search action. |
| FR-05 | Backend enforces two accepted runs per operational day and twelve-hour cooldown. |
| FR-06 | Only one run can execute at a time. |
| FR-07 | One run processes all configured roles and enabled sources. |
| FR-08 | Source adapters normalize records into common domain models. |
| FR-09 | System filters role and location relevance. |
| FR-10 | System preserves source provenance and original URLs. |
| FR-11 | System detects exact duplicates and flags uncertain cross-source matches. |
| FR-12 | User can manually import LinkedIn post URL and text. |
| FR-13 | System extracts only explicitly present recruitment email contacts. |
| FR-14 | System writes records to Google Sheets idempotently. |
| FR-15 | System retains pending sync data if Google Sheets is unavailable. |
| FR-16 | User can inspect run status, source-level outcomes, errors, and history. |
| FR-17 | Interrupted runs are not automatically resumed after restart. |
| FR-18 | User can retry pending Sheets synchronization without starting a new discovery run. |

## 6. Non-functional requirements

- Local-only default network binding (`127.0.0.1`).
- No secrets committed to Git.
- External requests have connect/read timeouts.
- Connector concurrency is bounded and configurable.
- Retry policy is bounded; no infinite retries.
- HTTP 403 stops the affected connector for that run; no bypass attempts.
- HTTP 429 follows provider guidance/backoff and does not trigger repeated aggressive retries.
- Logs avoid storing OAuth tokens and unnecessary personal data.
- Search quota enforcement is transactional and server-side.
- Every run and connector outcome is auditable.
- Unit tests use fixtures/mocks; tests must not scrape live LinkedIn or send uncontrolled requests to company websites.

## 7. Success criteria for MVP

1. User can configure up to three roles and Indonesia location preferences.
2. User can configure a curated set of company boards.
3. User can trigger one complete discovery cycle from the dashboard.
4. Backend correctly enforces quota, cooldown, and one-active-run policy.
5. At least one permitted ATS connector works end-to-end, with additional connectors added incrementally.
6. Results are normalized and filtered.
7. New records and provenance are synced to Google Sheets.
8. LinkedIn posts can be imported manually and parsed.
9. A Sheets outage does not silently discard accepted results.
10. User can understand what happened during each run.

## 8. Product terminology

- **Target role:** one of up to three primary occupations configured by the user.
- **Alias:** user-approved title/keyword variation for a target role.
- **Opportunity:** a distinct job opening.
- **Source:** an original career listing, LinkedIn post, or notification that refers to an opportunity.
- **Contact:** a recruitment contact explicitly present in a source.
- **Search run:** one user-authorized discovery cycle across all enabled sources and target roles.
- **Operational day:** 06:00 WIB to 05:59:59 WIB the next calendar day.
- **Pending sync:** data safely staged locally but not yet written to Google Sheets.
