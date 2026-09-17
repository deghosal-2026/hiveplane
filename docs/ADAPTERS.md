# HivePlane Adapters

Runtime adapters are how HivePlane stays framework-agnostic. The control plane owns the workload contract; adapters translate that contract to a concrete runtime.

## Why Adapters

The failure mode to avoid is becoming a wrapper around one framework. If HivePlane knows too much about LangGraph, it stops being a control plane. If it knows too little, it becomes a dashboard with no control. The adapter contract is the boundary that keeps both failure modes away.

Adapters report state transitions, tool calls, and usage; **they do not decide policy**. Every tool call must route through the policy boundary. Every model call must be checked against the certification attestation. Every tool output must pass through the shaping layer.

## Adapter Contract

An adapter must translate:

| Control-plane concept | Adapter responsibility |
|-----------------------|------------------------|
| Workload manifest | Configure the runtime from declarative state |
| Task submission | Start a run and return a run handle |
| State transition | Report queued → running → paused/completed/failed/cancelled |
| Tool call metadata | Emit tool calls for policy evaluation and audit; **route every call through the policy boundary** |
| Spend / token usage | Report usage for budget accounting |
| Intervention | Pause, resume, cancel, and (where supported) edit state |
| **Model identity** | Report the model in use; the control plane checks it against the certification attestation |
| **Sandbox execution** | Execute destructive or production-affecting runs in an isolated context with resource caps |
| **Tool-output routing** | Route all tool outputs through the shaping layer before they reach the agent context |
| **Injection scanning** | Submit tool outputs to the injection scanner at the boundary |

### Typed contract

The contract is a `typing.Protocol` in `hiveplane.adapters`. An adapter reports state and
usage and routes tool calls through the control-plane boundary; it never decides policy.

```python
from hiveplane.adapters import Adapter, AdapterRunExecutor, StubAdapter

class Adapter(Protocol):
    def register(self, workload: AgentWorkload) -> None: ...
    def submit(self, context: RunContext) -> None: ...
    def pause(self, run_id: str) -> bool: ...
    def resume(self, run_id: str) -> bool: ...
    def cancel(self, run_id: str) -> None: ...
    def status(self, run_id: str) -> RunState: ...
    def usage(self, run_id: str) -> UsageReport | None: ...
    def tool_calls(self, run_id: str) -> list[ToolCallResult]: ...
```

- `submit` receives a `RunContext` (run, workload, sandbox flag) and starts execution.
- `status` must report honest state; `pause`/`resume` return whether the request was accepted.
- `usage` returns spend since the last report for budget accounting.
- `tool_calls` returns the calls the adapter routed through the tool boundary for audit.
- `AdapterRunExecutor` bridges an adapter onto the run lifecycle's `RunExecutor` seam, so
  the lifecycle never depends on adapter internals.
- `StubAdapter` is the in-memory conformance baseline (`hiveplane.adapters.stub`).

### Writing a worker

A raw-worker entrypoint is `module:function` (from `spec.runtime.entrypoint`) and has the
signature `run(task: dict, ctx: WorkerContext) -> JsonValue`.

```python
from hiveplane.adapters.worker import WorkerContext

def run(task: dict, ctx: WorkerContext) -> dict:
    result = ctx.tool_call("mcp.github.list_pull_requests", host="api.github.com")
    ctx.report_usage(input_tokens=120, output_tokens=40, tool_calls=1)
    ctx.checkpoint()
    return {"tool": result.tool_id}
```

- `ctx.tool_call(...)` routes through the policy, egress, and shaping boundary and raises
  `ToolCallDeniedError`, `ToolCallBlockedError`, or `ToolCallEscalatedError` when the call is
  not allowed.
- `ctx.report_usage(...)` is priced server-side and enforced against the run budget; it raises
  `RunTerminatedError` if the run has gone terminal.
- `ctx.checkpoint()` blocks while the run is paused and raises `RunCancelledError` when stopped.
- The adapter records the returned value as the run result and reports the terminal state; it
  never decides policy.

Raw-worker execution is opt-in: set `HIVEPLANE_EXECUTION__ADAPTER=raw-worker` (default `none`)
and point `HIVEPLANE_EXECUTION__ENTRYPOINTS_ROOT` at the project root that holds the entrypoint
modules.

### Execution Sandbox

When a workload has `sandbox.enabled: true`, the adapter must:

1. **Create an isolated execution context** — no shared filesystem with the control plane; separate process or container.
2. **Enforce resource caps** — `memory_mb`, `cpu_cores`, `wall_clock_seconds`, `max_output_bytes` from the manifest. The adapter (or the sandbox runtime it delegates to) must kill or pause the run if any cap is exceeded.
3. **Restrict network egress** — only hostnames in `egress_allowlist` are reachable; all other outbound connections are blocked.
4. **Route tool calls through the policy boundary** — the sandbox does not bypass policy; every tool call is still evaluated for permissions, trust level, and approval requirements.
5. **Record the full action and result** — sandboxed runs are auditable end-to-end; the audit trail includes the sandbox context, resource usage, and egress attempts.

The sandbox is a separate execution context with resource caps, restricted network egress, and no shared filesystem with the control plane (threat T13: sandbox escape).

### Tool-Output Shaping

The adapter must route every tool output through the shaping layer before it enters the agent's context window. The shaping layer:

