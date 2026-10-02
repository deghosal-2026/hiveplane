# D22: Reconciliation Design

> Status: implemented (M26)

**Milestones:** M26 · **Extends:** —

## Problem

A fleet declared only through ad-hoc API calls drifts: someone edits a manifest, deletes an agent, or changes a threshold and nothing notices. HivePlane needs the Kubernetes-defining pattern — declare the fleet in git, then continuously converge actual state to it. The controller must register missing workloads, deregister removed ones, update changed manifests, trigger re-certification on behavioral change, and quarantine workloads outside desired state. Crucially, it must not become a destructive "apply" tool: history is never deleted and destructive actions are gated.

## Overview

```
   git repo / dir                     control plane
  ┌──────────────┐   load    ┌──────────────────────────────────┐
  │ workloads/   │──────────▶│ Observer ▶ Differ ▶ Planner      │
  │ policies/    │           │      │                    │       │
  │ triggers/    │           │  registry,          dry-run?     │
  │ budgets/     │           │  certs, policy           ▼       │
  └──────────────┘           │                     plan/apply    │
      ▲ poll/webhook         │                          │        │
      │ read-only            │              destructive? ▼        │
      └──────────────────────│ Recorder ◀── gate (policy)          │
        (no write-back)      │    └▶ drift_records, reconcile_runs  │
                             └──────────────────────────────────┘
```

## Design

### Desired-State Spec Format and Loader

The desired state is a fleet manifest set — a typed document tree. Each document has `kind`, `metadata.name`, and a content hash; the set loads atomically, and a parse/validation error fails the whole revision with nothing applied. The loader accepts a local directory or a git source (`url`, `path`, `ref`) and produces a validated `DesiredSet`: it fetches the pinned `ref` (branch/tag/commit) into a temp worktree and records the resolved revision. It is strictly read-only — **never pushes** to the source repo.

```
kind: workload      metadata: { name: incident-triage-agent, team: platform }
  spec: { runtime: {adapter: langgraph}, model: {identity: "gpt-4o-2024-08-06"} }
kind: policy_pack   metadata: { name: platform-baseline, version: 3 }
  spec: { inherits: [org-baseline], rules: [...] }
kind: trigger       metadata: { name: pr-analysis, workload: repo-analysis-agent }
  spec: { source: github, filter: {...}, admission_rule: staging-auto }
kind: budget        metadata: { team: platform }
  spec: { periods: {day: 50, week: 250}, hard_stop: true }
```

### Controller Loop

`observe → diff → plan → act → record` on a fixed tick and on webhook/poll change:

1. **Observe** — read current state from registry, certification, policy, and budget stores.
2. **Diff** — compare observed vs. desired per object, field by field.
3. **Plan** — order deltas into actions; classify each additive/soft vs. destructive.
4. **Act** — execute through existing service APIs (never direct table writes), gated by conflict policy.
5. **Record** — persist `reconcile_runs`, per-object outcomes, and `drift_records`; emit audit events.

### Reconcile Actions

| Action | Trigger | Destructive? |
|--------|---------|--------------|
| Register missing workload | In desired, absent observed | No (additive) |
| Update manifest | Content hash differs | Soft; behavioral fields trigger re-cert |
| Trigger re-certification | Changed `runtime`/`model`/`tools`/`corpus` or cert threshold | No |
| Enforce policy-pack version | Observed pack version ≠ desired | Soft |
| Deregister removed workload | Absent desired, present observed and managed | Yes — history retained |
| Quarantine unmanaged workload | Present observed, not managed by any desired source | Yes — runs blocked, not deleted |

Deregistration marks a workload inactive and removes it from admission; it never deletes runs, attestations, or audit. Quarantine blocks new runs but preserves the object so an operator can adopt or remove it explicitly.

### Drift Records, History, and Conflict Policy

Every field-level mismatch produces a `drift_record` (`object_ref`, `field`, `desired_value`, `observed_value`, `detected_at`, `resolution` ∈ `declared_wins`/`observed_wins`/`unresolved`). `reconcile_runs` records revision, mode, action counts, outcome, and timestamps; both surface via API and `hiveplane reconcile status`. Conflicts resolve per field class, not globally:

| Field class | Default | Rationale |
|-------------|---------|-----------|
| `spec.*` declarative fields | declared-wins | Git is the source of truth |
| Runtime/observed state (`status`, `last_seen`, leases) | observed-wins | Never overwritten by desired state |
| Operator-pinned fields (`pin: true`) | observed-wins | Emergency override without fighting the controller |
| Certification status | observed-wins | Controller requests re-cert; it does not assert status |

Pinned fields are reported as drift with resolution `ignored`, keeping divergence visible rather than silently hidden.

### Guardrails Against Destructive Reconcile

- Default mode is additive/soft; destructive actions require `spec.guardrails.allow_destructive: true` **and** a permitting policy decision.
- A first run against empty/unknown observed state is plan-only; `apply` requires a second confirmation or `--force`.
- Deregistration and quarantine are rate-limited (max N per reconcile) so a bad revision cannot wipe the fleet.
- An empty desired set never cascades to mass deregistration unless explicitly allowed.
- Every destructive action is audited with the revision that justified it.

### Git Source, Concurrency, and CLI

