# D25: Runtime Adapter v2 Design

> Status: implemented (M31)

**Milestones:** M31 · **Extends:** D6

## Problem

v0.1.0 ships two adapters (raw-worker, LangGraph) against an informal contract. To onboard real user apps — PydanticAI, OpenAI Agents SDK, CrewAI — the boundary needs a versioned, documented contract that covers streaming, cancellation, tool calls, usage, and model identity uniformly, a conformance suite every adapter must pass, and a conversion path for apps that were never written for HivePlane. Without this, each adapter drifts and framework types leak into the core (DD-02).

See [PRD 05: Features](../prd/05-features.md) § Tools & MCP and § LLM Provider & Agent Contract, and [PRD 09: Roadmap](../prd/09-roadmap.md) pillar R.

## Overview

```
  user app (LangGraph / CrewAI / PydanticAI / OpenAI SDK)
        │
        │  hiveplane wrap  ── generates, never edits source
        ▼
  workload.yaml + adapter_scaffold.py  (inert until registered)
        │
        │  register + certify
        ▼
 ┌───────────────────────────────────────────────┐
 │ Core control plane (no framework imports)      │
 │  DTOs · run lifecycle · policy · budget        │
 └───────────────┬───────────────────────────────┘
                 │ Adapter Contract v2
                 ▼
 ┌───────────────────────────────────────────────┐
 │ raw-worker · langgraph · pydanticai · openai   │
 │ (framework types converted at this boundary)   │
 └───────────────────────────────────────────────┘
                 │ all adapters pass Conformance Suite v2
```

## Design

### Adapter Contract v2

The v2 contract is explicit, versioned (`adapter_contract: 2`), and documented. It extends D6 with first-class streaming, explicit cancellation, normalized usage, and adapter-reported model identity.

```
capabilities() -> AdapterCapabilities          # negotiated at registration
register(manifest) -> AdapterHandle
submit(task, run_id, context) -> void
stream(run_id) -> Iterator[AdapterEvent]        # model_delta | tool_call |
                                                # tool_result | checkpoint | usage | state
pause(run_id) -> void
resume(run_id, modified_context?) -> void
cancel(run_id, deadline_s) -> void              # cooperative checkpoint, then hard deadline
status(run_id) -> RunState
tool_calls(run_id) -> list[ToolCall]
usage(run_id) -> UsageReport
model_identity(run_id) -> ModelIdentity         # from actual inference, not self-report
conformance_version() -> str
```

| Area | v2 requirement |
|------|----------------|
| Lifecycle | register/submit/pause/resume/cancel/status; honest state, durably persisted (D18) |
| Streaming | `stream()` yields ordered `AdapterEvent`s; consumers tolerate adapters that buffer |
| Tool calls | every call routes through the policy boundary; adapter executes and returns real data (D6) |
| Cancellation | cooperative at checkpoints, escalating to a hard deadline; reports terminal state |
| Usage | normalized `UsageReport` (input/output tokens, cost, tool calls) from provider responses |
| Model identity | reported by the adapter from inference and bound to the attestation (DD-10, T11) |
| Versioning | contract version in the manifest (`spec.runtime.adapter_contract`) and in `capabilities()` |

`AdapterCapabilities` declares `streaming`, `pause_resume`, `state_edit`, `tool_execution`, `sandbox`, `deterministic_replay`, and `max_agent_depth`. Admission refuses a manifest that requires a capability the bound adapter does not advertise.

### Reference Adapters

| Adapter | Framework | Notes |
|---------|-----------|-------|
| `raw-worker` | none | Reference implementation; unchanged behavior, contract bumped to v2. |
| `langgraph` | LangGraph | Compiled graph behind the contract; durable checkpointer replaces `InMemorySaver` (D18). |
| `pydanticai` | PydanticAI | Wraps `pydantic_ai.Agent`; a custom model routes inference through `ctx.complete()` (D17). Certifiable via the benchmark. |
| `openai-agents` or `crewai` | OpenAI Agents SDK / CrewAI | **At least one ships in M31.** Maps the framework agent/tool loop onto `ctx.complete()` and `ctx.tool_call()`. |

