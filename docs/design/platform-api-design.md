# D37: Platform API & Extensibility Design

> Status: draft

**Milestones:** M55–M56 · **Extends:** —

## Problem

v0.1.0's API is internal and unpaginated, benchmarks are hand-authored, and there is no programmatic surface, SDK, or extension path. v0.2.0 is the final big release, so the plane must expose a frozen, documented API; make corpora easy to author and version; serve agents as fully gated endpoints; enforce per-tenant quotas; publish fleet events; and let the community extend triggers, channels, and policy — while ensuring no extension can crash or block the control plane.

## Overview

```
 author/operator ──▶ corpus CLI ──▶ corpora ──▶ benchmark profiles
                                             (fast/full → attestation)
        │
        ▼
   REST API v2 ──▶ Python SDK ──▶ agent-as-service endpoints
   (OpenAPI,       (typed client)   (auth·budget·admission·policy)
    pagination)
        │
        ├── per-tenant rate limits (429 + Retry-After)
        ├── fleet-events webhook (run/approval/drift/trigger)
        └── plugin hooks (trigger sources·fan-out·policy checks)
                    └─ sandboxed; failures isolated
```

## Design

### Corpus and benchmark tooling

`hiveplane corpus init|add|validate|publish` authors a corpus: `init` scaffolds a versioned corpus from a template (repo agent, triage, generation, classification); `add` appends a task (input, expected outcome, deterministic check, fixtures); `validate`/lint rejects malformed tasks with field-level errors; `publish` freezes an immutable version and records its content hash. Versions are immutable and shareable via export/import bundles (D36).

Benchmark **profiles**: `fast` (deterministic subset for the dev loop) and `full` (all tasks for promotion). The profile used is recorded in the attestation. Production promotion requires a minimum profile (`full` by default); certifying on `fast` is rejected for production contexts. Scheduled corpus expansion periodically proposes new cases from production feedback candidates (D27), but only reviewed, validated cases are added.

### REST API v2

Versioned under `/v2` and described by an OpenAPI document that is the contract. Rules: pagination on every collection (`limit`/`cursor`, stable ordering), a consistent error model (`{error: {code, message, details, request_id}}`), idempotency keys on mutations, and tenant scoping on every route. Contract tests run against the spec in CI; the API freezes at release and any change is a new version.

### Python SDK

A typed client (`hiveplane-sdk`) covering runs, certs, triggers, pipelines, cost, approvals, health, and artifacts. It wraps API v2, handles pagination/retries/auth, and round-trips a full run end-to-end (submit → wait → result). Generated from OpenAPI where practical, with hand-written ergonomics.

### Agent-as-service endpoints

Each workload can be served as `POST /v2/services/{workload}/invoke`. The request passes through the same gates as any run — auth, tenant rate limit, budget/spend cap (D35), admission/certification (D26), and policy (D29) — then dispatches. The endpoint returns a run handle or, with `wait=true`, the result. Nested agent-as-tool calls propagate budget/policy/certification.

### Per-tenant API rate limits

Token-bucket quotas per tenant per route class. Over-limit returns `429` with `Retry-After` and `X-RateLimit-*` headers. Limits are tenant-configurable and one tenant cannot starve others; rate-limit denials are metered.

### Fleet-events webhook

Operators subscribe to `run.*`, `approval.*`, `drift.*`, and `trigger.*` events. Delivery reuses the D36 fan-out manager (HMAC-signed, at-least-once, retries, dead-letter). Subscriptions are tenant-scoped and filter by event type and workload.

### Plugin hooks

Documented hook points: custom trigger sources, fan-out channels, and policy checks. Plugins load from a configured path and run in a restricted/sandboxed context with timeouts and no ambient credentials. Failures are isolated: a throwing or hanging hook is disabled and logged, cannot crash the plane, and never blocks the critical path. A policy hook that must decide fails closed to the built-in default, never to the plugin.

## Data Model

| Table | Key columns |
|-------|-------------|
| `corpora` | `id`, `tenant_id`, `version`, `content_hash`, `template`, `published_at` |
| `corpus_tasks` | `corpus_id`, `task_id`, `input`, `expected`, `check`, `fixtures` |
| `benchmark_profiles` | `id`, `name`, `task_selector`, `min_for_production` |
| `api_keys` | `id`, `tenant_id`, `scopes`, `rate_limit_class` |
| `rate_limit_state` | `tenant_id`, `route_class`, `tokens`, `updated_at` |
| `event_subscriptions` | `id`, `tenant_id`, `events`, `url`, `secret_ref` |
| `plugins` | `id`, `kind`, `entrypoint`, `enabled`, `status` |

## Interfaces/API

```
hiveplane corpus init|add|validate|publish

POST /v2/runs  ·  GET /v2/runs?cursor=...  ·  GET /v2/runs/{id}
POST /v2/services/{workload}/invoke        (auth·budget·admission·policy)
GET  /v2/cost/...  ·  GET /v2/health/...  ·  GET /v2/certs/...
POST /v2/subscriptions { events, url }
GET  /v2/openapi.json
```

## Failure Modes

| Failure | Behavior |
|---------|----------|
| Fast profile used for promotion | Rejected: minimum profile not met |
| Corpus task malformed | `validate` fails with field-level errors; publish refused |
| Over-limit tenant | `429` + `Retry-After`; other tenants unaffected |
| Plugin throws or hangs | Hook disabled and logged; plane continues; critical-path policy fails closed |
| Unpaginated client | Default page size plus cursor always returned |
| Contract drift | Contract tests fail CI; the spec is the source of truth |

## Security

API v2 is tenant-scoped and role-checked on every route; keys are scoped and audited (D33). Agent-as-service inherits every gate — it is not a bypass. Rate limits protect per-tenant availability. Plugins run sandboxed with no ambient secrets and bounded time/memory, and never block admission or dispatch. Imported corpora are validated before use.

## Testing

- Corpus scaffold validates; profiles run the right subset; versions immutable; expansion adds reviewed cases only.
- API v2 contract tests (OpenAPI), pagination/error-model conformance, idempotency.
- SDK round-trips a full run; agent-as-service enforces auth/budget/admission/policy; over-limit returns 429.
- Events webhook receives run/approval/drift events; a crashing plugin does not destabilize the plane.

## Open Questions

- Should profiles be first-class versioned objects, or just task selectors in a corpus?
- Is the SDK generated from OpenAPI, or hand-written over a generated core?
- Do plugins get capability grants (network, secrets), or none by default?
- Is `full` mandatory for all production promotion, or configurable per tenant?

## See Also

- [PRD 05: Features](../prd/05-features.md) — Corpus & Benchmark Tooling, API/SDK & Extensibility
- [PRD 09: Roadmap](../prd/09-roadmap.md) — pillars N, O
- [WBS v0.2.0 Part 16](../wbs/v0.2.0/wbs-v0.2.0-part16-corpus-api.md) — M55–M56
- [Certification v2 Design](certification-v2-design.md) (D26)
- [Learning Loop Design](learning-loop-design.md) (D27)
- [Defense & Policy v2 Design](defense-policy-v2-design.md) (D29)
- [Cost, Showback & ROI v2 Design](cost-roi-v2-design.md) (D35)
- [Operator Experience Design](operator-experience-design.md) (D36)
- [Design Decisions](design-decisions.md) — DD-01, DD-09
