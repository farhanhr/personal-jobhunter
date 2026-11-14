---
name: react-typescript-frontend
description: Use when implementing React components, hooks, routing, forms, state, TanStack Query, TypeScript types, or Vite frontend behavior.
---

# React + TypeScript Frontend

- Use function components and focused feature-based modules.
- Keep TypeScript strict; avoid `any`. If a boundary is genuinely unknown, use `unknown` and narrow it.
- Use TanStack Query for server state (jobs, search status, settings, summaries); use React state for transient UI state.
- Do not duplicate backend business logic in the browser. Quota, cooldown, matching rules, and source enablement are authoritative on the backend.
- Use typed API functions in a dedicated `src/api` or feature API module; do not scatter raw `fetch` calls through components.
- Handle loading, empty, error, stale, and partial-result states explicitly.
- Cancel or ignore stale polling requests when a run completes or a page unmounts.
- Keep accessibility: semantic HTML, keyboard support, visible focus, form labels, and status announcements.
- Do not store Google OAuth tokens, spreadsheet credentials, or provider API keys in frontend code or `VITE_*` variables.
- Do not make direct requests to LinkedIn or ATS websites from the frontend.
- Use the Vite dev proxy in development; production frontend must call the same-origin FastAPI API.

## Learning output

Explain component composition, props/state, hooks, server state vs UI state, and query invalidation when they are introduced.
