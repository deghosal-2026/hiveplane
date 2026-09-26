# WBS v0.2.0 — Part 3: Multi-Agent Orchestration

**Milestones:** M29–M30 · **Part:** 3 of 19

## Goal

A fleet OS must run fleets, not just single agents. Add workload pipelines (a DAG of chained agents with handoffs and per-step gates), a smart task router, and agent-as-tool composition.

## M29 — Multi-Agent Pipelines

**Objective:** Build the pipeline runtime: a declarative DAG of workloads with parent-child runs, fan-in/fan-out, output→input handoffs, per-step approval gates, and pipeline-level budgets.

**Work items:**

- [x] [#189](https://github.com/deghosal-2026/hiveplane/issues/189) — M29-01 — Pipeline spec (nodes = workloads/steps, edges = data/ordering, handoff mappings) with strict validation + cycle detection
- [x] [#190](https://github.com/deghosal-2026/hiveplane/issues/190) — M29-02 — Pipeline execution engine on top of the run lifecycle: parent run + child runs, state aggregation
- [x] [#191](https://github.com/deghosal-2026/hiveplane/issues/191) — M29-03 — Handoff layer: map a step's structured output into the next step's input; schema-validated at the boundary
- [x] [#192](https://github.com/deghosal-2026/hiveplane/issues/192) — M29-04 — Fan-out/fan-in (map over N items, reduce aggregate) using parallel child runs
- [x] [#193](https://github.com/deghosal-2026/hiveplane/issues/193) — M29-05 — Per-step approval gates (interrupt before/after a node) reusing the approval queue
- [x] [#194](https://github.com/deghosal-2026/hiveplane/issues/194) — M29-06 — Pipeline-level budget: cumulative cap across child runs; per-step budget overrides
- [x] [#195](https://github.com/deghosal-2026/hiveplane/issues/195) — M29-07 — Pipeline failure semantics (fail-fast vs. continue-on-error per node) + retry of a failed node
- [x] [#196](https://github.com/deghosal-2026/hiveplane/issues/196) — M29-08 — Pipeline state/observability: parent timeline shows each node's status, cost, and artifacts
- [x] [#197](https://github.com/deghosal-2026/hiveplane/issues/197) — M29-09 — Tests: linear, fan-out/fan-in, handoff validation, gate blocking, budget aggregation, node retry

**Test ticket:** [x] [#198](https://github.com/deghosal-2026/hiveplane/issues/198) — Test cases for Multi-Agent Pipelines

**Deliverables:**
- `hiveplane.pipelines` package (spec, engine, handoff, budget)
- Pipeline submission API + CLI (`hiveplane pipelines submit|status`)
- `docs/design/orchestration-design.md`

**Acceptance criteria:**
- [x] A 3-node pipeline runs end-to-end with a validated handoff between each step
- [x] A cycle in a pipeline spec is rejected at validation
- [x] A per-step approval gate pauses the pipeline and resumes after approval
- [x] Fan-out over N items produces N child runs and a reduced aggregate
- [x] Cumulative pipeline spend never exceeds the pipeline budget (verified by test)
- [x] A failed node honors its fail-fast/continue policy and can be retried

**Done when:** a multi-agent DAG runs end-to-end with per-step gates, handoffs, and an enforced pipeline budget.

> **Status:** M29 complete. `hiveplane.pipelines` (spec DSL, handoff layer, store,
> engine, executor adapters), migration `0008` (`pipeline_run_headers` +
> `pipeline_node_runs`), `Run.pipeline_origin` / `WorkloadSpec.io`, the pipeline
> API, and `hiveplane pipelines` CLI landed. All tests pass with a database;
> coverage 95% total, ruff and mypy strict clean. Issues #189–#198 to be closed at
> the exit gate.

**Dependencies:** M25 (pipeline model); v0.1.0 run lifecycle, policy/approvals, budget.

**Notes / risks:** handoff schema mismatches are the most likely failure — validate at both producer and consumer. Keep pipeline state derivable from child run state where possible to avoid dual sources of truth.

## M30 — Smart Task Router & Agent-as-Tool

**Objective:** Route a plain-language task to the best certified workload with a cheap classifier, and let one certified workload be called as a tool inside another run with budget/policy/certification propagation. Add A2A interop as a stretch.

**Work items:**

- [ ] [#199](https://github.com/deghosal-2026/hiveplane/issues/199) — M30-01 — Task router: cheap-model classifier maps a task to a candidate workload/pipeline from the certified catalog
- [ ] [#200](https://github.com/deghosal-2026/hiveplane/issues/200) — M30-02 — Router guardrails: only certified workloads are eligible; confidence threshold + explicit fallback/refusal
- [ ] [#201](https://github.com/deghosal-2026/hiveplane/issues/201) — M30-03 — Router explanations: why this workload was chosen (candidate scores recorded in the run story)
- [ ] [#202](https://github.com/deghosal-2026/hiveplane/issues/202) — M30-04 — Agent-as-tool: expose a certified workload as a callable tool to other workloads
- [ ] [#203](https://github.com/deghosal-2026/hiveplane/issues/203) — M30-05 — Propagation: nested agent-as-tool calls inherit budget, policy, and certification checks; results are attributed
- [ ] [#204](https://github.com/deghosal-2026/hiveplane/issues/204) — M30-06 — Recursion/depth limits and cycle detection for agent-as-tool calls
- [ ] [#205](https://github.com/deghosal-2026/hiveplane/issues/205) — M30-07 — A2A interop (stretch): Agent2Agent protocol adapter for cross-plane calls behind a feature flag
- [ ] [#206](https://github.com/deghosal-2026/hiveplane/issues/206) — M30-08 — Tests: routing accuracy on a labeled set, refusal on low confidence, nested-call propagation, depth limits

**Test ticket:** [#207](https://github.com/deghosal-2026/hiveplane/issues/207) — Test cases for Smart Task Router & Agent-as-Tool

**Deliverables:**
- `hiveplane.router` package + `POST /route` API
- Agent-as-tool registry integration
- `docs/design/orchestration-design.md` and `docs/design/orchestration-design.md`

**Acceptance criteria:**
- [ ] A plain-language task routes to the expected workload on a labeled test set above an agreed threshold
- [ ] An uncertified workload is never a routing candidate
- [ ] Low-confidence tasks are refused with a reason, not guessed
- [ ] An agent-as-tool call carries budget/policy/certification context and is blocked when the nested workload is uncertified
- [ ] Recursive agent-as-tool calls beyond the depth limit are rejected

**Done when:** one endpoint can take a natural task and route it to the fleet, and agents can call certified agents as tools safely.

**Dependencies:** M29; M32 (certification gate must be authoritative).

**Notes / risks:** the router must be conservative — a wrong route that bypasses the right specialist is worse than a refusal. A2A is explicitly stretch; do not let it block M30.

## Exit Gate (M29, M30)

- [x] All tests in the system pass: `pytest`
- [x] Code coverage total ≥ 95%
- [x] Ruff clean
- [x] Mypy strict clean
- [x] All relevant docs updated (pipeline design, router design, user guide)
- [ ] All M29–M30 issues done and closed (M30 pending)
- [ ] Commit and push changes (M29 committed; phase gate completes with M30)

## See Also

- [PRD 05 Features](../../prd/05-features.md) — Multi-Agent Orchestration theme
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillar B
- [v0.2.0 index](wbs-v0.2.0-index.md)
