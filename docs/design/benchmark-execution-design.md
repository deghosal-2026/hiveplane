# D19: Benchmark Execution Design

> Status: proposed (M23, #105). Defines how certification executes the real agent. Prerequisite
> for a meaningful benchmark and scenario S1.

## Problem

The certification runner accepts a `TaskExecutor` protocol, but the only usable implementation
is `ReferenceExecutor`, which returns the task's own expected value as the execution output:

```python
# certification/runner.py — the check compares the expected value to itself
def execute(self, task):
    if task.check.type is CheckType.EXACT_MATCH:
        return TaskExecution(output={task.check.field: task.check.value})
```

Every task therefore passes by definition. The check never sees agent behavior. Meanwhile
`RawWorkerAdapter.submit(context)` can run the real agent through the boundary (adapter →
policy → tools → LLM), but the certification runner never calls it: the two execution worlds are
disconnected. The PRD names this exact failure as a kill criterion — "Certification is perceived
as theater. The benchmark must be real" (`08-risks.md`).

## Goals

- Certification executes the workload's real entrypoint against each corpus task.
- Tasks run through the **normal execution path** (adapter → sandbox → policy), not a bypass.
- Results are deterministic enough to certify: fixed model, fixed inputs, network off unless
  `allow_network`, injected timestamps, fake/replay provider in CI.
- `ReferenceExecutor` remains only for corpus self-satisfiability checks and minimal demos.
- A deliberately regressed agent (wrong model, broken prompt) fails certification.

## Non-Goals

- New corpus check types (`schema_match`, `rubric`, `custom`) — deferred beyond v0.1.0.
- Sandbox-hosted benchmark execution with container isolation — deferred (#110 provides the
  process backend; the container backend is later).
- Parallel task execution — v0.1.0 runs tasks sequentially.

## Design

### AdapterTaskExecutor

A `TaskExecutor` implementation that bridges a corpus task to a real run:

```
class AdapterTaskExecutor:
    def __init__(self, run_service, registry, *, timeout_s: float): ...

    def execute(self, task: BenchmarkTask) -> TaskExecution:
        # 1. Build a run submission for the workload under test
        #    - context: sandbox (benchmarks never run in production context)
        #    - task payload: task.input
        #    - model_identity: pinned (from benchmark config / manifest / --model-identity)
        #    - caller: "benchmark"
        # 2. Submit synchronously and wait for a terminal state
        # 3. Collect the agent result, usage, tool calls, and trace id
        # 4. Return TaskExecution(output=result, actions=tool_ids, latency_ms, tokens, trace_id)
```

Key properties:

- **Synchronous.** The runner needs the result to evaluate the check; the executor submits and
  blocks (with `timeout_s`) until the run reaches `completed`/`failed`/`cancelled`. This is the
  one place where the fire-and-forget run model is wrapped in a wait.
- **Policy-preserving.** The run goes through `RunService` admission and the `ToolGateway`
  exactly like any other run. The benchmark does not get a privileged path.
- **Pinned.** The executor refuses to run if no model identity is pinned (mirrors the design's
  "fixed model by exact ID"). The identity is passed to the run and bound to the attestation.
- **Isolated.** Runs use the `sandbox` context; tool side effects come from the fixture tool
  executor (#116), and network is off unless the task sets `allow_network`.

### Mapping task → run → TaskExecution

| Corpus task field | Run submission | Collected into |
|-------------------|----------------|----------------|
| `input` | `task` payload | agent result |
| `timeout_seconds` | run/executor timeout | `latency_ms` bound |
| `allow_network` | sandbox egress policy | `network_used` |
| `expected` / `check` | not passed to the agent | evaluated by `BenchmarkRunner` |
| — | pinned model identity | `TaskExecution.model_identity` (for attestation) |

The agent sees only `input`; it must not see `expected` or `check`, otherwise the benchmark is
gameable.

### Executor selection

`HIVEPLANE_CERTIFICATION__EXECUTOR` gains a value:

| Value | Executor | Use |
|-------|----------|-----|
| `none` (default) | `UnconfiguredTaskExecutor` | refuses; forces explicit configuration |
| `reference` | `ReferenceExecutor` | corpus self-check, minimal demo |
| `adapter` | `AdapterTaskExecutor` | real certification (this design) |

`adapter` requires the LLM provider seam (#107/#115), the tool executor (#116), and a durable
signing keypair (#125) to be wired.

### Determinism with a real agent

Real LLMs are not perfectly deterministic, even at temperature 0. The strategy:

- **CI:** fake/replay provider → fully deterministic, hermetic.
- **Field test:** local or cloud provider at temperature 0, pinned model ID, fixture inputs.
  Certification thresholds tolerate a small number of non-critical failures; critical failures
  remain zero-tolerance.
- **Replay:** record provider responses for corpus tasks and replay them to reproduce a
  benchmark run exactly (supports regression diffs).

A task that cannot be made deterministic is excluded from the certification corpus and used only
for manual smoke testing (per D10).

### Runner integration

`BenchmarkRunner.run()` is unchanged in shape: it calls `executor.execute(task)`, enforces the
task's bounds (`_enforce_bounds`), then evaluates the deterministic check (`evaluate_check`).
Only the executor implementation changes. `TaskExecution` gains an optional `model_identity`
field so the runner can assert the executed model matches the pinned identity.

## Error Handling

| Condition | Behavior |
|-----------|----------|
| No model identity pinned | Executor refuses (configuration error) |
| Run fails / times out | Task recorded as `FAIL` with the attributed reason |
| Run cancelled | Task recorded as `FAIL` (reason: cancelled) |
| Agent returns no result | Task `FAIL` (missing output) |
| Policy blocks a tool the task needs | Task `FAIL` (policy decision recorded in trace) |

## Testing Strategy

- A known-good fake agent passes all corpus tasks with the `adapter` executor.
- A deliberately broken agent (wrong prompt/model) fails at least one critical task.
- The same corpus passes with `reference` but the reference path is never used for production
  certification.
- Determinism: two CI runs with the fake provider produce identical `BenchmarkResult`s
  (content-addressed `benchmark_run_id`).

## Open Questions

- How to bound total benchmark wall-clock (sum of task timeouts can exceed the "minutes" goal)?
- Whether the executor should reuse one run per corpus or one run per task (v0.1.0: per task).
- How regression diffs bind to replay files when the provider is non-deterministic.
- Whether benchmark runs should be hidden from the operator fleet view or shown as a distinct class.

## See Also

- [Certification pipeline design](certification-pipeline-design.md) (D10) — runner, checks, attestation
- [LLM provider design](llm-provider-design.md) (D17) — pinned model, runtime identity
- [Runtime adapter design](runtime-adapter-design.md) (D6) — adapter contract
- [Execution sandbox design](execution-sandbox-design.md) (D11) — benchmark sandbox
- [Execution path design](execution-path-design.md) — run lifecycle, admission
- [PRD 08: Risks](../prd/08-risks.md) — certification-as-theater kill criterion