Sources are `git` (`url`, `path`, `ref`, `auth_secret_ref`) or `directory`. Change detection uses periodic poll (default 60s) and/or a webhook that verifies HMAC before triggering a reconcile; auth uses a secret reference resolved through the secret store (D33), never inline in the tree. Reconcile is single-writer: a controller takes a PostgreSQL advisory lock keyed by source id before acting, skips the tick if held, and with multiple replicas (D34) one reconciles while the rest observe — no double-acting. The lock releases on completion or connection loss.

```
hiveplane reconcile status              # last revision, last reconcile, open drift count
hiveplane reconcile plan [--source s]   # dry-run: prints the action set, mutates nothing
hiveplane reconcile apply [--source s]  # executes soft actions; destructive gated
```

## Data Model

| Table | Key columns |
|-------|-------------|
| `desired_specs` | `id`, `source_id`, `kind`, `name`, `revision`, `content_hash`, `spec`, `updated_at` |
| `reconcile_state` | `source_id`, `last_revision`, `last_observed_hash`, `last_reconcile_at`, `status` |
| `reconcile_runs` | `id`, `source_id`, `revision`, `mode` (plan/apply), `actions`, `counts`, `outcome`, timestamps |
| `drift_records` | `id`, `object_ref`, `field`, `desired_value`, `observed_value`, `detected_at`, `resolution`, `reconcile_run_id` |

## Failure Modes

| Failure | Behavior |
|---------|----------|
| Git unreachable | Keep last-known-good revision; mark source `blocked`; do not act on stale desired state |
| Invalid revision | Reject atomically; record validation errors; take no action |
| Advisory lock contention | Skip tick; second replica stays passive; no double-action |
| Partial action failure | Independent actions continue; failures marked `blocked` and retried next tick |
| Bad revision mass-deregisters | Rate limit + destructive gate stops the cascade; operator sees the plan first |

## Security

Desired state is untrusted input: strict schema validation, no code execution, no write-back to git. Git credentials are secret references, never inline. The controller acts only through authenticated service APIs, so it cannot bypass certification, policy, or budget enforcement (DD-15). Destructive actions require an explicit policy decision and are audited with attribution (DD-07). Advisory-lock ownership prevents split-brain acting across replicas.

## Testing

- Idempotency: two consecutive `apply` runs produce one action set and a no-op second run.
- Add/remove/update paths: register missing, deregister removed (history retained), update manifest, trigger re-cert on threshold change.
- Dry-run safety: `plan` mutates no state.
- Concurrency: two controller processes, one lock — exactly one acts.
- Guardrails: destructive action without permission is planned but blocked; rate limit caps mass actions.
- Conflict policy: pinned fields stay observed-wins and are recorded as ignored drift.

## Implementation (M26)

| Module | Responsibility |
|--------|----------------|
| `hiveplane.reconcile.loader` | Fleet-manifest-set validation and directory/git loading (`DesiredStateLoader`, `GitSource`) |
| `hiveplane.reconcile.observe` | Read-only snapshot of registry/policy state (`ServiceObserver`, `ObservedState`) |
| `hiveplane.reconcile.conflict` | Per-field conflict classes and payload merge (`ConflictPolicy`, `merge_declared_wins`) |
| `hiveplane.reconcile.differ` | Desired-vs-observed deltas and drift records (`Differ`) |
| `hiveplane.reconcile.planner` | Action classification and guardrails (`Planner`, `Guardrails`) |
| `hiveplane.reconcile.executor` | Action execution through `RegistryService` (`ActionExecutor`) |
| `hiveplane.reconcile.controller` | The observe→diff→plan→act→record loop (`ReconcileController`) |
| `hiveplane.reconcile.locking` | Single-writer per-source lock (in-memory and Postgres advisory) |
| `hiveplane.reconcile.source` | Source references, poll-on-change, webhook HMAC verification (`SourceWatcher`) |

Drift resolution uses the frozen M25 `DriftResolution` values: declarative mismatches
resolve `declared_wins` once applied, observed-wins mismatches are recorded
`observed_wins`, and drift that a guardrail left unacted stays `unresolved`.
Re-certification on a threshold change is requested through the additive
`RegistryService.request_re_certification`; quarantine forces
`CertificationStatus.QUARANTINED` without deleting history.

## Open Questions

- Should re-certification be requested inline or deferred to the drift scheduler when a change is detected?
- How long should `ignored` drift records be retained before archival?
- Is a per-source interval enough, or do teams need per-object cadences?
- Should unmanaged workloads be quarantined by default or only warned, to ease incremental adoption?

## See Also

- [PRD 05: Features](../prd/05-features.md) — Fleet Control & Scheduling (desired-state reconciliation)
- [PRD 09: Roadmap](../prd/09-roadmap.md) — pillar K
- [WBS v0.2.0 Part 1](../wbs/v0.2.0/wbs-v0.2.0-part1-foundation.md) — M26
- [Fleet Control Data Model Design](fleet-control-data-model-design.md) (D21) — `desired_specs`, `drift_records`
- [Registry Service Design](registry-service-design.md) (D3) — registration and manifest updates
- [Certification Pipeline Design](certification-pipeline-design.md) (D10) — re-certification on change
- [Policy Engine Design](policy-engine-design.md) (D4) — destructive-action permission
- [Secrets & RBAC Design](secrets-rbac-design.md) (D33) — git credential references
- [Fleet Execution Design](fleet-execution-design.md) (D34) — leader election and replica coordination
- [Design Decisions](design-decisions.md) — DD-07, DD-15
