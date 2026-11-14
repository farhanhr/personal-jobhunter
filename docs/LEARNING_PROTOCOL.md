# Learning Protocol for AI-Assisted Development

The owner is learning software engineering while using AI-assisted development. The agent must optimize for both a working application and genuine understanding.

## Before implementation

1. State the problem being solved.
2. Explain relevant concepts in beginner-friendly language.
3. Explain why the chosen technology or pattern fits this project.
4. Name at least one alternative and why it is not selected.
5. Give a small implementation plan and acceptance criteria.

## During implementation

- Work in small, reviewable steps.
- Prefer readable code and explicit types.
- Do not introduce abstractions before there is a demonstrated need.
- Do not silently install new dependencies.
- Do not change project architecture without an ADR proposal.
- Add tests with the implementation, not as an afterthought.

## After implementation

Provide:

1. Files changed.
2. What the code now does.
3. How the code works, step by step.
4. Concepts used (for example, dependency injection, idempotency, transaction, React state, query cache).
5. Why the design was chosen and its trade-offs.
6. Tests actually run and their results.
7. Limitations and next learning topic.

## Avoid false confidence

- Do not say a test passed unless it was executed.
- Do not invent API behavior or undocumented platform access.
- Distinguish a mock, a fixture, a local integration test, and a live external test.
- State when a behavior is unverified.

## Suggested learning loop

Understand → Plan → Implement → Test → Review → Explain → Small learner exercise → Commit.
