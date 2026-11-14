# ADR-013 — Allow Partial Source Availability

**Status:** Accepted (owner-approved 2026-11-13)  
**Date:** 2026-11-13

## Context

P0 03 required every selected source to be configured before accepting a run, while P0 05 and the implementation behavior specification allowed a run when at least one usable source remained. A single unavailable source should not prevent independent configured sources from being searched.

## Options
1. Require every enabled source to be reachable before accepting any run.
2. Accept when at least one enabled, locally valid, permitted source is configured; report source failures independently.

## Decision

Recommend option 2. Validate source configuration locally before reservation. Do not perform broad source network probing as a condition for every connector. A runtime source error becomes a warning. If no source is enabled, locally valid, and permitted, reject before quota reservation. If all source operations fail after acceptance, the run is `failed` and quota remains consumed.

## Consequences

- Better fault isolation.
- Search can still return results if one provider is down.
- The UI must distinguish unavailable, skipped, failed, and succeeded sources.
- The user sees partial results and source warnings.

## Verification

Test one connector failing while another succeeds; all connectors failing after acceptance; no usable configured sources; and disabled sources.
