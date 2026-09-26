# D34: Fleet Execution Design

> Status: draft

**Milestones:** M46–M48 · **Extends:** D2

## Problem

v0.1.0 executes runs in-process on one control-plane host. The Complete Fleet OS must execute across many hosts safely: remote workers that register and heartbeat, leases that survive a worker crash, a scheduler that is orderly under contention, and a control plane that runs multiple replicas without double-acting. It must also prove all of this on demand via chaos drills. This design covers distributed execution (M46), scheduling (M47), and HA + chaos (M48) on top of the run lifecycle (D2).

See [PRD 05: Features](../prd/05-features.md) § Fleet Control & Scheduling and [PRD 09: Roadmap](../prd/09-roadmap.md) pillar K.

## Overview

```
        ┌──────────────── Controller replicas ────────────────┐
        │  leader (active)  ·  standby (leader election)      │
        │  reconcile · scheduler · DLQ · chaos                 │
        └───────────────┬─────────────────────────────────────┘
                        │ lease + assign
        ┌───────────────┼───────────────┬───────────────┐
        ▼               ▼               ▼               ▼
   ┌─────────┐    ┌─────────┐     ┌─────────┐    ┌─────────┐
   │ worker  │    │ worker  │ ... │ worker  │    │ worker  │
   │ identity│    │ heartbeat│    │ drain   │    │ unhealthy│
   └────┬────┘    └────┬────┘     └────┬────┘    └────┬────┘
        └──────────────┴───────────────┴──────────────┘
                        │ execute run via adapter (D25)
                        ▼
                 lease expiry / crash → reclaim → requeue
```

## Design

### Worker Daemon

`hiveplane worker` connects to the plane, authenticates, advertises capabilities (adapters, sandbox backends, capacity, labels), then pulls assigned runs and executes them through the normal adapter path (D25). The daemon reports run state, usage, tool calls, artifacts, and checkpoints; it is stateless between runs — all durable state lives in the plane (DD-05).

### Registration, Heartbeat & Liveness

On start a worker registers identity, capabilities, capacity, and labels. It heartbeats every `heartbeat_interval_s` (default 10) with current load (running/max concurrency) and renews its active leases. Worker states:

| State | Meaning |
|-------|---------|
| `registering` | Identity verified; capabilities advertised |
| `ready` | Eligible for new leases |
| `busy` | At capacity; no new leases |
| `draining` | Finishing in-flight work; no new leases |
| `maintenance` | Excluded from scheduling |
| `unhealthy` | No heartbeat within `heartbeat_timeout_s` (default 30) |
| `deregistered` | Removed; in-flight runs reassigned |

Missed heartbeats mark a worker `unhealthy`; its leased runs are reclaimed by lease expiry.

### Lease-Based Execution

The scheduler grants a run to a worker as a **lease** for `lease_ttl_s` (default 60), renewed on heartbeat and checkpoint. Every state/usage report carries the `lease_id`; a report with a stale lease is rejected — this fences a zombie worker that resumes after reassignment. Lease expiry returns the run to the queue with `attempt += 1` and `reassigned_from` attribution. Because reassignment can replay work, run steps must be idempotent and side-effecting tool calls must carry idempotency keys.

### Crash Detection & Reclaim

A worker crash is detected by missed heartbeats plus lease expiry. Reclaim logic:

1. Find runs whose lease expired or whose worker is `unhealthy`.
2. Requeue with a new lease attempt and attribution.
3. Resume from the last durable checkpoint where the workload supports it (D18); otherwise restart.
4. If a partial side effect is suspected, policy may require an approval before replay.

### Worker Identity

Workers are not anonymous. Enrollment issues a signed worker token (JWT-style) bound to `worker_id`, tenant, and capabilities; mTLS is supported for transport. Both registration and every lease acceptance verify the token; expired, revoked, or mismatched identities are refused with an audit event (rogue-host defense). Tokens rotate; a revocation list is checked on heartbeat. An unauthenticated worker never executes fleet work.

### Worker Lifecycle

