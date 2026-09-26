# D23: Trigger Service v2 Design

> Status: draft

**Milestones:** M27–M28 · **Extends:** D12

## Problem

D12 proved the trigger loop — webhook/alert/PR/cron ingest, rule matching, dedup, watch mode — but left the rule surface loose (YAML `match` with no strict schema), signature and replay protection underspecified, and omitted cooldowns, per-trigger rate limits, backpressure, admission rules, freeze windows, and dead-letter replay. For a 24/7 fleet that runs itself, those gaps are operational and security hazards. D23 supersedes D12 for v0.2.0: a strict trigger DSL, hardened ingest, admission gating that never bypasses certification, freeze windows, and a replayable DLQ.

## Overview

```
   sources          ingest                 evaluate             admit
 ┌──────────┐  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐
 │ webhook  │─▶│ HMAC + replay    │─▶│ DSL matcher      │─▶│ admission    │
 │ github   │  │ verify; dedup/   │  │ + filter         │  │ rule + policy│
 │ alertmgr │  │ cooldown; rate   │  │ + template       │  │ + cert gate  │
 │ cron     │  │ limit/backpressure│  │                 │  └──────┬───────┘
 │ watch    │  └────────┬─────────┘  └────────┬─────────┘         ▼
 └──────────┘           │ suppressed          ▼          Execution API
                   trigger_events      trigger_runs      (idempotent submit)
                              └───── failed ─▶ trigger_dlq ─▶ replay
```

## Design

### Trigger DSL

A trigger is a typed document with strict validation; unknown fields are errors, not ignored:

```
- id: pr-analysis
  source: github
  target: { kind: workload, ref: repo-analysis-agent }
  filter: { event: [pull_request], actions: [opened, synchronize], repo: "myorg/myrepo" }
  task_template: { repo: "{{ event.repository.full_name }}", pr_number: "{{ event.number }}" }
  dedup: { key: "{{ event.repository.full_name }}/{{ event.number }}", window_minutes: 30 }
  cooldown_seconds: 60
  rate_limit: { max_per_minute: 30, burst: 10 }
  admission_rule: staging-auto
  enabled: true
```

`source` ∈ `webhook | github | alertmanager | cron | watch`; `target.kind` ∈ `workload | pipeline` (D24). Invalid matchers, unknown template fields, or an unbounded template fail validation at registration.

### Webhook Ingest (HMAC + Replay Protection)

```
POST /triggers/webhook/{trigger_id}
  X-HivePlane-Signature: sha256=<hex>   X-HivePlane-Timestamp: <unix>   X-HivePlane-Nonce: <opaque>
  → 202 { accepted: true, run_id? } | 409 { error: duplicate|replay } | 401 { error: bad_signature }
```

Signature is HMAC-SHA256 over `timestamp + "." + nonce + "." + raw_body` using the trigger's shared secret. Requests are rejected if the timestamp is outside a ±300s skew window or the `(trigger_id, nonce)` pair was seen within it. Raw body bytes are signed — not a re-serialization — preserving payload ordering. Every rejection is audited.

### Cron Engine

Cron triggers are timezone-aware (`schedule`, `timezone`), evaluated on a 60s jittered tick with a `missed_schedule_policy`: `skip` (default — drop the missed tick), `catch_up` (at most one catch-up fire per missed interval), or `catch_up_all` (every missed interval, bounded by `max_catch_up`). DST boundaries are tested explicitly: a skipped or repeated wall-clock hour must not double-fire or silently drop.

### Dedup, Cooldowns, Rate Limits, and Backpressure

- **Dedup** — a key rendered from the event; if it exists within `window_minutes`, the event is marked `deduplicated` and no run is submitted.
- **Cooldown** — a per-trigger minimum interval; matching events inside it are suppressed and recorded as `suppressed_cooldown`, never queued.
- **Rate limit / backpressure** — a per-trigger token bucket (`max_per_minute`, `burst`) returns `429` + `Retry-After` and records `rejected_rate` (client-retryable, not DLQ); a global ingest budget rejects excess with `rejected_backpressure` when saturated, never silently dropping.

