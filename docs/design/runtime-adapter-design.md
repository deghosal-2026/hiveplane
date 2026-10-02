# D6: Runtime Adapter Design

> Status: draft (v0.1.0 contract implemented for raw-worker + LangGraph; LLM seam, tool
> **v0.2.0:** extended by [Runtime Adapter v2 (D25)](runtime-adapter-v2-design.md).
> execution, and sandbox enforcement are pending — see Implementation Status below)

## Implementation Status (v0.1.0)

The adapter contract below is the target. Current code does **not** satisfy all of it:

| Contract area | Current state | Tracked by |
|---------------|---------------|------------|
| Lifecycle (register/submit/pause/resume/cancel/status) | Implemented (`RawWorkerAdapter`, `LangGraphAdapter`) | — |
| Tool-call routing through policy boundary | Implemented (`ToolGateway`) | — |
| **Tool execution** | **Not implemented** — `ToolGateway.invoke()` shapes a caller-provided `output`; the agent fabricates tool results | #116 |
| **LLM invocation seam** | **Not implemented** — `WorkerContext` has no way to call a model | #115 |
| **Model-identity verification from real inference** | **Not implemented** — identity is self-reported at submission | #115 |
| **Sandbox execution (caps, egress, FS isolation)** | **Not implemented** — the adapter runs the entrypoint on an in-process daemon thread; `InMemorySandboxManager` records bookkeeping only | #110 |
| **Durable resume** | **Not implemented** — in-flight state lives in memory and is lost on restart | #111 |
| Output shaping | Implemented (pipeline runs on tool output) | — |
| Injection scanning | Implemented | — |

The sandbox and model-identity sections below describe the **target** contract, not the
current implementation. Container/cgroup isolation is deferred beyond v0.1.0; v0.1.0 targets
process-level enforcement (POSIX rlimits + wall-clock watchdog), which is not yet wired in.

## Problem

HivePlane must operate heterogeneous runtimes without becoming a wrapper around any one of them (DD-02). After the PRD rewrite, the adapter contract must also include: execution sandbox (isolated context, resource caps, restricted egress), tool-output shaping layer (filter/truncate/budget before reaching agent context), model-identity binding (certification binds to model; model-swap check at runtime), and injection scanning of tool outputs.

See [PRD 02: Architecture](../prd/02-architecture.md) § Runtime Adapters and § Execution Sandbox, and [PRD 05: Features](../prd/05-features.md) § Safe Execution and § Tools & MCP.

## Adapter Interface

```
register(manifest) -> adapter_handle
submit(task, run_id, context) -> void
pause(run_id) -> void
resume(run_id, modified_context?) -> void
cancel(run_id) -> void
status(run_id) -> RunState
usage(run_id) -> UsageReport
tool_calls(run_id) -> [ToolCall]
sandbox_config(run_id) -> SandboxConfig
shaped_output(tool_call_id) -> ShapedOutput
verify_model_identity(run_id, attestation_model) -> MatchResult
```

### Interface Details

| Method | Purpose |
|--------|---------|
| `register(manifest)` | Initialize the adapter with the workload manifest; return a handle |
| `submit(task, run_id, context)` | Start a run; `context` includes target environment, certification status, sandbox flag |
| `pause(run_id)` | Cooperatively pause the run; state is retained |
| `resume(run_id, modified_context?)` | Resume; optionally with modified context (state edit) |
| `cancel(run_id)` | Stop the run and clean up |
| `status(run_id)` | Return current `RunState` |
| `usage(run_id)` | Return token/tool usage since last report |
| `tool_calls(run_id)` | Return tool calls made during the run |
| `sandbox_config(run_id)` | Return the sandbox configuration applied to this run |
| `shaped_output(tool_call_id)` | Return the shaped (filtered/truncated/scanned) output for a tool call |
| `verify_model_identity(run_id, attestation_model)` | Compare the runtime model against the attestation's model identity; return match result |