Framework types are converted to HivePlane DTOs inside the adapter; only DTOs cross into core.

### Conformance Suite v2

One shared, parameterized pytest suite runs against every adapter factory in CI. A v2 adapter must pass all of it:

- lifecycle: register → submit → stream → pause → resume → cancel → status;
- streaming event ordering and completion, including adapters that buffer;
- cancellation at a checkpoint and enforcement of the hard deadline;
- usage normalization: values come from provider responses, never agent-supplied;
- model identity captured from inference; mismatch blocks the run (T11);
- tool calls execute and return real data and route through policy;
- sandbox provisioning, resource caps, egress restriction, and teardown;
- durable resume after a control-plane restart;
- deterministic replay against the fake provider;
- import-boundary check (see below).

Each adapter reports `conformance_version() == "2"`; a missing or failing suite blocks release.

### `hiveplane wrap`

`hiveplane wrap <path> [--framework auto|langgraph|crewai|openai] [--out <dir>] [--dry-run]` converts an existing app into an onboardable workload. It inspects the app with an AST scan — it **never imports or executes it**, and **never writes to the source tree**.

1. Detect the framework from imports; locate agents, graphs, tools, and the entrypoint.
2. Emit into `--out` (a new directory): `workload.yaml` (manifest draft, certification `uncertified`), `adapter_scaffold.py` (implements contract v2, imports the app as a library), `corpus.template.yaml`, and a link to the "bring your own agent" guide.
3. The scaffold is **inert**: `wrap` does not register, certify, or run anything. Registration is a separate explicit step; production admission still requires certification.

`--dry-run` prints the detection and generation plan without writing. `wrap` is a scaffolder, not a code rewriter — that is the contract.

### Import-Boundary Enforcement

Core packages (`hiveplane.core`, `hiveplane.run`, `hiveplane.policy`, `hiveplane.budget`, …) must not import framework or provider SDKs. An automated boundary test AST-scans core modules and fails on forbidden imports (`langgraph`, `pydantic_ai`, `agents`, `crewai`, `openai`, …). Adapters may import frameworks; core imports only `hiveplane.adapters.base` DTOs and the protocol. This keeps HivePlane from becoming a framework wrapper (DD-02).

## Data Model

| Table / field | Contents |
|---------------|----------|
| `runs.adapter_contract` | Contract version the run was admitted under |
| `runs.adapter_name` | Bound adapter identity |
| `adapter_capabilities` (registry) | Adapter name, version, capability flags, conformance version |
| `wrap_jobs` | Source path, detected framework, generated output dir, dry-run flag, timestamp |

Usage, model identity, tool calls, and sandbox config continue to use the D6/D7 entities.

## Interfaces / API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/adapters` | List adapters, contract version, capabilities, conformance status |
| GET | `/adapters/{name}` | Adapter detail and conformance report |
| POST | `/wrap` | Server-side wrap job (equivalent to the CLI) |

CLI: `hiveplane wrap <path> [--framework] [--out] [--dry-run]`, `hiveplane adapters list`.

## Failure Modes

| Failure | Detection | Response |
|---------|-----------|----------|
| Adapter lacks required capability | Registration/admission | Refuse run with `adapter_capability_missing` |
| Conformance suite failure | CI | Adapter not shippable |
| Model identity mismatch | Inference report vs. attestation | Block run `refused: model_swap` (T11) |
| Streaming adapter stalls | Heartbeat/timeout | Cancel at deadline; surface terminal state |
| `wrap` cannot detect framework | AST scan | Report no-match; write nothing; exit non-zero |
| Framework type leaks into core | Import-boundary test | CI failure; block merge |

## Security

- Adapters execute tool calls through the policy boundary and never supply fabricated outputs (D6).
- Model identity is captured from actual inference, not self-reported, preventing model-swap (T11).
- `wrap` performs static inspection only; it never executes untrusted app code and never mutates sources.
- Generated scaffolds are inert and uncertified, so they cannot reach production until registered, certified, and admitted.

