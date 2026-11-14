---
name: python-engineering
description: Use when writing or reviewing Python modules, typing, domain logic, exceptions, packaging, configuration, or maintainable Python code in the backend.
---

# Python Engineering

- Use the Python version declared by the project; do not guess or silently upgrade it.
- Add type annotations to public functions and meaningful internal boundaries.
- Prefer small functions, explicit names, and cohesive modules.
- Use dataclasses for simple internal domain values where appropriate; use Pydantic at untrusted data/API boundaries.
- Do not use broad `except Exception` unless the boundary logs and translates unexpected failures safely.
- Never swallow errors or silently convert malformed external values into invented facts.
- Keep I/O at edges; keep domain rules deterministic and unit-testable.
- Use dependency injection for repositories, clocks, and connector clients when it improves testability.
- Use aware datetimes. Persist UTC instants; convert to `Asia/Jakarta` only for operational-day and display calculations.
- Use `Decimal` for money if salary fields are implemented; do not use float for financial values.
- Avoid globals holding mutable run state.
- Do not add dependencies without explaining the need and checking maintenance, license, and security.

## Required explanation

When adding a pattern such as dataclass, Pydantic model, protocol/interface, context manager, or dependency injection, explain what it solves and what simpler alternative exists.
