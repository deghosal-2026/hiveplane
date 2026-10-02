# WBS v0.2.0 — Part 12: Scheduling, HA & Chaos

**Milestones:** M47–M48 · **Part:** 12 of 19

## Goal

Make fleet execution orderly under load (priorities, limits, backpressure, preemption, maintenance windows, DLQ) and safe at scale (leader election for controllers, plus chaos drills that prove resilience).

## M47 — Scheduling: Priority, Limits, Preemption, Maintenance & DLQ

**Objective:** Add priority queues, per-workload concurrency limits, backpressure with rejection reasons, QoS classes with preemption, maintenance windows, and dead-letter handling for triggers.

**Work items:**

- [x] [#352](https://github.com/deghosal-2026/hiveplane/issues/352) — M47-01 — Priority queues: runs carry priority; scheduler dequeues by priority + fairness
- [x] [#353](https://github.com/deghosal-2026/hiveplane/issues/353) — M47-02 — Per-workload and per-tenant concurrency limits
- [x] [#354](https://github.com/deghosal-2026/hiveplane/issues/354) — M47-03 — Backpressure: admission rejects work with a reason when over capacity; queue depth visible
- [x] [#355](https://github.com/deghosal-2026/hiveplane/issues/355) — M47-04 — QoS classes: guaranteed / burstable / best-effort mapped to scheduling + budget priority
- [x] [#356](https://github.com/deghosal-2026/hiveplane/issues/356) — M47-05 — Preemption: urgent (guaranteed) runs preempt best-effort runs with attribution
- [x] [#357](https://github.com/deghosal-2026/hiveplane/issues/357) — M47-06 — Maintenance windows / freeze: triggers pause, runs drain (integrated with M28)
- [x] [#358](https://github.com/deghosal-2026/hiveplane/issues/358) — M47-07 — Dead-letter queue + replay for failed trigger deliveries (`hiveplane triggers replay`)
- [x] [#359](https://github.com/deghosal-2026/hiveplane/issues/359) — M47-08 — Queue visualizer data (depth, priorities, waiting reasons)
- [x] [#360](https://github.com/deghosal-2026/hiveplane/issues/360) — M47-09 — Tests: priority ordering; limit enforcement; backpressure reason; preemption with attribution; drain during maintenance; DLQ replay

**Test ticket:** [#361](https://github.com/deghosal-2026/hiveplane/issues/361) — Test cases for Scheduling: Priority, Limits, Preemption, Maintenance & DLQ

**Deliverables:**
- `hiveplane.scheduler` package (queues, limits, QoS, preemption)
- Queue API + CLI; `docs/design/fleet-execution-design.md`

**Acceptance criteria:**
- [x] A higher-priority run is scheduled before a lower-priority one under contention
- [x] Concurrency limits are enforced per workload and per tenant
- [x] Over-capacity submissions are rejected/queued with an explicit reason
- [x] An urgent guaranteed run preempts a best-effort run with attribution recorded
- [x] During a maintenance window no new work admits and in-flight work drains
- [x] A dead trigger replays from the DLQ

**Done when:** the fleet schedules work predictably under load, preempts safely, and drains cleanly for maintenance.

> **Status:** M47 complete. `hiveplane.scheduler` provides a priority queue ordered by QoS class (guaranteed/burstable/best-effort), explicit priority, and aging (so strict priority cannot starve lower classes); per-workload and per-tenant concurrency limits plus a max queue depth give backpressure with stable reason codes (`capacity: per_workload_limit`, `capacity: per_tenant_limit`, `capacity: queue_depth`, `maintenance: frozen`); guaranteed work may preempt best-effort/burstable victims **only at idempotent checkpoints**, with `preempted_by` attribution and a recorded preemption trail; maintenance/freeze blocks admission (integrated with M28 freeze); and the queue visualizer exposes depth, QoS/priority breakdown, waiting reasons, and running load (`GET /queue`, `hiveplane queue`). DLQ + replay for failed trigger deliveries (M27) covers `hiveplane triggers replay`. Issues #352–#361 closed; 2062 tests pass with a database, coverage 95.05%, ruff and mypy strict clean.

**Dependencies:** M28 (freeze/DLQ), M46 (workers), M49 (budget priority).

**Notes / risks:** preemption must be safe — only preempt at idempotent checkpoints; never mid-side-effect. Fairness matters: strict priority can starve low-priority work.

## M48 — Leader Election (HA) & Chaos/Game-Day Mode

**Objective:** Make the control plane safe to run with multiple controller replicas via leader election, and ship a chaos/game-day mode that injects seeded failures to prove resilience on demand.

**Work items:**

- [x] [#362](https://github.com/deghosal-2026/hiveplane/issues/362) — M48-01 — Leader election for the reconciliation/controller loop (single active reconciler; standbys ready)
- [x] [#363](https://github.com/deghosal-2026/hiveplane/issues/363) — M48-02 — Documented HA model: single-plane state store, stateless workers, controller leader election
- [x] [#364](https://github.com/deghosal-2026/hiveplane/issues/364) — M48-03 — Failover: leader loss promotes a standby without double-reconcile or split-brain
- [x] [#365](https://github.com/deghosal-2026/hiveplane/issues/365) — M48-04 — Chaos mode: `hiveplane chaos` seeded drills — kill a worker mid-run, revoke a cert mid-flight, force budget exhaustion, inject tool failures
- [x] [#366](https://github.com/deghosal-2026/hiveplane/issues/366) — M48-05 — Drill reporting: what was injected, what the plane did, pass/fail per drill
- [x] [#367](https://github.com/deghosal-2026/hiveplane/issues/367) — M48-06 — Drill guardrails: drills are scoped to a sandbox/tenant and cannot affect production unless explicitly allowed
- [x] [#368](https://github.com/deghosal-2026/hiveplane/issues/368) — M48-07 — Tests: second replica does not double-reconcile; leader failover; each drill produces the expected recovery/halt

**Test ticket:** [#369](https://github.com/deghosal-2026/hiveplane/issues/369) — Test cases for Leader Election (HA) & Chaos/Game-Day Mode

**Deliverables:**
- Leader-election implementation + HA documentation
- `hiveplane.chaos` package + drill catalog + `docs/design/fleet-execution-design.md`

**Acceptance criteria:**
- [x] A second controller replica does not execute the same reconcile actions (verified)
- [x] Leader loss promotes a standby within the configured window with no split-brain
- [x] Kill-worker drill → lease expiry reassigns the run
- [x] Revoke-cert-mid-flight drill → the run halts (or degrades) per policy
- [x] Budget-exhaustion and tool-failure drills produce the expected intervention
- [x] Each drill emits a pass/fail report

**Done when:** the plane runs HA without double-acting, and resilience can be proven on demand with chaos drills.

> **Status:** M48 complete. `hiveplane.ha` provides controller leader election over a lease table (in-memory or PostgreSQL row-locked) with a monotonic **fencing epoch**; standbys do not act, and a stale leader that resumes after failover is rejected (`StaleLeaderError`), so a second replica cannot double-reconcile. `hiveplane.chaos` runs seeded drills (kill-worker, revoke-cert, exhaust-budget, inject-tool-failure) through a drill catalog, emits pass/fail reports, and enforces guardrails: production drills require `allow_production` plus an admin authorizer, otherwise they are refused. Ships API (`GET /cluster/leader`, `POST/GET /chaos/drills`), CLI (`cluster leader`, `chaos run|drills`), and the `leader_leases` table (migration `0025`). Issues #362–#369 closed; 2078 tests pass with a database, coverage 95.04%, ruff and mypy strict clean.

**Dependencies:** M26 (reconciliation), M34 (quarantine), M46 (workers), M47 (scheduling).

**Notes / risks:** leader election requires a reliable coordination primitive (Postgres advisory lock or equivalent) — test split-brain explicitly. Chaos drills must be safe by default; production drills require an explicit flag.

## Exit Gate (M47, M48)

- [x] All tests in the system pass: `pytest`
- [x] Code coverage total > 95%
- [x] Ruff clean
- [x] Mypy strict clean
- [x] All relevant docs updated (scheduler, HA model, chaos drills)
- [x] All M47–M48 issues done and closed
- [x] Commit and push changes

## See Also

- [PRD 05 Features](../../prd/05-features.md) — Fleet Control & Scheduling theme
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillar K
- [v0.2.0 index](wbs-v0.2.0-index.md)
