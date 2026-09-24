# D18: Durable Resume Design

> Status: implemented (M23, #106; #111 startup recovery, #122 durable LangGraph checkpoint). Defines startup
> recovery and restart-resume. Prerequisite for scenario S8 and the "durable state" promise
> (run-lifecycle-design.md).

## Problem

Run state is persisted in the run store, but nothing reconnects runs to a worker after a
control-plane restart:

- The runtime adapter keeps run controls, tool calls, and usage in process memory. When the
  process dies, a paused run's `RunControl` and worker thread are gone.
- `RunService.resume()` calls `executor.resume(run_id)`, which returns `False` when the adapter
  has no control for the run — so resuming a paused run after restart silently does nothing.
- The app lifespan (`api/app.py`) only configures telemetry; it does not scan for interrupted
  runs.
- LangGraph runs use `InMemorySaver`, so graph checkpoint state is lost on restart too.

Result: S8 ("pause → restart control plane → resume with context intact") cannot pass, and the
run-lifecycle promise "runs survive a control-plane restart" is unmet.

## Goals

- A paused run survives a control-plane restart and resumes with context intact.
- Running runs interrupted by a crash are reconciled to a defined state.
- Recovery is idempotent and safe to run on every boot.
- LangGraph graph checkpoints persist alongside run state.

## Non-Goals

- Distributed/multi-node recovery — v0.1.0 is a single control-plane process.
- Exactly-once side effects for interrupted tool calls — reconciliation is at-least-once with
  recorded attribution.
- Recovering runs from a different version's schema without migration (migrations run first).

## State Model

What must be durable to resume a run:

| Data | Where | Notes |
|------|-------|-------|
| Run identity, workload, context, state | run store | already persisted |
| Task payload, model identity, sandbox flag | run store | already persisted |
| Event/transition log | run store | already persisted |
| Tool calls made so far | run store (append) | extend: persist tool-call records |
| Usage accumulated | usage events | already persisted for budget |
| Pause/cancel intent | run state + event | must be durable, not just in-memory control |
| Agent step / graph checkpoint | run store / checkpointer | new (LangGraph durable saver) |

## Recovery on Boot

On application startup (lifespan), run a recovery pass:

1. Query the run store for runs in non-terminal states (`queued`, `running`, `paused`).
2. For each, determine worker reality:
   - **paused** → no worker should exist; mark as recoverable, re-attach an executor.
   - **running** → the worker died with the previous process; reconcile per policy (below).
   - **queued** → re-enqueue or re-submit through admission (respecting idempotency).
3. Re-register the workload's entrypoint (via the adapter's `register`) and rebuild any
   adapter-side bookkeeping from persisted state.
4. Record a recovery event on each affected run for audit.

Recovery must be safe to run repeatedly (idempotent): a run already reconciled is skipped.

### Running-run reconciliation

A `running` run whose process died is in an unknown mid-step condition. Policy:

- If the adapter can resume from a persisted checkpoint (LangGraph) or the run is idempotent by
  contract → mark recoverable and resume on operator action (or automatically if configured).
- Otherwise → transition to `failed` with reason `interrupted` (attributed to recovery), so the
  fleet view reflects reality rather than a run stuck in `running` forever.

The choice is recorded and visible; silent resurrection of a non-idempotent run is forbidden.

## Resume Semantics

- `pause(run_id)` persists the pause intent (run state) and requests a cooperative stop; the
  worker blocks at its next `ctx.checkpoint()`.
- On restart, a paused run's control is reconstructed from persisted state. `resume(run_id)`
  then re-attaches the executor and continues from the persisted step/checkpoint.
- `stop(run_id)` persists a terminal intent; a recovered run in `cancelled` is never resumed.
- Resume with modified context (state edit) is applied before the worker continues.

## Adapter Contract Changes

- The adapter must support **re-attach**: given persisted run state, rebuild `RunControl` and
  continue execution without restarting from step zero where a checkpoint exists.
- The adapter must expose enough state to reconcile (`status`, `tool_calls`, `usage`) after
  re-attach.
- The in-process spawner assumption is replaced by a spawner that can rehydrate from the store.

## LangGraph Checkpointing

- Replace `InMemorySaver` with a durable checkpointer (Postgres-backed) keyed by run id / thread
  id (#122).
- Recovery rehydrates the graph from its last checkpoint and continues at the pending node.
- The checkpointer lifecycle is tied to the run: destroyed or archived on terminal transition.

## Interaction with Sandbox and Stores

- Sandbox state is ephemeral; a recovered run is re-provisioned a sandbox rather than restoring
  the old one (documented; see D11 open question).
- Recovery depends on the run store being durable (Postgres in Docker; #118) and migrations
  having run.
- Budget/certification state used during resume must also be durable (#126, #128), otherwise a
  resumed run may re-check against reset budgets or lost certification.

## Error Handling

| Condition | Behavior |
|-----------|----------|
| Run store unavailable | Recovery aborts; control plane fails readiness (#130) |
| Workload no longer registered | Run reconciled to `failed` (reason: workload missing) |
| Entrypoint fails to load | Run reconciled to `failed` (reason: entrypoint) |
| Checkpoint corrupt | Run reconciled to `failed`; operator notified |
| Duplicate recovery pass | Idempotent; no double transitions |

## Testing Strategy

- Pause a run, kill the process (`docker compose restart`), resume, verify completion with
  context intact.
- Kill a running run mid-step, restart, verify reconciliation matches policy.
- LangGraph: pause mid-graph, restart, verify it resumes at the correct node.
- Idempotency: run recovery twice, assert no duplicate transitions or events.

## Open Questions

- Automatic vs operator-triggered resume for interrupted runs (default: operator-triggered).
- Whether to re-provision sandboxes on recovery or allow a long-lived sandbox to survive.
- At-least-once side effects for interrupted tool calls: dedup key vs operator review.
- How recovery interacts with watch-mode/long-running runs (v0.2.0).

## See Also

- [Run lifecycle design](run-lifecycle-design.md) (D2) — state machine, durability promise
- [State store design](state-store-design.md) (D7) — persistence, migrations
- [Runtime adapter design](runtime-adapter-design.md) (D6) — adapter contract, re-attach
- [Execution sandbox design](execution-sandbox-design.md) (D11) — sandbox on recovery
- [PRD 09: Roadmap](../prd/09-roadmap.md) — v0.1.0 "durable state"
