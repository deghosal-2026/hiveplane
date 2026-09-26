# D36: Operator Experience Design

> Status: draft

**Milestones:** M51–M54, M60 · **Extends:** D9, D15

## Problem

D9 and D15 give a minimal UI and five fan-out destinations. v0.2.0 requires that operators never come to HivePlane to learn something happened and can act from anywhere: nine channels, interactive and mobile approvals, escalation routing, notification preferences, an operator UI v2, a deep CLI, a natural-language copilot that is itself a certified workload, an instant incident stop, a durable artifact store, portable bundles, and time-travel replay/forking. These are one operator experience; splitting them yields inconsistent attribution and unsafe shortcuts.

## Overview

```
 events (run/approval/drift/cost)         Operator Experience
 ┌────────────┐   ┌──────────────────────────────────────────────┐
 │ run/policy │──▶│ delivery: 9 channels · templates · audit      │
 │ cert/cost  │   │ approvals: chat · mobile · queue v2 · on-call │
 └────────────┘   │ prefs: routing · batching · quiet hours       │
                  └──────────────┬───────────────────────────────┘
                                 ▼
        ┌──────────┬──────────┬──────────┬──────────┬──────────┐
        │ UI v2    │ CLI      │ ask      │ incident │ artifacts│
        │ (RBAC)   │ depth    │ copilot  │ mode     │ + bundles│
        └──────────┴──────────┴──────────┴──────────┴──────────┘
                                 ▼
                         replay · fork · A/B diff
```

## Design

### Nine-channel fan-out

Destinations: Slack, Teams, Jira, GitHub PR comment, PagerDuty, Discord, Linear, email, generic webhook. Each is a transport behind one `DeliveryManager` with per-destination timeout, success signal, the D15 retry ladder, and a dead-letter store. Delivery is at-least-once; consumers dedup by `(run_id, event_type)`. Config is per-workload with a team default; no config means UI-only.

### Templated payloads

Every message renders from a per-event template into a common envelope: event type, run/workload/tenant/team, status, summary, `trace_link`, `attestation_link`, and `public_verify_link` (unauthenticated verification, D26). Adapters render the envelope into Block Kit / Adaptive Card / Jira fields / markdown / email HTML / structured JSON. Templates are versioned; a render failure falls back to plain text and never drops the event.

### Interactive approvals

Slack and Teams action buttons call a signed webhook carrying `approval_id`, `run_id`, and a short-lived signature. The handler verifies approver identity (chat user → tenant membership/role, D33), binds the decision to exactly that approval, records the reason, and attributes it (DD-07). Approvals are single-use: a resolved `approval_id` cannot be replayed; a second attempt returns `already_resolved`.

### Mobile approvals

Email and PagerDuty carry a link to a lightweight, tokenized approve page (tenant-scoped, expiring, one-tap approve/deny with reason). It renders only the action summary, blast radius, trace link, and deadline; the token is verified server-side and single-use.

### Approval queue v2

Bulk approve/deny (one decision applied to N approvals, each audited separately), delegation (reassign to an approver/role for a window), and threaded comments visible in UI and fan-out. Every action is role-gated (D33) and attributed.

### Escalation and on-call routing

An approval has a response window (default 15m). No response → escalate to the next on-call operator/rotation, re-notify, and extend or auto-resolve per policy. Routing is per-team; escalation history is audited.

### Notification preferences

Per-team routing (which channels/events), batching (digest a window into one message), and quiet hours (suppress non-critical; critical pages still deliver). Prefs apply after event classification and before delivery; suppressed events are recorded, not lost.

### Delivery audit and retries

Every delivery records destination, attempt count, status, timestamps, and error. Failures retry per D15 and dead-letter with operator-visible resolution. Delivery state appears in run detail and the trigger log.

### Operator UI v2

Login plus RBAC-lite with server-side enforcement (the UI is never the only gate). Screens: live streaming run view, run timeline, cost explorer, ROI and health dashboards, trigger log, queue visualizer (depth/priority/waiting reason), diff viewer (regression and run-to-run), global search (runs/logs/approvals/artifacts), onboarding wizard (connect model → register → certify → first trigger), and approval queue v2. A viewer sees no approve/promote/kill-switch controls.

### CLI depth

`hiveplane health|cost|report|replay|top|logs` plus shell completions. Commands are thin clients of API v2 (D37) that honor tenant/role; `report` renders the digest/evidence pack and `top` ranks agents by spend/health/ROI.

### `hiveplane ask` NL operator copilot

`ask` answers live-state questions ("why did run 42 fail?", "show team X spend last week", "who approved the destructive call?"). It is itself a registered, certified, budgeted workload: it runs through admission, policy, budget, and the LLM seam, and every query is attributed to the caller's tenant/team. It is read-only by default — it queries state and never mutates without an explicit confirmation path — and is benchmarked against a fixed question corpus that it must keep passing.

### Incident mode

`hiveplane fleet pause` (and the UI big red button) halts the fleet in under 5s: sets a global halt flag checked directly at admission/dispatch (bypassing normal propagation), drains triggers, refuses new runs, lets in-flight runs checkpoint, and broadcasts owners via fan-out. Recovery (`hiveplane fleet resume`) is attributed and writes an incident record (trigger, scope, halt/resume times, owners notified).