- **drain** — finish in-flight runs, accept no new leases.
- **maintenance** — drain plus exclusion from scheduling (used with maintenance windows).
- **deregister** — explicit removal; in-flight runs are reassigned.
The fleet view (`hiveplane workers list`) shows worker id, state, capabilities, load, last heartbeat, active leases, and version.

### Scheduler: Priority, Fairness & Limits

Runs carry a priority (QoS class plus trigger trust/deadline) and enter priority queues. The scheduler dequeues by priority **and** weighted-fairness across workloads/tenants so strict priority cannot starve lower classes. Per-workload and per-tenant **concurrency limits** are enforced. Queue depth, priorities, and waiting reasons are exposed for the queue visualizer.

### Backpressure & QoS

When capacity is exceeded, admission rejects or queues with an explicit reason (`capacity: per_tenant_limit`, `capacity: queue_depth`, `capacity: per_workload_limit`). QoS classes map to scheduling and budget priority:

| Class | Scheduling | Preemptible |
|-------|------------|-------------|
| `guaranteed` | Reserved capacity | No |
| `burstable` | Spare capacity | Yes |
| `best-effort` | Idle capacity only | Yes |

### Preemption

An urgent `guaranteed` run may preempt `best-effort` (and `burstable`) runs to free capacity. Preemption happens **only at idempotent checkpoints** — never mid-side-effect. The victim is paused and requeued (or cancelled per policy) with `preempted_by` attribution; cost already spent is retained and attributed. Preemption events are audited and visible in the timeline.

### Maintenance Windows & Freeze

Integrates with the freeze mechanism (M28). During a window: triggers pause (no new admission), in-flight work drains via worker drain, scheduled runs defer, and trigger deliveries park in the DLQ. Freeze is plane-wide or scoped to a tenant/workload; `hiveplane freeze`/`unfreeze` is role-gated and audited.

### Dead-Letter Queue & Replay

Trigger deliveries that exhaust retries — and controller tasks that repeatedly fail — are parked in a DLQ with the event payload, failure reason, and attempt count. `hiveplane triggers replay <id|--all>` re-injects entries through the normal trigger path, honoring dedup keys. DLQ depth and age are surfaced; entries expire per retention.

### HA Leader Election

Controller replicas coordinate through a PostgreSQL advisory lock (or lease table). Exactly one **active leader** runs the reconciliation/controller loop; the rest are standbys polling for the lock. The leader holds a renewable lease; on leader loss a standby acquires it after lease expiry. Every leader bumps a **fencing token** (`leader_epoch`) included in reconcile actions, so a stale leader cannot double-reconcile — no split-brain. Reconcile actions are idempotent, the state store is single (PostgreSQL, DD-05), and workers are stateless. Failover completes within the configured window.

### Chaos / Game-Day Mode

`hiveplane chaos run <drill> [--scope tenant|sandbox] [--allow-production]` injects seeded failures and reports whether the plane recovered as expected:

| Drill | Injection | Expected response |
|-------|-----------|-------------------|
| `kill-worker` | Kill a worker mid-run | Lease expiry reassigns the run |
| `revoke-cert` | Revoke a certification mid-flight | Run halts or degrades per policy |
| `exhaust-budget` | Force budget exhaustion | Run pauses/fails with accounting |
| `inject-tool-failure` | Make a tool fail | Retry/circuit breaker intervenes |
| `partition-controller` | Drop the leader | Standby promotes without double-reconcile |

Drills are scoped to a sandbox/tenant by default and never touch customer data; production drills require `--allow-production`, an admin role, and an audit record. Each drill emits a pass/fail report: injected fault, observed response, timing, verdict.

## Data Model

Canonical worker/pipeline entities live in [D21](fleet-control-data-model-design.md); this design adds execution records:

| Table | Contents |
|-------|----------|
| `workers` | id, tenant, state, capabilities, capacity, labels, version, last heartbeat |
| `worker_tokens` | token id, worker id, issued/expires, revoked, rotation lineage |
| `leases` | lease id, run id, worker id, attempt, granted at, expires at, fencing token |
| `maintenance_windows` | scope (plane/tenant/workload), starts/ends, reason, created by |
| `dlq_entries` | entry id, source (trigger/reconcile), payload ref, reason, attempts, created at |
| `leader_leases` | lock key, leader replica id, leader_epoch, renewed at, expires at |
| `chaos_drills` | drill id, kind, scope, guardrails, started/finished, result |
| `chaos_reports` | drill id, injected fault, observed response, timing, pass/fail |