Dedup and cooldown state is persisted in `trigger_events`, so restarts do not forget in-flight keys (unlike D12's cache).

### Payload → Task Templating

Templates are typed substitutions into a declared input schema, not a general expression language: each field has a declared type (`string`, `number`, `boolean`, `array`, `object`); rendered values are length-limited (`max_field_bytes`, `max_total_bytes`) and overflow fails the trigger with a recorded reason; substitution is value-only — no code, no attribute traversal beyond declared paths, no shell interpolation — injection-safe by construction. The rendered task is validated against the target workload's input schema before submission.

### Sources

| Source | Events | Notes |
|--------|--------|-------|
| GitHub | PR `opened`/`synchronize`/`reopened`, `push`, `label`, issue-comment `/hiveplane` | Verifies `X-Hub-Signature-256`; comment command re-triggers |
| Alertmanager | alert `firing` / `resolved` | Dedup by alert `fingerprint`; `resolved` can cancel/annotate a run |
| Custom webhook | arbitrary JSON | HMAC as above |
| Cron / watch | scheduled | see below |

**Watch mode** is a first-class trigger type for 24/7 operators (deployment verification, health checks, fleet watchdog): it fires on schedule, but the workload decides whether to act. `max_concurrent_runs` prevents stacking — if a prior watch run is still active, the tick is skipped, not queued. Watch run state is persisted so a restart resumes rather than losing its place.

### Trigger → Admission Rules

Admission rules bind trigger trust to run context and **never bypass certification**:

| Admission rule | Staging | Production |
|----------------|---------|------------|
| `staging-auto` (trusted) | auto-admit | approval-gated |
| `gated` | approval-gated | approval-gated |
| `deny` | refused | refused |

Regardless of rule or trigger trust, production admission still requires a valid, unexpired certification (DD-09); the service checks it before submitting and blocks with `blocked_cert` otherwise.

### Freeze, History, and DLQ

A freeze calendar (tenant/team/workload scope) pauses triggers and drains running work: matching triggers are recorded as `suppressed_freeze`, and in-flight runs reach a terminal state gracefully (no hard kill) unless the freeze specifies `drain: abort`; freeze windows are audited with the declaring operator. Every evaluation writes a `trigger_events` row and, when applicable, a `trigger_runs` row (`submitted`/`blocked_cert`/`blocked_admission`/`failed`) with an attributed reason. A delivery that fails after retry exhaustion (exponential backoff, max 10) is parked in `trigger_dlq` with its original payload, headers, and reason; `hiveplane triggers replay <id>` re-drives it through the same pipeline (re-verifying dedup/cooldown), and replay is audited.

## Data Model

| Table | Key columns |
|-------|-------------|
| `triggers` | `id`, `tenant_id`, `source`, `target_kind`, `target_ref`, `filter`, `task_template`, `dedup_config`, `cooldown_seconds`, `rate_limit`, `admission_rule`, `timezone`, `missed_schedule_policy`, `enabled` |
| `trigger_events` | `id`, `trigger_id`, `source`, `event_id`, `payload`, `received_at`, `dedup_key`, `outcome` (`accepted`/`deduplicated`/`suppressed_cooldown`/`suppressed_freeze`/`rejected_rate`/`rejected_backpressure`/`rejected_signature`) |
| `trigger_runs` | `trigger_id`, `event_id`, `run_id`, `fired_at`, `status` (`submitted`/`blocked_cert`/`blocked_admission`/`failed`), `reason` |
| `trigger_dlq` | `id`, `trigger_id`, `event_id`, `payload`, `headers`, `failure_reason`, `attempts`, `created_at`, `replayed_at` |

## API / CLI

```
POST /triggers/webhook/{id}   POST /triggers/github      POST /triggers/alertmanager
GET  /triggers                GET  /triggers/{id}        POST /triggers/{id}/enable|disable
GET  /triggers/events         GET  /triggers/dlq         POST /triggers/dlq/{id}/replay
hiveplane triggers list|show|create|test|replay
hiveplane triggers test <id> --payload <file>   # dry-run match + render, no run
```

## Failure Modes

| Failure | Behavior |
|---------|----------|
| Bad/missing HMAC | 401/403, audited; no event row beyond the rejection |
| Replayed timestamp+nonce | 409 `replay`, audited |
| Execution API unavailable | Retry with backoff; park in DLQ after exhaustion |
| Duplicate delivery after restart | Persisted dedup key returns the original `run_id` |
| Template overflow | Trigger fails with `template_overflow`; no partial task submitted |
| Freeze during storm | Triggers suppressed; runs drain; every suppression recorded |

## Security

Ingest is HMAC-SHA256 verified over raw bytes with timestamp+nonce replay protection; per-trigger secrets resolve from the secret store (D33). Templating is typed and length-limited, preventing injection into agent context. Admission rules cannot bypass certification or policy (DD-09, DD-15); production triggers are approval-gated by default. All decisions are append-only and attributed (DD-07). DLQ payloads may contain sensitive data — access is tenant-scoped and audited.

## Testing

- Signature verification: valid accepted; invalid/missing rejected and audited; duplicate `(timestamp, nonce)` rejected within the window.
- Dedup/cooldown: same key starts exactly one run; cooldown suppresses and records; burst beyond the rate bucket returns 429 and is recorded, not lost.
- Cron: fires on schedule; DST boundaries; each missed-schedule policy.
- Templating: correct render; unsafe/overflowing substitution rejected.
- Sources: GitHub PR + `/hiveplane` re-trigger; Alertmanager fingerprint dedup.
- Admission/freeze/DLQ: staging auto-admits, production without approval held, cert still enforced; no admission during a freeze and in-flight runs drain; a failed delivery is parked and replayed with the original payload.

## Open Questions

- Should cooldowns be per-trigger only, or also per dedup-key namespace?
- Can a `resolved` Alertmanager event cancel an in-flight run, or only annotate it?
- Should freeze `drain: abort` be permitted at all, or always graceful?

## See Also

- [PRD 05: Features](../prd/05-features.md) — Run Lifecycle & Execution; Fleet Control & Scheduling · [PRD 09: Roadmap](../prd/09-roadmap.md) — pillar A
- [WBS v0.2.0 Part 2](../wbs/v0.2.0/wbs-v0.2.0-part2-triggers.md) — M27–M28
- [Trigger Service Design](trigger-service-design.md) (D12) — superseded by this doc for v0.2.0
- [Fleet Control Data Model Design](fleet-control-data-model-design.md) (D21) — trigger tables and scoping
- [Certification Pipeline Design](certification-pipeline-design.md) (D10) — certification gate before fire · [Policy Engine Design](policy-engine-design.md) (D4) — admission rules and approvals
- [Orchestration Design](orchestration-design.md) (D24) — pipeline targets · [Secrets & RBAC Design](secrets-rbac-design.md) (D33) — trigger signing secrets
- [Design Decisions](design-decisions.md) — DD-07, DD-09, DD-15