### Artifact store

Run artifacts (files/reports) are stored in a local or S3/MinIO backend, content-addressed by hash, linked to runs/pipelines, retained per tenant policy, and linked in fan-out payloads and run detail. Retention and purge coordinate with D38.

### Export/import bundles

`hiveplane export/import` bundles manifest + corpus + policy pack + provenance signatures. Import verifies signatures, refuses tampered bundles, supports `--dry-run`, and never auto-executes imported workloads — they register → certify → admit as usual.

### State diff, replay, and forking

Frame-by-frame replay reconstructs a run from checkpoints/events; run-to-run diff compares state/tool/model/cost/outcome; forking copies a run's state for edit and re-run from the fork point; A/B replay runs two versions/configs on identical input. Replays and forks are side-effect-free by default (no fan-out delivery, no destructive tools) and clearly marked non-production.

## Data Model

| Table | Key columns |
|-------|-------------|
| `fanout_deliveries` | `id`, `run_id`, `tenant_id`, `event_type`, `destination`, `status`, `attempts`, `error`, `delivered_at` |
| `approval_decisions` | `approval_id`, `operator_id`, `decision`, `reason`, `channel`, `decided_at` (unique, single-use) |
| `notification_prefs` | `team_id`, `events`, `channels`, `batch_window`, `quiet_hours` |
| `escalations` | `approval_id`, `level`, `target`, `fired_at`, `responded_at` |
| `artifacts` | `id`, `run_id`, `tenant_id`, `location`, `content_hash`, `expires_at` |
| `incidents` | `id`, `scope`, `halted_at`, `resumed_at`, `owners_notified` |
| `replays` | `id`, `source_run_id`, `mode`, `fork_point`, `side_effects` |

## Interfaces/API

```
POST /approvals/{id}/decision { decision, reason }      (Slack/Teams/mobile token)
GET  /deliveries?run_id=...&status=...   ·  POST /deliveries/{id}/retry
GET  /search?q=...&type=run|approval|artifact
POST /ask { question }                  → attributed, read-only, certified workload
POST /fleet/pause  ·  POST /fleet/resume
GET  /artifacts/{id}  ·  POST /export  ·  POST /import?dry_run=true
POST /replay/{run_id}  ·  POST /runs/{id}/fork  ·  POST /replay/ab
```

## Failure Modes

| Failure | Behavior |
|---------|----------|
| Destination down | Retry ladder → dead-letter; operator resolves; fallback channel notified |
| Replayed approval | `already_resolved`; attempt audited, not applied |
| Quiet hours vs critical | Critical pages bypass suppression; suppression recorded |
| Halt flag unreadable | Admission fails closed (no new runs) |
| Tampered import | Signature verification fails; import refused before any write |
| Fork/replay side effects | Disabled by default; explicit flag required and audited |

## Security

Approval tokens/signatures are short-lived, single-use, and bound to a specific `approval_id`/`run_id`; approver identity maps to tenant membership/role (D33) and RBAC is enforced server-side. `ask` is read-only and audited per query — it must not become a mutation backdoor. Incident mode is admin-only and audited. Artifacts and bundles are tenant-scoped, encrypted at rest, and signature-verified on import.

## Testing

- Each of the nine channels delivers against a mocked/fixture endpoint, including retry/dead-letter paths.
- Slack/mobile interactive approval resolves a run, is attributed, and rejects replay.
- Escalation pages the next operator when the first does not respond.
- Prefs batch/suppress/route correctly; critical bypasses quiet hours.
- UI view-model and RBAC-gated action tests plus Playwright e2e for the wizard and key views.
- `ask` answers a fixed live-state question set under budget/cert and cannot mutate.
- Incident mode halts in <5s and broadcasts; replay/fork are side-effect-free.

## Open Questions

- Should batching apply to approvals (latency risk) or only informational events?
- Is the mobile approve token per-approval or per-operator-window?
- Do forked runs share the parent's budget, or get a child budget?
- Should `ask` support mutations behind an explicit two-step confirm, or stay read-only permanently?

## See Also

- [PRD 05: Features](../prd/05-features.md) — Result Delivery, Operator Surface, Artifacts & Portability
- [PRD 09: Roadmap](../prd/09-roadmap.md) — pillars I, M, Q
- [WBS v0.2.0 Part 14](../wbs/v0.2.0/wbs-v0.2.0-part14-delivery-ui.md) — M51–M52
- [WBS v0.2.0 Part 15](../wbs/v0.2.0/wbs-v0.2.0-part15-cli-artifacts.md) — M53–M54
- [WBS v0.2.0 Part 18](../wbs/v0.2.0/wbs-v0.2.0-part18-distribution-replay.md) — M60
- [Operator UI Design](operator-ui-design.md) (D9)
- [Result Fan-out Design](result-fanout-design.md) (D15)
- [Secrets & RBAC Design](secrets-rbac-design.md) (D33)
- [Platform API & Extensibility Design](platform-api-design.md) (D37)
- [Reporting, Tenancy & Distribution Design](reporting-tenancy-distribution-design.md) (D38)
- [Design Decisions](design-decisions.md) — DD-07
