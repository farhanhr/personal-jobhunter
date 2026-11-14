# 08 — Learning Roadmap

**Purpose:** Every implementation phase must teach the user the concepts behind the code.

## Learning contract

For each task, the coding agent must:
1. Explain the concepts before implementation.
2. Explain why the selected technology/approach is appropriate.
3. Name at least one alternative and its trade-off.
4. Implement a small, reviewable change.
5. Add or update tests.
6. Explain the important code paths after implementation.
7. Provide a small exercise/check for user understanding.

Do not turn every task into a long lecture. Teach concepts just in time.

## P0 — Requirements and architecture

Concepts:
- Functional vs non-functional requirements.
- System boundary.
- Context/container/component diagrams.
- Modular monolith.
- Separation of concerns.
- Domain modeling.
- Data flow.
- API contracts.
- Architecture Decision Records.

Expected understanding:
- Explain why one FastAPI backend is sufficient.
- Explain why frontend and backend are separated.
- Explain why LinkedIn browser access is excluded.
- Explain why the app has both Google Sheets and SQLite.

## P1 — AI harness

Concepts:
- Project instructions vs skills vs agents vs MCP.
- Agent Skills format.
- Context management.
- Tool permissions.
- Plan/build/review workflow.
- Prompt injection and untrusted source data.
- Coding-agent verification.

Expected understanding:
- Explain what AGENTS.md controls.
- Explain when a skill should activate.
- Explain why reviewer is read-only.
- Explain how to inspect AI changes before committing.

## P2 — Development foundation

Concepts:
- Git repository and branches.
- Python virtual environments and dependency locks.
- Node/npm and frontend build tools.
- Environment variables and secrets.
- Unit-test structure.
- Linting and type checking.

## P3 — FastAPI backend

Concepts:
- HTTP request/response.
- REST resources.
- HTTP status codes.
- Pydantic validation.
- Dependency injection.
- Async I/O.
- Application lifecycle.
- Error handling.

## P4 — Connector and ingestion engineering

Concepts:
- REST APIs.
- HTTP headers and status codes.
- Pagination.
- Timeouts.
- Retries and exponential backoff.
- Adapter pattern.
- ETL pipeline.
- Schema validation.
- Data normalization.
- Idempotency.

## P5 — SQLite and search policy

Concepts:
- Relational tables and indexes.
- Transactions.
- Atomicity.
- `BEGIN IMMEDIATE`.
- Concurrency control.
- Operational day and timezone-aware timestamps.
- Idempotency keys.
- State machines.
- Crash recovery.

## P6 — Google Sheets and OAuth

Concepts:
- OAuth 2.0.
- Installed/desktop application flow.
- Scopes and least privilege.
- Access vs refresh tokens.
- API quotas.
- Batch operations.
- Upsert.
- Outbox pattern.
- Eventual synchronization.

Important: Gmail `gmail.readonly` is a restricted OAuth scope. The need for the Gmail integration and its consent/verification implications must be evaluated before enabling it.

## P7 — React frontend

Concepts:
- JavaScript/TypeScript.
- JSX.
- Components.
- Props.
- State and hooks.
- Client-side routing.
- Form handling.
- Component composition.
- Accessible UI.

## P8 — API integration

Concepts:
- API contracts.
- Query vs mutation.
- Server state vs client state.
- TanStack Query.
- Caching and invalidation.
- Polling.
- Loading/empty/error states.
- Type-safe API clients.

## P9 — LinkedIn post intake

Concepts:
- Text parsing.
- Regular expressions.
- Context extraction.
- Provenance.
- Data validation.
- Human review.
- Privacy and data minimization.

## P10 — Job intelligence

Concepts:
- Rule-based classification.
- Precision and recall.
- Confusion matrix.
- Labeled evaluation dataset.
- Sentence embeddings.
- Cosine similarity.
- Hybrid retrieval.
- Model evaluation.

## P11 — Tailored CV and RAG (future)

Concepts:
- Document parsing.
- Chunking.
- Embeddings.
- Vector indexes.
- Metadata filtering.
- Retrieval.
- Reranking.
- Context assembly.
- Grounded generation.
- Hallucination detection.
- Citation/evidence traceability.
- RAG evaluation.

RAG is not synonymous with embeddings or vector search. RAG requires retrieving relevant information and supplying it as context to a generative model.

## Learning evidence

Maintain `docs/learning/` with:
- Concept notes.
- Short exercises.
- Decisions explained in the user's own words.
- Debugging lessons.
- Links to official documentation.

The goal is for the user to understand and explain the application's architecture, not just operate it.
