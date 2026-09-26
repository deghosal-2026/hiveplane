# WBS v0.2.0 — Part 12: Scheduling, HA & Chaos

**Milestones:** M47–M48 · **Part:** 12 of 19

## Goal

Make fleet execution orderly under load (priorities, limits, backpressure, preemption, maintenance windows, DLQ) and safe at scale (leader election for controllers, plus chaos drills that prove resilience).

## M47 — Scheduling: Priority, Limits, Preemption, Maintenance & DLQ

**Objective:** Add priority queues, per-workload concurrency limits, backpressure with rejection reasons, QoS classes with preemption, maintenance windows, and dead-letter handling for triggers.

**Work items:**

- [ ] [#352](https://github.com/deghosal-2026/hiveplane/issues/352) — M47-01 — Priority queues: runs carry priority; scheduler dequeues by priority + fairness
- [ ] [#353](https://github.com/deghosal-2026/hiveplane/issues/353) — M47-02 — Per-workload and per-tenant concurrency limits
- [ ] [#354](https://github.com/deghosal-2026/hiveplane/issues/354) — M47-03 — Backpressure: admission rejects work with a reason when over capacity; queue depth visible
- [ ] [#355](https://github.com/deghosal-2026/hiveplane/issues/355) — M47-04 — QoS classes: guaranteed / burstable / best-effort mapped to scheduling + budget priority
- [ ] [#356](https://github.com/deghosal-2026/hiveplane/issues/356) — M47-05 — Preemption: urgent (guaranteed) runs preempt best-effort runs with attribution
- [ ] [#357](https://github.com/deghosal-2026/hiveplane/issues/357) — M47-06 — Maintenance windows / freeze: triggers pause, runs drain (integrated with M28)
- [ ] [#358](https://github.com/deghosal-2026/hiveplane/issues/358) — M47-07 — Dead-letter queue + replay for failed trigger deliveries (`hiveplane triggers replay`)
- [ ] [#359](https://github.com/deghosal-2026/hiveplane/issues/359) — M47-08 — Queue visualizer data (depth, priorities, waiting reasons)
- [ ] [#360](https://github.com/deghosal-2026/hiveplane/issues/360) — M47-09 — Tests: priority ordering; limit enforcement; backpressure reason; preemption with attribution; drain during maintenance; DLQ replay

**Test ticket:** [#361](https://github.com/deghosal-2026/hiveplane/issues/361) — Test cases for Scheduling: Priority, Limits, Preemption, Maintenance & DLQ

**Deliverables:**
- `hiveplane.scheduler` package (queues, limits, QoS, preemption)
- Queue API + CLI; `docs/design/fleet-execution-design.md`

**Acceptance criteria:**
- [ ] A higher-priority run is scheduled before a lower-priority one under contention
- [ ] Concurrency limits are enforced per workload and per tenant
- [ ] Over-capacity submissions are rejected/queued with an explicit reason
- [ ] An urgent guaranteed run preempts a best-effort run with attribution recorded
- [ ] During a maintenance window no new work admits and in-flight work drains
- [ ] A dead trigger replays from the DLQ

**Done when:** the fleet schedules work predictably under load, preempts safely, and drains cleanly for maintenance.

**Dependencies:** M28 (freeze/DLQ), M46 (workers), M49 (budget priority).

**Notes / risks:** preemption must be safe — only preempt at idempotent checkpoints; never mid-side-effect. Fairness matters: strict priority can starve low-priority work.

## M48 — Leader Election (HA) & Chaos/Game-Day Mode

**Objective:** Make the control plane safe to run with multiple controller replicas via leader election, and ship a chaos/game-day mode that injects seeded failures to prove resilience on demand.

**Work items:**

- [ ] [#362](https://github.com/deghosal-2026/hiveplane/issues/362) — M48-01 — Leader election for the reconciliation/controller loop (single active reconciler; standbys ready)
- [ ] [#363](https://github.com/deghosal-2026/hiveplane/issues/363) — M48-02 — Documented HA model: single-plane state store, stateless workers, controller leader election
- [ ] [#364](https://github.com/deghosal-2026/hiveplane/issues/364) — M48-03 — Failover: leader loss promotes a standby without double-reconcile or split-brain
- [ ] [#365](https://github.com/deghosal-2026/hiveplane/issues/365) — M48-04 — Chaos mode: `hiveplane chaos` seeded drills — kill a worker mid-run, revoke a cert mid-flight, force budget exhaustion, inject tool failures
- [ ] [#366](https://github.com/deghosal-2026/hiveplane/issues/366) — M48-05 — Drill reporting: what was injected, what the plane did, pass/fail per drill
- [ ] [#367](https://github.com/deghosal-2026/hiveplane/issues/367) — M48-06 — Drill guardrails: drills are scoped to a sandbox/tenant and cannot affect production unless explicitly allowed
- [ ] [#368](https://github.com/deghosal-2026/hiveplane/issues/368) — M48-07 — Tests: second replica does not double-reconcile; leader failover; each drill produces the expected recovery/halt

**Test ticket:** [#369](https://github.com/deghosal-2026/hiveplane/issues/369) — Test cases for Leader Election (HA) & Chaos/Game-Day Mode

**Deliverables:**
- Leader-election implementation + HA documentation
- `hiveplane.chaos` package + drill catalog + `docs/design/fleet-execution-design.md`

**Acceptance criteria:**
- [ ] A second controller replica does not execute the same reconcile actions (verified)
- [ ] Leader loss promotes a standby within the configured window with no split-brain
- [ ] Kill-worker drill → lease expiry reassigns the run
- [ ] Revoke-cert-mid-flight drill → the run halts (or degrades) per policy
- [ ] Budget-exhaustion and tool-failure drills produce the expected intervention
- [ ] Each drill emits a pass/fail report

**Done when:** the plane runs HA without double-acting, and resilience can be proven on demand with chaos drills.

**Dependencies:** M26 (reconciliation), M34 (quarantine), M46 (workers), M47 (scheduling).

**Notes / risks:** leader election requires a reliable coordination primitive (Postgres advisory lock or equivalent) — test split-brain explicitly. Chaos drills must be safe by default; production drills require an explicit flag.

## Exit Gate (M47, M48)

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] All relevant docs updated (scheduler, HA model, chaos drills)
- [ ] All M47–M48 issues done and closed
- [ ] Commit and push changes

## See Also

- [PRD 05 Features](../../prd/05-features.md) — Fleet Control & Scheduling theme
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillar K
- [v0.2.0 index](wbs-v0.2.0-index.md)
