# Execution Path Design

> Status: approved for implementation

## Purpose

This is the implementation blueprint for the **execution path**: accepting runs,
gating them, moving them through one durable state machine, enforcing policy and
budget, isolating them in a sandbox, shaping tool output, and executing them
through a pluggable runtime adapter.

It turns the conceptual designs (D2 run lifecycle, D4 policy, D5 budget, D6
runtime adapter, D11 execution sandbox) into concrete modules, interfaces, and
build order. It is scoped to the control-plane execution path; the benchmark /
certification pipeline, telemetry export, operator UI, and trigger ingestion are
deliberately out of scope (see [Deferred](#deferred)).

## Confirmed decisions

1. **Run durability now, Postgres later.** The run lifecycle is written against a
   `RunStore` protocol with an in-memory implementation plus a JSON-file-backed
   implementation so a run survives a control-plane restart. The PostgreSQL
   implementation swaps in later without touching the service layer. This
   mirrors how `RegistryStore` already works.
2. **Adapters are dependency-inverted seams.** The lifecycle depends on narrow
   protocols it defines; concrete implementations arrive in the adapter and
   sandbox workstreams. Test doubles are never production code — the conformance
   suite proves the real implementations satisfy the same seams.
3. **Process-level sandbox for v0.1.0.** Isolation uses a subprocess with POSIX
   resource limits and a wall-clock watchdog, plus an egress guard at the
   tool-call boundary. A container backend (Docker/gVisor) is a documented
   follow-on; the backend is behind an interface.
4. **LangGraph is an optional extra.** The LangGraph adapter imports `langgraph`
   lazily; its tests skip when the dependency is absent. It is not a core
   dependency.
5. **`langgraph` and Docker are not prerequisites** for the control plane to run,
   import, or pass its test suite.

## Architecture

The execution path is a set of services over injected protocol seams. Dependency
direction points inward: the lifecycle defines the seams it needs, and the
policy, budget, sandbox, and adapter workstreams implement them.

```
                       api/ (FastAPI routers)
                              │
              ┌───────────────┼────────────────┬───────────────┐
              ▼               ▼                ▼               ▼
        RunService      ApprovalService   PolicyEngine    BudgetService
              │               │                │               │
              │   ┌───────────┴──── run gates ─┴───────────┐   │
              ▼   ▼                                        ▼   ▼
        ┌──────────────── AdmissionPipeline ────────────────────┐
        │  CertificationGate → ModelBinding → BudgetGate →       │
        │  PolicyGate → (sandbox decision)                       │
        └───────────────────────┬───────────────────────────────┘
                                ▼
                          RunStore + Run
                                │
                                ▼
                        RunExecutor (seam)
                                │
                     ┌──────────┴──────────┐
                     ▼                     ▼
              SandboxManager        ShapingPipeline
              (isolation)           (filter/truncate/
                                     budget/injection)
```

### Seams (protocols)

| Seam | Defined in | Implemented by |
|------|-----------|----------------|
| `RunStore` | `execution/store.py` | in-memory, JSON-file, later Postgres |
| `CertificationGate` | `execution/gates.py` | wraps `RegistryService.check_admission` |
| `PolicyGate` | `execution/gates.py` | `policy.engine.PolicyEngine` |
| `BudgetGate` | `execution/gates.py` | `budget.service.BudgetService` |
| `SandboxGate` | `execution/gates.py` | `sandbox.manager` (required + provisioning) |
| `RunExecutor` | `execution/gates.py` | `adapters.base.Adapter` implementations |
| `DeliveryTransport` | `execution/fanout.py` | Slack webhook, generic webhook |

## Package layout

```
src/hiveplane/
  execution/            # Run lifecycle: the core of this document
    __init__.py
    models.py           # Run extensions, AdmissionResult, DeliveryRecord
    errors.py           # RunNotFound, IllegalTransition, Admission*, ...
    store.py            # RunStore protocol, InMemoryRunStore, JsonFileRunStore
    gates.py            # Certification/Policy/Budget/Sandbox/RunExecutor protocols
    admission.py        # AdmissionPipeline
    service.py          # RunService: submit, transition, intervene, usage
    fanout.py           # FanOutService + DeliveryTransport implementations
  policy/               # Policy engine, packs, approvals
    models.py           # ApprovalRecord, ApprovalDecision
    engine.py           # PolicyEngine (deny-by-default, explainable)
    packs.py            # PolicyPack model, store, tighten-only merge
    approvals.py        # ApprovalService
  budget/               # Cost table and budget enforcement
    pricing.py          # CostTable, per-model pricing, UnknownModelPrice
    models.py           # BudgetSnapshot, CostAttribution
    service.py          # BudgetService
  sandbox/              # Execution isolation
    models.py           # SandboxInstance, SandboxStatus
    manager.py          # SandboxManager protocol, ProcessSandbox, FakeSandbox
    egress.py           # EgressGuard
  shaping/              # Tool-output shaping and injection scanning
    pipeline.py         # ShapingPipeline
    injection.py        # InjectionScanner, InjectionVerdict
  adapters/             # Runtime adapters
    base.py             # Adapter protocol, AdapterContext, ToolCall, outputs
    registry.py         # AdapterRegistry keyed by core.spec.RuntimeAdapter
    raw_worker.py       # Reference adapter
    langgraph_adapter.py# Optional example adapter (lazy import)
    conformance.py      # Reusable conformance checks
  api/
    runs.py             # /runs routers
    approvals.py        # /approvals routers
    policy.py           # /policy and /policy-packs routers
```

Test layout mirrors the packages: `tests/execution/`, `tests/policy/`,
`tests/budget/`, `tests/sandbox/`, `tests/shaping/`, `tests/adapters/`, and
`tests/conformance/`.

## Run lifecycle

### Data model

Extend the existing `core/run.py::Run` aggregate rather than introducing a second
run representation. Add optional execution fields (all defaulted, so existing
constructors and tests keep working):

- `manifest_version: int | None`
- `context: AdmissionContext | None`
- `sandbox: bool = False`
- `task: dict[str, JsonValue]`
- `result: JsonValue | None`
- `failure_reason: str | None`
- `cost_usd: float = 0.0`

`core/run.py` keeps `RunState` and `can_transition`. `AdmissionContext` moves to
`core/run.py` (it is used by both the registry and execution) and is re-exported
from `registry.models` so existing imports and tests are unaffected. Moving it
avoids a `core → registry` import cycle.

New models in `execution/models.py`:

- `AdmissionOutcome` (`admitted`, `sandbox_only`, `refused`).
- `AdmissionCheck` (`step`, `passed`, `reason`, `rule`).
- `AdmissionResult` (`run_id`, `context`, `outcome`, `checks`, `decision`).
- `InterventionAction` (`pause`, `resume`, `stop`).
- `DeliveryStatus` (`pending`, `delivered`, `failed`).
- `DeliveryRecord` (`run_id`, `destination`, `status`, `attempts`, `error`,
  `timestamp`).
- `RunContext` (the run plus its workload manifest and sandbox flag, handed to a
  `RunExecutor`).
- `RunSubmission` (the `POST /runs` request body: `workload`, `task`, `caller`,
  `context`, `model_identity`).

### Admission pipeline

`AdmissionPipeline.check(workload, context, task)` runs checks in order and
returns an `AdmissionResult`; it never raises for a business refusal, so callers
can render a precise reason. `RunService.submit` raises `AdmissionRefusedError`
carrying the `AdmissionResult`.

1. `CertificationGate.require_admission(workload, context)` — the existing
   registry admission. `sandbox` admits everything; `staging` requires
   provisional/certified; `production` requires a live signed attestation and no
   pending re-certification.
2. **Model-identity binding** — when certified, the task's reported model
   identity is compared to the attestation's; mismatch → `refused: model_swap`.
3. `BudgetGate.check(...)` — per-run/day/team headroom; exhausted → `refused:
   budget` (or `sandbox_only` when the context permits).
4. `PolicyGate.evaluate(admission_context)` — context-aware policy; `deny` →
   `refused: policy`; `escalate` → the run is created paused pending approval.
5. **Sandbox decision** — `SandboxGate.required(...)` is true when the workload
   enables a sandbox, the context is `sandbox`, or the action class is
   destructive; the run is created with `sandbox=True`.

Outcomes: `admitted` → `RunState.QUEUED`; `sandbox_only` → queued with
`sandbox=True`; refused → `AdmissionRefusedError`.

### State machine

Reuse `can_transition`. `RunService.transition(run_id, target, actor, detail)`:

- rejects illegal transitions with `IllegalTransitionError`,
- persists the run **before** side effects are acknowledged,
- appends an attributed `RunEvent` (sequence, actor, from/to, detail),
- starts fan-out on `completed` / `failed`, and on escalation pause.

Pause is cooperative: the service requests pause from the `RunExecutor` and only
records `PAUSED` when the executor confirms honest state. Stop (`cancel`) is
immediate: the executor is cancelled, the sandbox is torn down, and the run moves
to `CANCELLED`.

### RunStore

```python
class RunStore(Protocol):
    def save_run(self, run: Run) -> None: ...
    def get_run(self, run_id: str) -> Run | None: ...
    def list_runs(self, *, workload: str | None = None,
                  state: RunState | None = None) -> list[Run]: ...
    def add_event(self, event: RunEvent) -> None: ...
    def list_events(self, run_id: str) -> list[RunEvent]: ...
    def add_usage(self, report: UsageReport) -> None: ...
    def list_usage(self, run_id: str) -> list[UsageReport]: ...
    def save_admission(self, result: AdmissionResult) -> None: ...
    def get_admission(self, run_id: str) -> AdmissionResult | None: ...
    def add_delivery(self, record: DeliveryRecord) -> None: ...
    def list_deliveries(self, run_id: str) -> list[DeliveryRecord]: ...
```

- `InMemoryRunStore` copies on read/write (same discipline as
  `InMemoryRegistryStore`).
- `JsonFileRunStore` persists each record family under a base directory and
  reconstructs on construction. A test constructs a service, transitions a
  paused run, builds a **new** service over the same directory, and asserts the
  paused run and its event log survive — this is the durability acceptance.

### Fan-out

`FanOutService.notify(run)` reads `spec.fan_out`, composes a message with the run
result, trace link, and (when present) attestation link, then attempts delivery
through `DeliveryTransport` with `settings.fanout.max_retries` and
`settings.fanout.timeout_s`.

- `SlackTransport` posts to `settings.fanout.slack_webhook_url`.
- `WebhookTransport` posts JSON to `settings.fanout.generic_webhook_url` or the
  destination URL.
- Delivery status is recorded and never blocks the terminal transition; failed
  deliveries are logged with their error.

## Policy and approvals

### Policy engine

- `core/decision.py` gains `DataSensitivity` (`public`, `internal`, `pii`,
  `restricted`), `PolicyContext`, and `BlastRadius` (factor contributions and a
  0–100 score), so core does not depend on the policy package.
- Extend `core/decision.py::DecisionOutcome` with `BLOCK_INJECTION` (the design
  already specifies it) and extend `PolicyDecision` with `blast_radius:
  BlastRadius | None` and `certification_status`.

`PolicyEngine.evaluate(context)` implements the design's evaluation order:
certification → injection → explicit deny → explicit allow → trust level →
blast radius → action-class approval → default deny. Every decision returns the
originating rule, a human-readable reason, and the blast-radius score. Deny wins
over allow at every level.

### Policy packs

`PolicyPack` mirrors the design's YAML. `packs.py` provides a store and a
tighten-only merge: pack rules may deny or require approval, never loosen a
manifest deny. Resolution order: system defaults → team pack → manifest.

### Approvals

`ApprovalService.escalate(decision, evidence)` creates a pending
`ApprovalRecord` and pauses the run. `approve(id, operator)` resumes the run and
records the actor; `deny(id, operator, reason)` fails the run with the reason
recorded. Escalation fires an optional Slack notice and does not block.

## Budget enforcement

- `core/usage.py` gains `BudgetCheck` and `BudgetLevel`, so the budget seam has
  a return type without core depending on the budget package.
- `pricing.py`: `CostTable` maps `provider/family/version` to input/output
  per-1k-token prices. An unknown model raises `UnknownModelPriceError` (fails
  loudly, per the milestone acceptance).
- `BudgetService.check(workload, context)` reports per-run/day/team headroom.
- `BudgetService.record_usage(report)` prices the usage, decrements run/day/team
  state, and returns a `BudgetCheck`; over-budget yields `escalate` or `fail` per
  workload policy.
- Day/team aggregates live in a `BudgetStore` protocol (in-memory + JSON-file),
  keyed by day and team.
- `CostAttribution` records are written per usage event for showback.
- Burn metrics are emitted through a counter hook (a no-op default until the
  telemetry effort wires an exporter).

## Execution sandbox

- `SandboxStatus` (`provisioning`, `ready`, `running`, `completed`, `failed`,
  `cancelled`, `destroyed`) and `SandboxInstance` records per the design.
- `SandboxManager` protocol: `provision(config, run_id) -> SandboxInstance`,
  `destroy(sandbox_id)`, `status(sandbox_id)`, `reap()`.
- `ProcessSandbox` runs the workload in a subprocess with POSIX resource limits
  (`RLIMIT_AS`, `RLIMIT_CPU`), a wall-clock watchdog, an ephemeral working
  directory, and a captured stdout/exit result. On cap/timeout it kills the
  process and reports the reason.
- `EgressGuard` enforces the manifest egress allowlist at the tool-call boundary
  (cloud metadata endpoints always denied). Real network-namespace enforcement is
  deferred to the container backend; the guard is the enforcement point adapters
  use regardless of backend.
- `FakeSandbox` is deterministic: it records provision/destroy calls and can
  simulate cap violations, timeouts, and teardown for tests.
- Lifecycle guarantee: terminal transitions always destroy the sandbox; `reap()`
  force-destroys stale instances.

## Tool-output shaping and injection scanning

`ShapingPipeline.apply(text, spec, budget_state) -> ShapedOutput` runs, in order:

1. **Filter** — apply `spec.filter_rules` (redact/mask via regex).
2. **Truncate** — if over `spec.max_bytes`, apply `truncate_strategy`
   (`head`/`tail`/`summary`).
3. **Budget** — track cumulative output bytes for the run; past budget, truncate
   aggressively.
4. **Injection scan** — when `spec.injection_scan`, scan the shaped text.

`ShapedOutput` reports `text`, `truncated`, `redactions`, `original_bytes`,
`shaped_bytes`, and an optional `InjectionVerdict`.

`InjectionScanner` implements the design's pattern categories. High-confidence
patterns return `BLOCK_INJECTION` (output never reaches the agent); lower-
confidence patterns return `ESCALATE`. A corpus test asserts no false positives
on the demo fixtures.

## Runtime adapters

`adapters/base.py` defines the protocol the design describes:

```
register(manifest) -> handle
submit(task, run_id, context) -> void
pause(run_id) / resume(run_id, modified_context?) / cancel(run_id)
status(run_id) -> RunState
usage(run_id) -> UsageReport
tool_calls(run_id) -> list[ToolCall]
sandbox_config(run_id) -> SandboxSpec
shaped_output(tool_call_id) -> ShapedOutput
verify_model_identity(run_id, attestation_model) -> ModelIdentityMatch
```

The protocol is named `Adapter` to avoid colliding with the `RuntimeAdapter`
enum in `core/spec.py`.

- `RawWorkerAdapter` executes the manifest `runtime.entrypoint`
  (`module:callable`), routing every tool call through the injected policy gate
  and shaping pipeline, and reporting usage.
- `LangGraphAdapter` wraps a compiled graph behind the same protocol with a lazy
  `langgraph` import and an optional dependency extra.
- `AdapterRegistry` maps `core.spec.RuntimeAdapter` to a factory so the lifecycle
  can resolve an executor from the manifest.
- `conformance.py` holds reusable assertions; `tests/conformance/` runs them
  across adapters and sandbox backends: lifecycle, sandbox caps/egress/teardown,
  shaping truncate/redact/budget, injection block/escalate, model-identity, and
  policy routing.

## API surface

New routers, wired in `create_app` with constructor injection so tests can supply
in-memory services:

- `POST /runs` — submit; returns the run or a precise refusal.
- `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/events`, `GET /runs/{id}/usage`.
- `POST /runs/{id}/pause`, `/resume`, `/stop`.
- `GET /approvals`, `GET /approvals/{id}`, `POST /approvals/{id}/approve`,
  `POST /approvals/{id}/deny`.
- `POST /policy/evaluate` (explainability/debug), `GET /policy-packs`,
  `POST /policy-packs`.

### Errors and status codes

| Error | Status |
|-------|--------|
| `RunNotFoundError` | 404 |
| `IllegalTransitionError` | 409 |
| `RunAdmissionRefusedError` | 403 |
| `ApprovalNotFoundError` | 404 |
| `BudgetExhaustedError` | 402 |
| `UnknownModelPriceError` | 422 |

## Testing strategy

- **Unit** — each module against in-memory doubles and a fixed `Clock` injected
  everywhere (no wall-clock reads in logic).
- **Integration** — FastAPI `TestClient` through submit → admit → run (fake
  executor) → complete → fan-out (fake transport), asserting the event log.
- **Durability** — `JsonFileRunStore` round-trips a paused run across service
  reconstruction.
- **Conformance** — the adapter suite is green for `RawWorkerAdapter` and, when
  `langgraph` is installed, `LangGraphAdapter`.
- **Regression** — a malformed/unknown model price fails loudly; an over-budget
  run is blocked; a seeded injection is blocked or escalated; a large payload is
  truncated before reaching the agent.

## Build order

Each phase ends at a milestone exit gate: all tests pass, coverage > 95%, ruff
clean, mypy strict clean, docs updated, changes committed. Phases are ordered so
each one only depends on seams already defined.

1. **Run lifecycle core** — models, errors, stores, gates, admission, service,
   fan-out, APIs. Gates are exercised with test doubles.
2. **Policy and approvals** — `PolicyEngine`, packs, `ApprovalService`; wire the
   real `PolicyGate` into admission and add approval APIs.
3. **Budget** — cost table and `BudgetService`; wire the real `BudgetGate`.
4. **Sandbox and shaping** — `SandboxManager`/`ProcessSandbox`, `EgressGuard`,
   `ShapingPipeline`, `InjectionScanner`; wire the real `SandboxGate`.
5. **Adapters and conformance** — `Adapter` protocol, raw-worker, optional
   LangGraph adapter, adapter registry, and the conformance suite.

## Deferred

- **PostgreSQL wiring for all stores** — `RunStore`/`BudgetStore`/registry/certification/approval
  stores must use Postgres when configured; today `create_app` hardcodes in-memory stores for
  everything except the run store (#118, #126, #128). The JSON-file store remains the
  local/offline default.
- **Auto-migration** — schema migrations must run on startup so a fresh stack works on first
  boot (#118).
- **Container sandbox backend** — Docker/gVisor with real network namespaces. v0.1.0 targets a
  process-level backend (POSIX rlimits + wall-clock watchdog); that is not yet wired into the
  adapter path (#110).
- **LLM provider seam + agent contract seams** — no provider exists today; `WorkerContext` has no
  model-invocation method and the tool boundary does not execute tools (#104, #107, #115, #116).
  Required before any execution-path item can be exercised end-to-end.
- **Durable resume** — pause/resume across process restarts; current pause/resume is in-memory
  only (#106, #111).
- **Approval re-dispatch** — implemented (#129): on approve + resume the escalated call is
  re-dispatched through the boundary (raw-worker re-drives the entrypoint; LangGraph re-drives
  the graph) and the run completes.
- **Trigger ingestion / auto-start** — `trigger_origin` is accepted and persisted,
  but webhook/alert/PR/cron ingestion and dedup are a separate effort.
- **Certification benchmark execution** — the gate, engine, and attestations ship in v0.1.0, but
  the benchmark does not yet execute the real agent (the `ReferenceExecutor` is a stand-in) —
  #105, #117, #109.
- **Telemetry exporters, CLI, operator UI, agent health** — separate efforts.

## See Also

- [Run lifecycle design](run-lifecycle-design.md) (D2)
- [Policy engine design](policy-engine-design.md) (D4)
- [Budget enforcement design](budget-enforcement-design.md) (D5)
- [Runtime adapter design](runtime-adapter-design.md) (D6)
- [State store design](state-store-design.md) (D7)
- [Execution sandbox design](execution-sandbox-design.md) (D11)
- [Result fan-out design](result-fanout-design.md) (D15)
