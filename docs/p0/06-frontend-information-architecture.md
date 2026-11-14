# 06 — Frontend Information Architecture

**Version:** 0.1.0  
**Stack baseline:** React + TypeScript + Vite

> **P0.1 override:** job availability and Gmail UI rules are superseded by `docs/p0/11-architecture-reconciliation.md`, ADR-017, and ADR-018.

## 1. Framework decision

Use a React single-page application (SPA) built with Vite.

Reasons:
- Application is local and does not need search-engine indexing or server-side rendering.
- FastAPI owns backend/data/AI responsibilities.
- Static frontend can be served by FastAPI in normal local use.
- Frontend can be replaced or expanded independently later.

Do not introduce Next.js for MVP unless a concrete requirement emerges.

## 2. Frontend technology baseline

- React
- TypeScript
- Vite
- React Router
- Tailwind CSS
- shadcn/ui
- TanStack Query for server state
- React Hook Form + Zod where useful

Pin compatible versions during P2 rather than relying on floating dependencies indefinitely.

## 3. Navigation

```text
/
├── Overview
├── Jobs
│   ├── Job Feed
│   └── Job Detail
├── LinkedIn Hiring Posts
│   ├── Import Post
│   └── Review Extraction
├── Contacts
├── Companies
├── Search History
├── Settings
│   ├── Target Roles
│   ├── Location Preferences
│   ├── Sources
│   └── Integrations
└── Sync Status
```

## 4. Overview page

Must show:
- Auto Search action.
- Remaining quota (e.g. 1/2 used).
- Cooldown countdown/next eligible time.
- Next quota reset at 06:00 WIB.
- Last run timestamp and status.
- Total and new opportunities.
- Opportunities by target role, location, and source.
- Pending Google Sheets sync warning.

Auto Search button is enabled only when backend policy reports `can_start=true`. Frontend state is informational; backend remains authoritative.

## 5. Search progress

When a run is active, show:
- Overall status.
- Current stage.
- Completed/total sources.
- Per-source status.
- Records found/filtered/new/updated.
- Errors and retryable/non-retryable outcomes.

Avoid fake percentage progress if connectors do not provide meaningful progress. Use stage/source progress instead.

## 6. Job Feed

Filters:
- Target role (max three configured roles).
- Province/city.
- Work mode.
- Source type.
- User status new/saved/ignored; availability open/closed_confirmed/unknown.
- Location review status.
- Keyword search over title/company.

Each card should show:
- Job title.
- Company.
- Location and work mode.
- Target role mapping.
- Role relevance explanation/score.
- First seen and posted date when known.
- Source type.
- Source count.
- Original source link.

Use `role_relevance_score`, not `candidate_fit_score`, until a verified candidate profile is introduced.

## 7. Job Detail

Show:
- Canonical opportunity fields.
- Original description.
- Required skills.
- All source records.
- Original source URLs.
- Recruiter contact details explicitly present in the source.
- Extraction context and verification status.
- Status controls such as Save/Ignore.

## 8. LinkedIn Hiring Posts

Manual import form:
- Post URL.
- Post text.
- Optional author name.
- Capture date.
- Parse/preview action.
- Review extracted job/company/location/email.
- Confirm or edit before saving.

Do not fetch the URL. Do not embed a hidden crawler or browser automation.

## 9. Companies

Show curated company registry:
- Company name.
- Official website/career URL.
- ATS type.
- Board identifier.
- Enabled state.
- Last successful check.
- Last error.
- Access notes.

## 10. Settings

- Up to three target roles.
- Approved role aliases.
- Excluded keywords.
- Country locked to Indonesia for MVP.
- Selected provinces/cities.
- Remote policy.
- Employment type/seniority preferences.
- Enabled company sources.
- No Gmail integration in MVP (ADR-017).

## 11. Search history

Show:
- Run ID.
- Accepted/start/finish times (WIB display).
- Operational day.
- Run status.
- Sources succeeded/failed.
- New/updated/filtered record counts.
- Pending sync count.
- Error details.

## 12. UI state management

- TanStack Query for server state (jobs, company registry, settings, run status, summary).
- React local state for ephemeral UI state (open dialog, selected tab).
- Backend is source of truth for quota and search status.
- Do not calculate authoritative quota in React.