1. **Filters** — applies `filter_rules` (regex patterns with actions: `redact`, `collapse_whitespace`, `drop`, `escalate`). Secrets and sensitive patterns are redacted before persistence.
2. **Truncates** — applies `max_bytes_per_tool_call` using the configured `truncation_strategy` (`head_tail`, `head_only`, or `summary`). Large payloads are bounded so context windows don't silently blow.
3. **Budgets** — tracks cumulative tool output size across a run; if total output exceeds the sandbox `max_output_bytes` cap, the run is paused or failed.
4. **Scans for injection** — tool outputs are scanned for prompt-injection patterns (threat T14). Suspicious patterns escalate for approval rather than silently passing through to the agent.

The shaping happens at the boundary, not inside the agent. The adapter never passes raw tool output directly to the model.

### Model-Identity Binding

Certification binds to `model.identity` from the manifest. The adapter must:

1. **Report the model in use** — on run start, emit the model identifier (provider + model name + version).
2. **Accept the control-plane check** — the control plane compares the reported model against the certification attestation. If they don't match, the run is blocked (threat T11: model-swap attack).
3. **Emit model changes** — if the model changes mid-run (e.g. a tiered strategy escalates to a different model), the adapter must report the change. The control plane re-checks against the attestation; a mismatch blocks the run.

This means: an agent certified on `gpt-4o-2024-08-06` cannot silently run on `gpt-4o-mini`. The certification is bound to the model, and the runtime is checked.

### Injection Scanning of Tool Outputs

Tool outputs are a primary injection vector (threat T14). A malicious tool response can contain instructions that hijack the agent. The adapter must submit every tool output to the injection scanner:

1. **Scan before shaping** — the injection scanner runs on the raw tool output before filtering and truncation.
2. **Escalate on detection** — suspicious patterns (e.g. "ignore previous instructions", "you are now in admin mode", embedded command sequences) escalate for human approval rather than being passed through.
3. **Log the scan result** — every scan outcome (clean, suspicious, blocked) is recorded in the audit trail with the matched patterns.

## Adapters (v0.1.0)

### Raw Python worker

Reference adapter, no framework dependency. The entrypoint is `module:function` with signature
`run(task, ctx)`. Enabled with `HIVEPLANE_EXECUTION__ADAPTER=raw-worker`; see **Writing a worker**
above.

### LangGraph

Example adapter for a compiled graph. Install the optional extra and enable it:

```bash
pip install -e ".[langgraph]"
HIVEPLANE_EXECUTION__ADAPTER=langgraph
```

- The entrypoint (`spec.runtime.entrypoint`) resolves to a **compiled graph object**
  (`module:graph`), not a function.
- Nodes reach the control-plane client through
  `config["configurable"]["hiveplane_ctx"]` and call `ctx.tool_call(...)`, `ctx.report_usage(...)`,
  and `ctx.checkpoint()` exactly as a raw worker does.
- Supersteps are streamed (`stream_mode="values"`); `ctx.checkpoint()` between supersteps gives
  operators cooperative pause/resume/cancel.
- A LangGraph `interrupt(...)` maps to run `PAUSED`; resume re-drives the graph with
  `Command(resume=True)` on the same `thread_id`.
- `examples/docs_agent.py` is the reference graph.

### Later

- Additional runtimes as the adapter contract proves stable (v0.3.0 targets ≥ 3 adapter types).

## Adapter Conformance Suite

Each adapter must pass a conformance suite that exercises the full lifecycle plus the new safety and certification boundaries.

### Lifecycle Tests

| Test | Verifies |
|------|----------|
| Register → submit → state transitions | Core run lifecycle works |
| Budget report | Usage is reported and priced correctly |
| Pause → resume → cancel | Intervention controls work |
| Durable state after restart | Paused run survives control-plane restart |

### Sandbox Conformance Tests

| Test | Verifies |
|------|----------|
| Isolated execution context | Run executes in a separate context; no shared filesystem with control plane |
| Memory cap enforcement | Run is killed/paused when `memory_mb` is exceeded |
| CPU cap enforcement | Run is throttled or killed when `cpu_cores` is exceeded |
| Wall-clock cap enforcement | Run is terminated when `wall_clock_seconds` is exceeded |
| Output cap enforcement | Run is paused/failed when `max_output_bytes` is exceeded |
| Egress restriction | Only `egress_allowlist` hosts are reachable; other egress is blocked |
| Tool-call routing through policy | Sandbox does not bypass the policy boundary |

### Tool-Output Shaping Conformance Tests

| Test | Verifies |
|------|----------|
| Truncation applied | Output exceeding `max_bytes_per_tool_call` is truncated per strategy |
| Filter rules applied | Secrets and matching patterns are redacted/dropped/collapsed |
| Cumulative output budget | Total output exceeding `max_output_bytes` pauses/fails the run |
| Injection scanning | Suspicious patterns are detected and escalated, not passed through |
| No raw output to agent | Tool output never reaches the agent context without passing through shaping |

### Model-Binding Conformance Tests

| Test | Verifies |
|------|----------|
| Model identity reported | Adapter emits model identifier on run start |
| Match allows run | Reported model matches attestation → run proceeds |
| Mismatch blocks run | Reported model differs from attestation → run is blocked |
| Mid-run model change | Model change mid-run is detected; mismatch blocks the run |

## See Also

- [Runtime adapter design](design/runtime-adapter-design.md)
- [Run lifecycle design](design/run-lifecycle-design.md)
- [Manifest format spec](workloads/manifest-format-spec.md)
- [PRD 02: Architecture](prd/02-architecture.md)
- [PRD 06: Security Baseline](prd/06-security-baseline.md)