## Execution Sandbox in the Adapter Contract

The sandbox is part of the adapter contract (DD-14, T13). When a run requires sandbox execution:

1. The Execution API passes `sandbox: true` and the sandbox config (from the manifest) to the adapter.
2. The adapter provisions an isolated execution context:
   - **Resource caps**: memory, CPU, wall-clock limits enforced.
   - **Restricted egress**: network access limited to the `allow` list; cloud metadata endpoints always denied.
   - **Isolated filesystem**: no shared filesystem with the control plane; the sandbox has its own scratch space.
3. Tool calls from within the sandbox are routed through the policy boundary — the sandbox does not bypass policy.
4. On terminal transition, the sandbox context is torn down and resources are released.

### Sandbox Config Schema

```yaml
sandbox:
  enabled: true
  resource_caps:
    memory_mb: 512
    cpu_cores: 1.0
    wall_clock_s: 300
  egress:
    allow: ["api.github.com"]
    deny: ["169.254.169.254"]
    mode: restricted
  filesystem: isolated
```

### Sandbox Guarantees

The guarantees below are the **target** for the container backend. The v0.1.0 local backend is
process-level (POSIX `RLIMIT_AS`/`RLIMIT_CPU` + wall-clock watchdog + tool-call-boundary egress
guard) and is not yet wired into the adapter path (#110). Network-namespace/filesystem isolation
is deferred to the container backend.

| Guarantee | Target (container backend) | v0.1.0 (process backend) |
|-----------|----------------------------|--------------------------|
| Resource isolation | OS-level cgroups or container isolation | POSIX rlimits (memory/CPU) + wall-clock watchdog (pending #110) |
| Network restriction | Egress firewall / network policy | Egress guard at the tool-call boundary only (no network namespace) |
| Filesystem isolation | Separate mount namespace; no shared volumes | Ephemeral working directory (no mount namespace) |
| No control-plane access | Sandbox runs in a separate process/container; no shared credentials | Subprocess shares the control-plane process environment (gap) |
| Tool-call routing | All tool calls go through the policy boundary, even from the sandbox | Implemented |

## Tool-Output Shaping Layer

The adapter includes a tool-output shaping layer that processes tool call results before they reach the agent context window (DD-13, T14):

### Shaping Pipeline

```
Tool Call Result
      │
      ▼
  ┌─────────────────────────────────────────┐
  │ 1. Filter (redact/mask secrets, PII)     │
  │ 2. Truncate (if > max_bytes)             │
  │ 3. Budget (cumulative output budget)     │
  │ 4. Injection scan (prompt-injection)     │
  └─────────────────────────────────────────┘
      │
      ▼
  Shaped Output → Agent Context
```

### Shaping Rules

| Stage | Rule | Config Source |
|-------|------|---------------|
| Filter | Pattern-based redaction/masking | `spec.output_shaping.filter_rules` |
| Truncate | If output > `max_bytes`, apply `truncate_strategy` | `spec.output_shaping.max_bytes`, `truncate_strategy` |
| Budget | Cumulative output bytes per run tracked; excess → aggressive truncation | Run-level output budget |
| Injection scan | Scan for prompt-injection patterns; block or escalate | `spec.output_shaping.injection_scan` |

The shaping layer operates in the adapter (server-side), not in the agent. This ensures that large tool payloads never silently blow context windows and that injection attempts are caught before reaching the agent.

### Injection Scanning

If `injection_scan` is `true`, the shaped output is scanned for prompt-injection patterns (see [Policy engine](policy-engine-design.md) § Prompt-Injection Defense):

- High-confidence patterns → `block_injection` (output never reaches agent; security event recorded)
- Lower-confidence patterns → `escalate` (output delivered but run paused for human review)

## Model-Identity Binding

Certification binds to model identity (DD-10, T11). The adapter verifies that the runtime model matches the attestation's model identity:

1. On run start, the adapter reports the runtime model identity (provider, family, version).
2. The control plane compares it against the attestation's `model_identity`.
3. If they match, the run proceeds.
4. If they differ, the run is blocked (`refused: model_swap`) and a security event is recorded.

This prevents model-swap attacks where an agent certified on model A is secretly run on model B.

### Model Identity Schema

```yaml
model_identity:
  provider: openai
  family: gpt-4o
  version: "2024-08-06"
```

The identity is checked at run start and is immutable for the duration of the run. If a router strategy is used (`spec.model.strategy: router`), the adapter must report which model was actually used for each call, and each call's model is checked against the attestation.

## LLM Provider Seam

Agents must not call model providers directly. Every model call routes through the
control-plane boundary so it can be governed, metered, traced, and identity-checked.

- `WorkerContext.complete(prompt, **options) -> CompletionResult` is the agent-facing seam.
- The seam delegates to the configured **LLM provider** (local Ollama/OMLX-style
  OpenAI-compatible endpoint, cloud OpenAI, or a deterministic fake/replay provider for CI).
  Provider selection and credentials come from `HIVEPLANE_MODEL__*`; the manifest supplies
  the model identity and strategy.
- On each call the seam:
  1. emits a `model_call` telemetry span (prompt/response truncated, model, tokens, cost, latency);
  2. reports real usage (input/output tokens, cost) from the provider response — agents no
     longer hardcode `report_usage()`;
  3. captures the **runtime model identity** reported by the provider and checks it against the
     attestation binding (T11). For `router` strategies, each call's model is checked;
  4. honors cooperative pause/cancel checkpoints.
- `CompletionResult` carries: `content`, `model_identity`, `usage`, `finish_reason`.

See [D17: LLM Provider Design](llm-provider-design.md) (#104).

## Agent Contract Seams

An agent entrypoint is `run(task, ctx) -> result` and must satisfy the minimum contract:

| Capability | Contract |
|------------|----------|
| Model calls | via `ctx.complete()` only; no direct provider/SDK calls |
| Tool calls | via `ctx.tool_call(tool_id)` only; the agent does not supply tool output (#116) |
| Usage | reported automatically by the seams; explicit `report_usage()` is for non-model usage |
| Pause/cancel | call `ctx.checkpoint()` at safe points; long model calls are interruptible |
| Structured result | return a JSON-serializable result matching the task's expected fields |
| Determinism | support the fake provider for reproducible CI and benchmark runs |

## Durable Resume

`pause(run_id)` must persist enough in-flight state for `resume(run_id)` to continue after a
control-plane restart (the current contract assumes in-memory retention — see #111):

- run state, current step, and accumulated tool/usage records live in the durable run store;
- LangGraph runs persist their graph checkpoint (a durable checkpointer replaces `InMemorySaver`);
- on boot, recovery re-attaches an executor to `paused`/`running` runs and rehydrates context;
- a run whose worker process died is reconciled to a defined terminal or resumable state.

See [D18: Durable Resume Design](durable-resume-design.md) (#106).

## Rules

- adapters report state transitions; they do not decide policy
- adapters route tool calls through the policy boundary (including from the sandbox)
- adapters route **model calls** through the provider seam; agents never call providers directly
- adapters execute tool calls and return real data; agents never supply tool output
- adapters report usage for budget accounting; model usage is captured from provider responses
- adapters apply tool-output shaping before returning outputs to the agent context
- adapters scan tool outputs for injection if `injection_scan` is enabled
- adapters capture runtime model identity from actual inference; mismatches block the run
- adapters may implement pause cooperatively, but must report honest state and persist it durably
- adapters must support sandbox execution when the manifest requires it
- adapters must tear down sandbox contexts on terminal transition

## Reference Adapters (v0.1.0)

- **raw-worker** — a plain Python worker; the reference implementation. Lifecycle, tool-call
  routing, and output shaping are implemented. LLM invocation (#115), tool execution (#116),
  sandbox enforcement (#110), and durable resume (#111) are pending.
- **langgraph** — a compiled graph example. Wraps LangGraph's execution model behind the adapter
  contract; currently uses `InMemorySaver` (durable checkpointer pending #122).

### Adapter selection and dispatch (M23, #109)

`HIVEPLANE_EXECUTION__ADAPTER` selects the run executor bound to the `RunService`:

| Value | Behavior |
|-------|----------|
| `none` (default) | No adapter; runs are admitted but never executed (fail-fast warning at startup). |
| `raw-worker` | Bind only the raw-worker adapter. |
| `langgraph` | Bind only the LangGraph adapter. |
| `auto` | Build **both** adapters and bind a `DispatchingAdapter` that routes each run to the adapter named by `workload.spec.runtime.adapter`, remembering the owner per run for `pause`/`resume`/`cancel`/`status`/`usage`. |

The v0.1.0 field test uses `auto` so raw-worker workloads (repo-agent, incident-agent) and the
langgraph workload (docs-agent) run in one stack. The LangGraph adapter hands the run's task to the
graph under the `task` key (`graph.stream({"task": <run task>}, config)`), so a node reads
`state.get("task")` — matching the docs-agent example and the replay generator.

## Conformance Suite

Every adapter must pass the following conformance tests:

### Basic Lifecycle
1. register → submit → transition → usage report → pause → resume → cancel

### Sandbox
2. Submit a destructive run → verify sandbox context is provisioned
3. Verify resource caps are enforced (memory, CPU, wall-clock)
4. Verify egress restrictions (denied endpoint is blocked)
5. Verify filesystem isolation (no access to control-plane FS)
6. Verify sandbox teardown on terminal transition

### Output Shaping
7. Submit a run with a tool that returns > `max_bytes` → verify output is truncated
8. Submit a run with a tool that returns secrets → verify output is redacted
9. Verify cumulative output budget is enforced

### Injection Scanning
10. Submit a run with a tool output containing injection patterns → verify `block_injection`
11. Submit a run with a tool output containing lower-confidence patterns → verify escalation

### Model-Identity Binding
12. Submit a run where runtime model ≠ attestation model → verify run is blocked
13. Submit a run where runtime model = attestation model → verify run proceeds

### Policy Routing
14. Verify all tool calls (including from sandbox) are routed through the policy boundary
15. Verify adapter does not bypass policy for any tool call

### LLM & Tool Seams
16. Verify an agent's model call routes through `ctx.complete()` (no direct provider access)
17. Verify usage/cost is reported from the provider response, not agent-supplied values
18. Verify runtime model identity is captured from inference and mismatch blocks the run
19. Verify tool calls execute and return real (fixture-backed) data, not agent-fabricated output

### Approval Re-dispatch
20. Verify an escalated tool call is re-dispatched and executes after approval (#129)

### Durable Resume
21. Verify a paused run resumes with context intact after a control-plane restart (#111)

## Open Questions

- capability negotiation (which adapters support state edit/replay)
- sandboxing expectations for untrusted adapters
- whether model-identity verification should be per-call or per-run for router strategies
- whether output shaping should be configurable per-tool (some tools may need larger outputs)
- adapter-specific sandbox implementation (container vs cgroups vs WASM)

## See Also

- [Workload manifest](workload-manifest-design.md) — sandbox config, output shaping config, model identity
- [Run lifecycle](run-lifecycle-design.md) — sandbox execution path, output shaping in run flow
- [Policy engine](policy-engine-design.md) — injection scanning, tool trust levels
- [Registry service](registry-service-design.md) — model-identity binding, attestation storage
- [Security baseline](../prd/06-security-baseline.md) — T5, T11, T13, T14
- [Design decisions](design-decisions.md) — DD-02, DD-10, DD-13, DD-14
