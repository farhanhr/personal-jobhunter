# P1 Skill Inventory

These original skills are designed for the current project baseline. All are portable Agent Skills placed under `.agents/skills/`.

| Skill ID | Trigger | Priority |
|---|---|---|
| `architecture-planning` | Requirements, architecture, ADR, component/data-flow changes | Core |
| `python-engineering` | Python modules, typing, errors, packaging | MVP |
| `fastapi-backend` | Routes, dependencies, service layer, API responses | MVP |
| `react-typescript-frontend` | React components, hooks, Vite, TypeScript UI | MVP |
| `api-contract-design` | Frontend/backend API request-response changes | MVP |
| `job-domain-modeling` | Roles, locations, opportunities, sources, contacts | Core domain |
| `job-source-connectors` | ATS and permitted career page integrations | MVP |
| `data-ingestion-pipeline` | Validation, normalization, deduplication, idempotency | MVP |
| `sqlite-operational-state` | Quota, cooldown, transactions, interrupted runs | MVP |
| `google-sheets-integration` | Sheets API, batch writes, upsert, sync outbox | MVP |
| `testing-and-verification` | Unit, integration, frontend and contract tests | MVP |
| `public-repository-security` | Secrets, public GitHub content, fixtures, release review | Core |

## Deferred skills

Do not create or activate these until their implementation phase is approved:

- Gmail/OAuth restricted-scope integration.
- Embeddings and semantic matching.
- LLM structured extraction.
- RAG and vector retrieval.
- Tailored CV generation and evidence validation.

The deferred skills belong to later phases because the MVP does not need them.
