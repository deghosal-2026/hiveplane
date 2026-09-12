# D2: Run Lifecycle Design

> Status: draft

## Problem

Every run, regardless of runtime, must move through one observable and intervenable state machine. After the PRD rewrite, the lifecycle must also handle: certification-status checks at admission (refuse uncertified to production), trigger-originated runs (webhook/alert/PR/cron → auto-start), sandbox execution for destructive runs, tool-output shaping in the run flow, and result fan-out on completion/failure/escalation.

See [PRD 02: Architecture](../prd/02-architecture.md) for the high-level flow and [PRD 05: Features](../prd/05-features.md) for run lifecycle features.

## States

```
                              ┌──────────────────────────────────────────────┐
                              │ TRIGGER & INGRESS                             │
                              │ webhook / alert / PR / cron                    │
                              └───────────────┬──────────────────────────────┘
                                              │
                                              ▼
  Client ──> Execution API ──> Admission (cert check + budget check + policy)
                                   │
                    ┌──────────────┼──────────────┐
                    │ uncertified  │ certified     │
                    │ / provisional│                │
                    ▼              ▼                │
              SANDBOX ONLY    QUEUED               │
                    │              │                │
                    │              ▼                │
                    │          RUNNING ◄────── resume
                    │           │  ▲                │
                    │    ┌──────┤  │                │
                    │    │      ▼  │                │
                    │    │   PAUSED ────> CANCELLED │
                    │    │      │                   │
                    │    │      └────> FAILED       │
                    │    │                          │
                    │    ▼                          │
                    │  COMPLETED                   │
                    │    │                          │
                    └────┤                          │
                         │                          │
                         ▼                          │
                    FAN-OUT <───────────────────────┘
                  (Slack/Teams/Jira/PR/webhook)
```

### State Table

| State | Meaning |
|-------|---------|
| `queued` | Accepted, awaiting an adapter slot |
| `running` | Executing in adapter (sandbox or standard context) |
| `paused` | Suspended by operator or policy escalation; state retained |
| `completed` | Finished successfully; fan-out triggered |
| `failed` | Terminated by error, budget/policy violation, or injection block; fan-out triggered |
| `cancelled` | Stopped by operator; fan-out triggered (if configured) |

## Admission Phase

Admission is the gate every run passes through before `queued`. It performs the following checks in order:

1. **Certification-status check** (DD-09, T8) — the registry is queried for the workload's current `certification.status`. If the target context is `production` and the status is not `certified` (or the attestation is expired/unsigned), admission is refused. `uncertified` and `provisional` workloads may only run in `sandbox` or `staging` contexts.

2. **Model-identity binding check** (DD-10, T11) — the runtime model identity is compared against the model identity in the signed attestation. A mismatch blocks the run. This prevents model-swap attacks.

3. **Budget check** (DD-04) — remaining per-day and per-team budgets are checked; if exhausted, the run is rejected or queued.

4. **Policy pre-evaluation** — the policy engine evaluates context-aware permissions for the run's target environment, data sensitivity, and blast radius. See [Policy engine](policy-engine-design.md).

5. **Trigger dedup** — for trigger-originated runs, idempotency keys prevent duplicate runs from the same event.

### Admission Outcomes

| Outcome | Condition |
|---------|-----------|
| `admitted → queued` | All checks pass |
| `admitted → sandbox_only` | Uncertified/provisional workload, or destructive action class |
| `refused: certification` | Production context, status not `certified` or attestation expired |
| `refused: model_swap` | Runtime model identity ≠ attestation model identity |
| `refused: budget` | Budget exhausted |
| `refused: policy` | Policy engine denies the run at admission |

## Trigger-Originated Runs

Triggers auto-start runs without human submission (PRD 05: triggers, CUJ-3). The Trigger & Ingress Service handles event intake:

| Trigger Type | Source | Match Criteria | Auto-Start Behavior |
|--------------|--------|----------------|---------------------|
| `webhook` | HTTP POST to `/hooks/{workload}` | Payload fields, headers | Start run with payload as task input |
| `alert` | PagerDuty, AlertManager, custom | Severity, service, labels | Start run with alert context as task input |
| `github_pr` | GitHub webhook (PR events) | Event type, file paths, branch | Start run with PR context as task input |
| `cron` | Internal scheduler | Schedule expression | Start run in `watch` or `scheduled` mode |

### Trigger Flow

1. Event arrives at the Trigger & Ingress Service.
2. Service matches the event against workload trigger rules (from the manifest `spec.triggers`).
3. On match, the service checks certification status (same admission path as manual submission).
4. If admitted, a run is created with the trigger event as the task input and `trigger_origin` metadata (source, event ID, timestamp).
5. Dedup: events with the same idempotency key within a window are ignored.

### Scheduled and Watch Modes

- **one-shot** — a single run per trigger event.
- **watch** — the agent continuously monitors a target and starts runs on changes.
- **scheduled** — periodic runs at a fixed cadence (e.g., deployment verification every 6 hours).

`max_concurrent` caps how many trigger-originated runs may be active simultaneously for a workload.

## Sandbox Execution Path

