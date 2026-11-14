---
name: job-domain-modeling
description: Use when modeling primary target roles, role aliases, Indonesian locations, remote eligibility, companies, job opportunities, job sources, recruiter contacts, or job status.
---

# Job Domain Modeling

## Core entities

- `Company`: employer identity and official career source metadata.
- `Opportunity`: one canonical job opening.
- `JobSource`: each distinct page/post/alert that points to an opportunity.
- `Contact`: a recruitment contact explicitly present in a source, with provenance and context.
- `TargetRole`: one user-configured primary search role with bounded aliases and exclusions.

## Invariants

- Never exceed three primary target roles.
- Role aliases are explicit, reviewed and attached to one primary role.
- Do not classify a job as relevant only because it shares generic skills; title/responsibility evidence must support the role.
- Store raw location separately from normalized country/province/city.
- Do not infer remote eligibility from the word `Remote` alone. Distinguish Indonesia-only, worldwide, country-restricted and unknown.
- Unknown location is a review state, not a silent pass.
- Recruiter location is not job location.
- A company career listing and multiple hiring posts may refer to one opportunity; preserve all source records.
- Extract only contact details explicitly published for recruitment. Never guess an email from a name/domain.
- Track source URL, capture time, and extraction provenance.
- Separate role relevance from candidate fit; do not claim candidate fit without a candidate profile and evaluation method.

## Normalization

Prefer normalized controlled values plus original raw values. Keep source-specific extras in a structured metadata field only when needed and avoid polluting the canonical schema.
