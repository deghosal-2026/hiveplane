# D6: Runtime Adapter Design

> Status: draft.

## Problem

HivePlane must operate heterogeneous runtimes without becoming a wrapper around any one of them (DD-02).

## Adapter Interface

```
register(manifest) -> adapter_handle
submit(task, run_id) -> void
pause(run_id) -> void
resume(run_id) -> void
cancel(run_id) -> void
status(run_id) -> RunState
usage(run_id) -> UsageReport
tool_calls(run_id) -> [ToolCall]
```

## Rules

- adapters report state transitions; they do not decide policy
- adapters route tool calls through the policy boundary
- adapters report usage for budget accounting
- adapters may implement pause cooperatively, but must report honest state

## Reference Adapters (v0.1.0)

- **raw-worker** — a plain Python worker; the reference implementation
- **langgraph** — a compiled graph example

## Conformance Suite

Every adapter must pass: register → submit → transition → usage report → pause → resume → cancel.

## Open Questions

- capability negotiation (which adapters support state edit/replay)
- sandboxing expectations for untrusted adapters