When a run involves a destructive action class or the workload's `sandbox.enabled` is `true`, the run executes in an isolated sandbox context (DD-14, T13):

1. The policy engine flags the run as requiring sandbox execution.
2. The execution sandbox provisions an isolated context with the manifest's `resource_caps` (memory, CPU, wall-clock) and `egress` configuration.
3. Tool calls are routed through the policy boundary — the sandbox does not bypass policy.
4. The filesystem is isolated; no shared filesystem with the control plane.
5. Network egress is restricted to the `allow` list; cloud metadata endpoints are always denied.
6. On completion or failure, the sandbox context is torn down.

See [Runtime adapter](runtime-adapter-design.md) for the adapter contract that implements sandbox execution.

## Tool-Output Shaping in the Run Flow

Tool outputs are shaped at the boundary before reaching the agent context window (DD-13, T14):

1. The adapter reports a tool call result to the control plane.
2. The output-shaping layer applies the manifest's `output_shaping` rules:
   - **Filter** — pattern-based redaction/masking (secrets, PII, credit card numbers).
   - **Truncate** — if output exceeds `max_bytes`, it is truncated per `truncate_strategy` (`head`, `tail`, `summary`).
   - **Budget** — cumulative tool-output bytes per run are tracked; if the run's output budget is exceeded, further outputs are aggressively truncated.
3. **Injection scan** — if `injection_scan` is `true`, the output is scanned for prompt-injection patterns. Suspicious patterns escalate for approval or are deterministically blocked.
4. Only the shaped output is passed back to the agent context.

This prevents large tool payloads from silently blowing context windows and prevents injection via malicious tool responses.

## Result Fan-Out Transition

On terminal state transitions (`completed`, `failed`, `escalation`), the Result Fan-out Service is invoked (PRD 05: result delivery, CUJ-10):

| Terminal State | Fan-Out Destinations | Attachments |
|----------------|---------------------|-------------|
| `completed` | `spec.fan_out.on_completed` | trace link, attestation link |
| `failed` | `spec.fan_out.on_failed` | trace link, failure reason |
| `escalation` (paused for approval) | `spec.fan_out.on_escalation` | trace link, evidence, approval link |

### Fan-Out Flow

1. Run reaches a terminal state (or is paused for escalation).
2. State Store records the terminal transition (persisted before fan-out is acknowledged).
3. Fan-out Service reads the workload's `spec.fan_out` configuration.
4. For each configured destination, a message is composed with the run result, trace link, and (for certifications) attestation link.
5. Delivery is attempted with retries; delivery status is recorded.
6. Failed deliveries are logged but do not block the run's terminal state.

Supported destination types: `slack`, `teams`, `jira`, `github_pr_comment`, `webhook`.

## Transitions

| From | To | Trigger |
|------|----|---------|
| — | `queued` | Admission passes (manual or trigger-originated) |
| — | `sandbox_only` | Uncertified/provisional workload, or destructive action |
| — | `refused` | Admission fails (certification, model swap, budget, policy) |
| `queued` | `running` | Adapter picks up the run |
| `running` | `paused` | Operator action, policy escalation, or approval required |
| `paused` | `running` | Operator resumes (with optional state edit) |
| `running` | `completed` | Runtime reports success |
| `running` | `failed` | Runtime error, budget/policy violation, injection block, or sandbox escape attempt |
| `running`/`paused` | `cancelled` | Operator stops the run |
| `completed`/`failed`/`paused(escalation)` | fan-out | Terminal state triggers Result Fan-out Service |

## Requirements

- transitions are persisted before side effects are acknowledged
- every transition is attributed (who/what caused it) and audited
- pause/resume must not lose run context (DD-05)
- runs survive a control-plane restart (durable state in PostgreSQL)
- certification status is checked at every admission — no caching across runs that could let a revoked certification through
- trigger-originated runs carry `trigger_origin` metadata for attribution
- sandbox contexts are torn down on terminal transition
- fan-out delivery status is recorded but does not block terminal state
- tool-output shaping is applied to every tool call result before it reaches the agent

## Open Questions

- cooperative vs preemptive pause semantics per adapter
- max pause duration before a run is reclaimed
- trigger dedup window length and whether it is per-trigger-type
- whether sandbox provisioning adds meaningful latency to admission
- fan-out retry policy and dead-letter handling
- whether `watch` mode runs should be exempt from per-day budget caps

## See Also

- [Workload manifest](workload-manifest-design.md) — trigger rules, sandbox config, output shaping, fan-out config
- [Registry service](registry-service-design.md) — certification status, attestation storage, admission enforcement
- [Policy engine](policy-engine-design.md) — context-aware policy, blast-radius scoring, injection defense
- [Budget enforcement](budget-enforcement-design.md) — budget checks at admission and during execution
- [Runtime adapter](runtime-adapter-design.md) — sandbox execution contract, output shaping layer
- [State store](state-store-design.md) — run events, fan-out deliveries, trigger rules
- [Telemetry](telemetry-design.md) — run traces, health signals
- [Design decisions](design-decisions.md) — DD-04, DD-05, DD-09, DD-13, DD-14
