---
name: architecture-planning
description: Use when changing requirements, system architecture, component boundaries, data flow, domain models, or Architecture Decision Records for Personal Job Finder.
---

# Architecture Planning

## Required context

Read `AGENTS.md` and the relevant documents in `docs/p0/`. Use the P0 baseline as the default source of truth.

## Workflow

1. Restate the problem and non-functional constraints.
2. Identify affected components, ownership, inputs, outputs, and failure boundaries.
3. Draw or update a Mermaid diagram when it clarifies the design.
4. Compare at least two viable approaches when the decision is consequential.
5. Prefer the smallest design that meets current requirements (modular monolith, not microservices).
6. Identify data migration, privacy, source-policy, concurrency, and testing implications.
7. If a decision changes an accepted baseline, draft an ADR; do not silently implement it.
8. Update diagrams and contracts together so documentation remains consistent.

## Project invariants

- Local-only single-user MVP.
- React + TypeScript + Vite frontend; Python + FastAPI backend.
- Google Sheets for user-facing job records; SQLite for operational state.
- Manual Auto Search only; two accepted runs per operational day; 12-hour cooldown; 06:00 Asia/Jakarta reset.
- No LinkedIn browser automation; no auto-apply; no background scheduler.
- Maximum three primary target roles; Indonesia-first location rules.

## Learning output

Explain the architecture concept, why it fits, one alternative, and trade-offs in beginner-friendly language.
