---
name: testing-and-verification
description: Use when planning or writing tests, debugging failures, verifying a feature, assessing coverage, or preparing a change for review and commit.
---

# Testing and Verification

## Test layers

- Python unit tests: pure domain rules, normalization, search policy and parsers.
- Python integration tests: FastAPI routes and repositories using temporary SQLite/mocked Google clients.
- Frontend unit/component tests: Vitest and React Testing Library.
- Contract tests: request/response shapes shared between frontend and backend.
- End-to-end tests: Playwright against the local app only.

## Rules

- Never scrape live LinkedIn in tests.
- Do not send uncontrolled requests to company career websites or Google APIs from unit tests.
- Use sanitized fixtures and mock clocks.
- Test failure paths, not only happy paths.
- Test policy boundaries and concurrency explicitly.
- Run relevant lint/type/test commands and report exact results.
- Do not weaken or delete a failing test merely to make the suite green; first explain the failure and intended behavior.
- Do not claim full test coverage unless a coverage tool was run and its result inspected.

## Definition of verified

A change is verified only when the relevant automated tests pass, the diff has been inspected, and any external assumptions not covered by tests are stated clearly.