## Interfaces / API

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/workers/register` | Register a worker (identity verified) |
| POST | `/workers/{id}/heartbeat` | Liveness, load, lease renewal |
| POST | `/workers/{id}/drain` | Begin draining |
| DELETE | `/workers/{id}` | Deregister |
| GET | `/workers` | Fleet view |
| GET | `/queue` | Depth, priorities, waiting reasons |
| GET | `/dlq` / POST `/dlq/{id}/replay` | Inspect and replay parked work |
| GET | `/cluster/leader` | Current leader and epoch |
| POST | `/chaos/drills` | Run a scoped drill |

CLI: `hiveplane worker`, `hiveplane workers list`, `hiveplane triggers replay`, `hiveplane freeze|unfreeze`, `hiveplane chaos`.

## Failure Modes

| Failure | Detection | Response |
|---------|-----------|----------|
| Worker crash | Missed heartbeat + lease expiry | Reclaim, requeue, resume from checkpoint |
| Zombie worker returns | Stale `lease_id` on report | Report rejected (fencing) |
| Rogue/unauthenticated host | Identity verification | Registration/lease refused, audited |
| Capacity exceeded | Scheduler limits | Backpressure rejection with reason |
| Urgent run starved | QoS + fairness | Preemption at checkpoint with attribution |
| Leader loss | Lease expiry | Standby promotion, epoch bump, no double-reconcile |
| Trigger delivery exhausted | Retry count | Parked in DLQ, replayable |
| Drill escapes scope | Guardrail check | Refuse drill without explicit production flag |

## Security

- Worker identity is mandatory before distributed execution ships; unauthenticated hosts are refused.
- Lease fencing prevents a reclaimed worker from double-applying side effects.
- DLQ payloads may contain sensitive trigger data and inherit retention/redaction rules.
- Chaos drills are sandboxed by default; production drills require explicit authorization and audit.
- All worker, scheduler, leader, and drill actions are attributed and audited (DD-07).

## Testing

A run executes on a remote worker on a second host; killing the worker causes lease expiry and reassignment; a worker without a valid identity is refused; drain completes in-flight work and stops new assignments; priority ordering under contention; per-workload/per-tenant limits enforced; over-capacity rejection carries a reason; preemption with attribution; no admission during a maintenance window and in-flight work drains; a dead trigger replays from the DLQ; a second controller replica does not double-reconcile; leader failover with no split-brain; each chaos drill produces the expected recovery/halt and a pass/fail report.

## Open Questions

- Lease TTL vs. checkpoint frequency — the trade-off between fast reassignment and duplicate work.
- Whether preemption should always requeue or sometimes cancel per QoS.
- Coordination primitive: PostgreSQL advisory lock vs. a dedicated lease table under high churn.
- How long to retain DLQ entries before expiry, and whether replay is at-least-once or exactly-once.
- Which drills are safe enough to run automatically on a schedule vs. manually.

## See Also

- [Run lifecycle design](run-lifecycle-design.md) (D2) — run states that leases and the scheduler drive
- [Durable resume design](durable-resume-design.md) (D18) — checkpoints used on reclaim
- [Runtime adapter v2 design](runtime-adapter-v2-design.md) (D25) — adapter execution on workers
- [Orchestration design](orchestration-design.md) (D24) — child runs scheduled across the fleet
- [Certification pipeline design](certification-pipeline-design.md) (D10) — cert revocation drill
- [Reconciliation design](reconciliation-design.md) (D22) — the leader-elected controller loop
- [PRD 05: Features](../prd/05-features.md) — Fleet Control & Scheduling
- [WBS v0.2.0 Part 11](../wbs/v0.2.0/wbs-v0.2.0-part11-secrets-workers.md) — M46
- [WBS v0.2.0 Part 12](../wbs/v0.2.0/wbs-v0.2.0-part12-scheduling-ha.md) — M47–M48
