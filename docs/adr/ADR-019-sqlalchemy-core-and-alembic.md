# ADR-019 — Use SQLAlchemy Core and Alembic for SQLite

**Status:** Accepted (owner-approved 2026-11-13)  
**Date:** 2026-11-13

## Context

SQLite will hold operational state, private settings, a read model, connector configuration, and a sync outbox. The schema will evolve during the project and needs reproducible local migrations.

## Options
1. Python `sqlite3` directly with hand-managed SQL schema/version files.
2. SQLAlchemy 2.x Core with Alembic migrations.
3. SQLAlchemy ORM-first implementation.

## Decision

Recommend option 2. Use SQLAlchemy 2.x Core for explicit table definitions and queries, plus Alembic for schema migrations. Do not introduce ORM models/repositories unless a concrete use case justifies them. Keep important SQL and transaction boundaries visible for learning.

Quota reservation must still use an explicit SQLite `BEGIN IMMEDIATE` and commit before network I/O. Verify SQLAlchemy transaction behavior with an integration test; never assume a generic transaction automatically has the locking behavior required by the policy.

## Consequences

- Typed schema definitions and repeatable migrations.
- More dependencies than stdlib `sqlite3`.
- Learner must understand SQLAlchemy Core and migration concepts.
- Transaction tests are mandatory for cooldown/quota correctness.

## Verification

Test fresh database creation, upgrade from previous schema, rollback behavior, concurrent reservation, and `BEGIN IMMEDIATE` behavior with two connections.
