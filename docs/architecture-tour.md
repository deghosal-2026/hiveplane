# HivePlane Architecture Tour

A guided walkthrough of how HivePlane v0.2.0 — the Complete Fleet OS — is put together,
why it is shaped this way, and where each subsystem lives in the code. For the formal
system description, see [PRD 02: Architecture](prd/02-architecture.md); for the design
rationale behind individual decisions, follow the links to `docs/design/`.

> **The thesis:** production is earned, not assumed. An agent must pass a reproducible
> benchmark before it is admitted, and it is re-certified when it drifts. Everything else
> in the architecture exists to make that control loop safe to run at fleet scale.

---

## 1. The control plane in one picture

```
                    CERTIFICATION (the immune system)
   manifest change ──> benchmark ──> attestation ──> promotion gate
                                        │                    │
                                   transparency log     refuse regressions
                                        │                    │
                                   drift detector ──> quarantine ──> reinstatement

   EVENT SOURCES                CONTROL PLANE                     RUNTIMES
   webhooks  ──┐           ┌─────────────────────┐           ┌──────────────┐
   alerts    ──┤ triggers  │ registry · policy   │ adapters  │ raw worker   │
   cron      ──┼──────────>│ budget · sandbox    │──────────>│ LangGraph    │
   PRs       ──┤           │ scheduling · guards │           │ PydanticAI   │
   API/SDK   ──┘           │ secrets · tenancy   │           │ OpenAI Agents│
                           └─────────┬───────────┘           └──────┬───────┘
                                     │ state · usage · tool calls   │
                           ┌─────────▼───────────┐                  │
                           │ Postgres · audit    │<─────────────────┘
                           │ OTel · cost · UI    │
                           └─────────────────────┘
```

A request can enter from a manual CLI/SDK call, an event source (trigger), a GitOps
reconcile, another agent (agent-as-tool), or a service endpoint. No matter the door, it
walks the **same admission gauntlet**: tenant scope → incident/freeze → certification and
provenance → model-identity binding → budget → policy → sandbox. That single choke point
is what makes the fleet operable.

---

## 2. The admission gauntlet

Order matters: cheap, correctness-critical gates run before expensive work.

| Step | Question | Subsystem |
|------|----------|-----------|
| 1 | Is there an incident halt, freeze, or maintenance window? | `hiveplane.incident`, `hiveplane.triggers`, `hiveplane.scheduler` |
| 2 | Is the caller scoped to this tenant, with the right role? | `hiveplane.auth`, `hiveplane.tenancy` |
| 3 | Does the workload hold a valid, unexpired certification for *this exact artifact*? | `hiveplane.certification` |
| 4 | Does the agent bundle's signature/digest verify? | `hiveplane.transparency` |
| 5 | Does the runtime model match the attested model identity? | adapters + `hiveplane.certification` |
| 6 | Is there budget headroom (run/day/team/tenant + spend cap)? | `hiveplane.budget`, `hiveplane.cost` |
| 7 | Does policy allow this action, tool, and context? | `hiveplane.policy` |
| 8 | Is a sandbox required, and is it provisionable? | `hiveplane.execution` |

Refusals are explicit (`403`) and name the failing step; illegal transitions are `409`;
unknown records are `404`. Cross-tenant access looks like "not found" on reads and raises
`TenantScopeError` on writes — isolation is enforced in the store layer, not just the API.

---

## 3. The pillars, and where they live

### Foundation & reconciliation — `hiveplane.reconcile`
Git is the source of desired state. A reconciler loads a fleet manifest (workloads,
policies, triggers, budgets), diffs it against actual state, classifies actions as
additive/soft/destructive, and applies them **only through the registry service** so it can
never bypass certification, policy, or budget. Single-writer safety uses a per-source lock
plus a Postgres advisory lock, and leader election (`hiveplane.ha`) keeps a second replica
passive. See [reconciliation-design.md](design/reconciliation-design.md).

### Autonomy — `hiveplane.triggers`, `hiveplane.pipelines`, `hiveplane.router`, `hiveplane.agent_tools`
Events (webhook, Alertmanager, GitHub, cron) are HMAC-verified with a replay window, then
deduped, cooled down, rate-limited, and rendered through a value-only template language
into an admitted run. Pipelines execute a validated DAG (`workload`/`fan_out`/`fan_in`/
`gate`/`transform`) with per-step gates, budgets, and handoff schemas. The router sends a
task to the best *certified* workload without guessing, and agent-as-tool exposes certified
workloads as nested tools with depth/cycle/budget guards.

### The immune system — `hiveplane.certification`, `hiveplane.drift`
Certification binds an **artifact hash** over the behavior-affecting spec, toolset, model
identity, and policy version. Change any of them and the hash changes, so the workload is
automatically marked `uncertified`. The promotion gate refuses until a fresh passing
attestation exists; the drift detector compares re-evaluations to the agent's own baseline
and auto-quarantines on a strong signal or N consecutive failures. Reinstatement requires a
fresh passing certification. See [certification-v2-design.md](design/certification-v2-design.md)
and the [learning loop](design/learning-loop-design.md).