## Testing

Conformance Suite v2 for every shipped adapter in CI; a PydanticAI agent registered, certified, and run end-to-end; wrap round-trip on sample LangGraph/CrewAI/OpenAI-SDK apps (manifest + scaffold generated, source tree unchanged); an uncertified wrapped app refused production admission; model identity reported by the adapter and verified against the attestation; import-boundary test green.

## Open Questions

- Whether streaming events should be part of the persisted run story or telemetry only.
- How to negotiate capability differences across adapter versions without a hard break.
- CrewAI vs. OpenAI Agents SDK for the fourth runtime (one must ship; which is the better long-term bet).
- Whether `wrap` should infer a starter corpus from the app's tests.
- Handling frameworks that own their event loop and cannot be paused cooperatively.

## Implementation (M31)

| Module | Responsibility |
|--------|----------------|
| `hiveplane.adapters.base` | Contract v2: `AdapterCapabilities`, `AdapterEvent`, `CONTRACT_VERSION`, and the extended `Adapter` protocol; `*_of` helpers tolerate pre-v2 adapters |
| `hiveplane.adapters.raw_worker` / `langgraph` | v2 methods on the reference adapters; inference-captured model identity via `WorkerContext` |
| `hiveplane.adapters.pydanticai` | Wraps `pydantic_ai.Agent`; a governed `Model` routes inference through `WorkerContext.complete` |
| `hiveplane.adapters.openai_agents` | Wraps the OpenAI Agents SDK `Agent`; a governed `Model` routes inference through the same seam |
| `hiveplane.wrap` | AST-only detection + manifest/scaffold generation (`detect`, `scaffold`, `job`) |
| `hiveplane.api.adapters` | `GET /adapters`, `GET /adapters/{name}` — contract version and capabilities |

**Contract v2.** Every shipped adapter reports `capabilities()` (with
`contract_version: "2"`), an ordered `stream()`, the `model_identity()` captured
from actual inference, and `conformance_version() == "2"`. `spec.runtime.adapter_contract`
records the expected contract; `adapter_contract: 2` is the default.

**Conformance Suite v2.** `tests/conformance.py` gains
`assert_adapter_conforms_v2`, and every adapter (raw-worker, LangGraph,
PydanticAI, OpenAI Agents) passes it. Pause/resume is a negotiated capability:
adapters that cannot pause mid-flight declare `pause_resume=False` and are not
required to honour the hold scenarios.

**Governed inference.** The PydanticAI and OpenAI Agents adapters install a
custom framework model whose `request`/`get_response` calls
`WorkerContext.complete`, so identity checks, usage pricing, and budget
enforcement all run in the control plane. No framework type crosses into core
(enforced by `tests/test_adapter_boundary.py`).

**`hiveplane wrap`.** `hiveplane wrap <path> [--framework auto|langgraph|pydanticai|openai-agents|crewai] [--out <dir>] [--dry-run] [--force]`
inspects the app with an AST scan (never imports or executes it) and writes
`workload.yaml` (uncertified draft), `adapter_scaffold.py`, `corpus.template.yaml`,
and `README.md` into a new output directory. It never writes to the source tree
and never registers, certifies, or runs anything.

## See Also

- [Runtime adapter design](runtime-adapter-design.md) (D6) — the v0.1.0 contract this extends
- [LLM provider design](llm-provider-design.md) (D17) — the inference seam adapters route through
- [Durable resume design](durable-resume-design.md) (D18) — checkpoint/resume guarantees
- [Certification pipeline design](certification-pipeline-design.md) (D10) — benchmark execution via adapters
- [Orchestration design](orchestration-design.md) (D24) — pipelines consume adapter capabilities
- [PRD 05: Features](../prd/05-features.md) — runtime breadth, tools & MCP
- [WBS v0.2.0 Part 4](../wbs/v0.2.0/wbs-v0.2.0-part4-runtime-promotion.md) — M31
