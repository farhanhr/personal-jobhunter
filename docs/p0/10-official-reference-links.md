# 10 — Official Reference Links

These references support technology and integration decisions. Re-check version-specific documentation during P1/P2 before installing dependencies or implementing connectors.

## Frontend

- Vite guide: https://vite.dev/guide/
  - Vite provides a development server with Hot Module Replacement and a production build for static assets.
  - The current guide lists Node.js compatibility requirements; verify the active requirement again when setting up the laptop.
- TanStack Query React overview: https://tanstack.com/query/latest/docs/framework/react/overview
  - Used for server-state fetching, caching, synchronization, and updates.
- React documentation: https://react.dev/learn
- TypeScript documentation: https://www.typescriptlang.org/docs/
- shadcn/ui: https://ui.shadcn.com/

## Backend and local database

- FastAPI background tasks: https://fastapi.tiangolo.com/tutorial/background-tasks/
  - Background tasks can run after a response. This project still needs explicit persisted run state and interruption handling because in-process work is not a durable external queue.
- SQLite transaction documentation: https://sqlite.org/lang_transaction.html
  - SQLite permits multiple simultaneous readers but only one simultaneous writer. `BEGIN IMMEDIATE` is relevant to atomic run reservation.
- Python documentation: https://docs.python.org/3/
- Pydantic documentation: https://docs.pydantic.dev/

## Google integrations

- Google Sheets API usage limits: https://developers.google.com/workspace/sheets/api/limits
  - Contains per-minute quotas, batch request behavior, atomic updates, and exponential backoff guidance.
- Gmail API scopes: https://developers.google.com/workspace/gmail/api/auth/scopes
  - `gmail.readonly` is classified as a restricted scope. Evaluate consent, testing, and verification requirements before enabling Gmail intake.
- OAuth 2.0 for desktop apps: https://developers.google.com/identity/protocols/oauth2/native-app
  - Desktop application flow, loopback redirect, and PKCE guidance.

## Source access

- Greenhouse Job Board API: https://developers.greenhouse.io/job-board.html
- Lever Postings API: https://github.com/lever/postings-api
- Ashby Public Job Posting API: https://developers.ashbyhq.com/docs/public-job-posting-api
- LinkedIn automated activity policy: https://www.linkedin.com/help/linkedin/answer/a1340567/automated-activity-on-linkedin?lang=en

## Agent development

- OpenCode documentation: https://opencode.ai/docs/
- Agent Skills standard: https://agentskills.io/home
- Model Context Protocol: https://modelcontextprotocol.io/docs
- Context7: https://github.com/upstash/context7

## Research notes

- A publicly accessible ATS endpoint does not grant permission to submit applications or to crawl unrelated pages.
- Connector implementation must follow the current provider documentation and source terms.
- Gmail integration is optional, not a prerequisite for the MVP. Manual LinkedIn post intake remains available without Gmail access.