### Progressive delivery — `hiveplane.progressive`
Shadow runs execute a candidate on the same task without delivering and without side
effects; canary routes a deterministic percentage to a candidate within a blast-radius cap
and auto-promotes on a clean window or auto-aborts on regression. Model experiments route
across ≥2 configurations and pick the best-scoring arm with recorded rationale.

### Defense — `hiveplane.defense`, `hiveplane.policy`
Every tool output is scanned deterministically (no model calls) for injection; taint marks
propagate from untrusted tool/trigger output and block destructive calls while live; egress
is deny-by-default, port-aware, and always blocks cloud metadata. The policy engine is
context-aware (budget, taint, time), supports what-if dry-runs, versioned team packs, and a
fleet-wide tool kill switch. See [defense-policy-v2-design.md](design/defense-policy-v2-design.md)
and [policy-engine-design.md](design/policy-engine-design.md).

### Runtime guards & health — `hiveplane.guards`, `hiveplane.health`, `hiveplane.probes`
Four budgets (run/day/team + context-window tokens) plus spend-velocity and circuit-breaker
guards pause runs cleanly with accounting. Agent health rolls readiness, MTTR, drift,
quality, and breaker state into SLO/error-budget signals; burn-through triggers
auto-quarantine or throttle. Synthetic probes raise an early drift warning before the
detector trips. See [agent-health-design.md](design/agent-health-design.md).

### Scale — `hiveplane.worker`, `hiveplane.scheduler`, `hiveplane.ha`, `hiveplane.chaos`
Workers enroll with signed tokens, take time-boxed leases with fencing tokens, and are
reclaimed on missed heartbeats or expired leases. The scheduler is a priority queue with
QoS classes (guaranteed/burstable/best_effort), concurrency caps, and checkpoint-safe
preemption. Chaos drills (kill worker, revoke cert, exhaust budget, inject tool failure)
run through a guardrailed catalog and emit pass/fail reports.

### Cost & explanation — `hiveplane.cost`, `hiveplane.reporting`, `hiveplane.artifacts`
Every usage event is attributed tenant → team → workload (unattributed events fail loudly).
Budget periods, threshold alerts, showback, cost-per-completed-task, ROI with
`expensive_low_value` flags, attestation-bound result caching with savings, weekly digests,
audit export with integrity proofs, compliance evidence packs, retention/PII scrubbing, and
signed tenant purge. See [cost-service-design.md](design/cost-service-design.md) and
[platform-api-design.md](design/platform-api-design.md).

### Interface — `hiveplane.api`, `hiveplane.sdk`, `hiveplane.cli`, operator UI, `hiveplane.ask`
REST API (with a versioned `/v2` surface, `X-Request-ID` correlation, cursor pagination,
and a structured error envelope), a typed Python SDK, a broad CLI, a server-rendered
operator UI with SSE live runs, and `ask` — a read-only natural-language copilot over live
state that is itself under budget and certification.

---

## 4. The adapter boundary (the one pluggable seam)

Adapters are the only place a runtime framework is allowed to touch the system. Contract v2
makes the boundary explicit and versioned — `AdapterCapabilities`, `AdapterEvent`,
`CONTRACT_VERSION`, and an ordered event stream — and every adapter reports the **model
identity from actual inference**, never a configured string. That captured identity is what
the model-swap defense compares against the attestation. An AST import-boundary test fails
the build if a framework or provider SDK leaks into core (DD-02). See
[ADAPTERS.md](ADAPTERS.md) and [runtime-adapter-v2-design.md](design/runtime-adapter-v2-design.md).

---

## 5. State, audit, and telemetry

PostgreSQL is the system of record (Alembic migrations `0001`–`0036`), with a durable JSON
store for local use. A tamper-evident, hash-chained audit log records every privileged
action; it can be exported with a Merkle-root-plus-chain-head proof and pruned via a
persisted anchor without breaking `verify()`. OTel-native traces, Prometheus metrics
(including the plane's own metrics), structured logs, and Grafana dashboards ship in the
stack. See [state-store-design.md](design/state-store-design.md),
[telemetry-design.md](design/telemetry-design.md), and [observability.md](observability.md).

---

## 6. Where to go next

- **Hands-on:** [Tutorials](tutorials/) — start with [Getting Started](tutorials/01-getting-started.md).
- **Operating it:** [Operator Runbook](runbooks/operator-runbook.md).
- **Every feature and command:** [User Guide](USER_GUIDE.md).
- **Design rationale:** [Design docs index](design/README.md) and [Design Decisions](design/design-decisions.md).
- **Deploying:** [k3d reference deployment](runbooks/k3d-reference-deploy.md).
- **Why the product exists:** [PRD 01: Why](prd/01-why.md).
