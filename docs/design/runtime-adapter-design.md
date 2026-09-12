# D6: Runtime Adapter Design

> Status: draft

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

| Guarantee | How |
|-----------|-----|
| Resource isolation | OS-level cgroups or container isolation |
| Network restriction | Egress firewall / network policy |
| Filesystem isolation | Separate mount namespace; no shared volumes |
| No control-plane access | Sandbox runs in a separate process/container; no shared credentials |
| Tool-call routing | All tool calls go through the policy boundary, even from the sandbox |

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

## Rules

- adapters report state transitions; they do not decide policy
- adapters route tool calls through the policy boundary (including from the sandbox)
- adapters report usage for budget accounting
- adapters apply tool-output shaping before returning outputs to the agent context
- adapters scan tool outputs for injection if `injection_scan` is enabled
- adapters verify model identity at run start; mismatches block the run
- adapters may implement pause cooperatively, but must report honest state
- adapters must support sandbox execution when the manifest requires it
- adapters must tear down sandbox contexts on terminal transition

## Reference Adapters (v0.1.0)

- **raw-worker** — a plain Python worker; the reference implementation. Supports sandbox execution, output shaping, and model-identity verification.
- **langgraph** — a compiled graph example. Wraps LangGraph's execution model behind the adapter contract.

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
