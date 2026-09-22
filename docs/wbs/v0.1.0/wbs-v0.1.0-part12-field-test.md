# WBS v0.1.0 — Part 12: Field Test

**Milestone:** M23 · **Issues:** #56-#59, #92-#144 (34 closed; 19 open) · **Phases:** P0-P7

## Goal

Run three real, LLM-backed agents through the certified control loop and produce evidence for every v0.1.0 release gate.

> **Re-planning note (2026-09-19, updated 2026-09-21).** The original M23 (#56-#59) assumed the WBS had shipped LLM integration, real agent execution in certification, sandbox cap enforcement, and durable resume. A PRD/design-vs-code audit found none of these exist: agents are canned stubs, certification uses a `ReferenceExecutor` stand-in, resource caps are not wired into the adapter path, and nothing reconnects runs after a control-plane restart. A second deep review found three more architectural gaps: `WorkerContext` has no LLM invocation seam (#115), `ToolGateway` doesn't execute tools — the agent fabricates the output (#116), and the certification `TaskExecutor` protocol is disconnected from `RawWorkerAdapter` (#117). M23 was therefore re-planned into phases; design docs and enabling implementations (P0-P1) and the real-LLM audit chain (P2a-P2c) precede the remaining control-loop hardening (P2d), the docker test track (P3), and the field test itself (P4). Issues are ordered by dependency below; each row is the next actionable item after the one above it.

---

## Phases

### P0 — Design & Planning (spec-first)

**Issues:** [#132](https://github.com/deghosal-2026/hiveplane/issues/132) · [#133](https://github.com/deghosal-2026/hiveplane/issues/133) · [#104](https://github.com/deghosal-2026/hiveplane/issues/104) · [#105](https://github.com/deghosal-2026/hiveplane/issues/105) · [#106](https://github.com/deghosal-2026/hiveplane/issues/106) · [#92](https://github.com/deghosal-2026/hiveplane/issues/92) · [#97](https://github.com/deghosal-2026/hiveplane/issues/97) · [#114](https://github.com/deghosal-2026/hiveplane/issues/114)

- [x] #132 — **Update PRD for new v0.1.0 scope** (LLM, agent seams, persistence, durability, auto-migration, approval re-dispatch — add to features, success-metrics, roadmap, security-baseline)
- [x] #133 — **Update existing design docs for new functionalities + fix inaccuracies** (runtime-adapter, certification-pipeline, execution-sandbox, execution-path, state-store, result-fanout — add Implementation Status / Prerequisites blocks; document new seams; fix false claims)
- [x] #104 — Design: LLM provider integration & model-identity enforcement (local Ollama/OMLX + cloud OpenAI + fake for CI; runtime model identity; pricing; telemetry)
- [x] #105 — Design: certification must execute the real agent (adapter-backed benchmark executor)
- [x] #106 — Design: durable runs — startup recovery & restart-resume
- [x] #92 — Docker test plan (image build, per-layer test matrix, dummy data, LLM strategy)
- [x] #97 — Field test plan (phases → S1-S9/A1-A20, LLM matrix, repeatability)
- [x] #114 — Update WBS index + field-test docs for added scope

**Status:** Complete. Commits `a1322f5` (#132), `144c1ae` (#133), `bdac16e` (#104/#105/#106), `b77445d` (#92/#97/#114). All P0 issues closed.

**Exit:** PRD and design docs accurately document the new v0.1.0 scope; designs approved and reviewed against PRD T11/DD-10; both test plans committed; WBS self-consistent.

### P1 — LLM & Agent Enablement

**Issues:** [#107](https://github.com/deghosal-2026/hiveplane/issues/107) · [#115](https://github.com/deghosal-2026/hiveplane/issues/115) · [#116](https://github.com/deghosal-2026/hiveplane/issues/116) · [#117](https://github.com/deghosal-2026/hiveplane/issues/117) · [#108](https://github.com/deghosal-2026/hiveplane/issues/108) · [#112](https://github.com/deghosal-2026/hiveplane/issues/112) · [#113](https://github.com/deghosal-2026/hiveplane/issues/113) · [#118](https://github.com/deghosal-2026/hiveplane/issues/118) · [#119](https://github.com/deghosal-2026/hiveplane/issues/119) · [#120](https://github.com/deghosal-2026/hiveplane/issues/120) · [#125](https://github.com/deghosal-2026/hiveplane/issues/125) · [#126](https://github.com/deghosal-2026/hiveplane/issues/126) · [#127](https://github.com/deghosal-2026/hiveplane/issues/127) · [#128](https://github.com/deghosal-2026/hiveplane/issues/128)

- [x] #107 — Implement LLM provider integration (local + cloud + fake for CI)
- [x] #115 — LLM seam in WorkerContext (agent contract change: `ctx.complete()` routes through boundary, auto-reports usage, verifies model identity, emits model-call spans)
- [x] #116 — Tool execution layer (mock dispatcher with fixtures: agents get real tool data instead of fabricating outputs)
- [x] #117 — AdapterTaskExecutor bridge (connect certification `TaskExecutor` to `RawTaskExecutor` — corpus tasks run as real agent runs)
- [x] #108 — Real LLM-backed example agents (repo-agent, docs-agent, incident-agent — incl. the missing `examples/incident_agent.py`)
- [x] #112 — Container image & compose readiness for agent runs (examples in image, langgraph extra, postgres run store)
- [x] #113 — Align example manifests with v0.1.0 scope (teams/jira fan-out → slack/webhook; annotate inert triggers/mcp_servers)
- [x] #118 — Wire app factory to persistence layer + auto-migration (critical: control plane didn't work in Docker before)
- [x] #119 — Fix `hiveplane init` scaffolding (working entrypoint + agent + corpus)
- [x] #120 — Environment config profiles (CI fake / local Ollama / cloud OpenAI)
- [x] #125 — Persist attestation signing keypair across restarts (critical: all attestations break on restart)
- [x] #126 — Persist certification store (attestations + cert records) (critical: certified workloads become uncertified on restart)
- [x] #127 — CLI certify must accept and pin model_identity (critical: model pinning impossible from CLI)
- [x] #128 — Persist budget store (per-day aggregate enforcement across restarts) (critical: daily budget resets on restart)

**Status:** Complete. Commits `c69de85` (#107), `026d9d1` (#108/#112/#113/#115/#116/#117/#118/#120/#125/#126/#128), `f27d014` (#119/#127). All P1 issues closed.

**Exit:** all three workloads register, load in the built image, and run end-to-end against local + fake providers; agents call the LLM through the boundary, get real tool data, and report real usage and model identity.

### P2 — Control-Loop Hardening

> **Real-LLM field-test audit (2026-09-20).** A design-vs-code audit of the LLM provider, adapters, wiring, and certification path found that the three example agents and the provider classes exist but have never run end-to-end through the real app path with a real or replay provider. The audit opened #134–#144 for the untracked gaps. P2 is split into four dependency-ordered sub-phases below: wiring fixes (P2a), corpus/provider reconciliation (P2b), the real certification thesis (P2c), and the remaining control-loop hardening (P2d). Each row is the next actionable item after the one above it.

#### P2a — Wiring fixes (the audit chain)

**Issues:** [#134](https://github.com/deghosal-2026/hiveplane/issues/134) · [#135](https://github.com/deghosal-2026/hiveplane/issues/135) · [#136](https://github.com/deghosal-2026/hiveplane/issues/136) · [#137](https://github.com/deghosal-2026/hiveplane/issues/137) · [#138](https://github.com/deghosal-2026/hiveplane/issues/138) · [#139](https://github.com/deghosal-2026/hiveplane/issues/139) · [#141](https://github.com/deghosal-2026/hiveplane/issues/141) · [#142](https://github.com/deghosal-2026/hiveplane/issues/142) · [#144](https://github.com/deghosal-2026/hiveplane/issues/144)

- [x] #134 — Wire `FixtureToolExecutor` into the production `ToolGateway` (agents get real tool data) — `412a393`
- [x] #135 — Wire `LangGraphAdapter` into the app (`attach_langgraph` + settings selector; docs-agent can run) — `1bab1d3`
- [x] #136 — Wire the configured LLM provider into the adapters at startup (fail fast on misconfig) — `696ad83`
- [x] #137 — Fix `FakeProvider` replay keying (full request, not last user message) and ship replay fixtures (blocks #109) — `20ecfef`
- [x] #138 — Configure `model_aliases` so real-LLM calls don't raise `ModelIdentityMismatchError` — `23dd65d`
- [x] #139 — Price model calls through `CostTable` (`cost_usd` is hardcoded to 0.0) — `e8fa6e0`
- [x] #141 — Implement LLM provider retry/timeout from `ModelSettings` (`max_retries` is a dead setting) — `27a2ffb`
- [x] #142 — Use `ModelSettings.default_model` as fallback when no model identity is bound — `f200405`
- [x] #144 — Fail fast on disabled adapter/certification executor instead of silently no-op'ing — `2af724c`

**Status:** Complete. All P2a issues closed. These precede #109 — real certification is impossible until the agents can actually run end-to-end.

**Exit:** the control plane, on a fresh `docker compose up` with the CI/local profile, executes runs and certifies via the adapter executor; agents receive fixture tool data, the provider is built at startup, replay is keyed per task, and disabled adapters are loud.

#### P2b — Corpus & provider reconciliation

**Issues:** [#98](https://github.com/deghosal-2026/hiveplane/issues/98) · [#123](https://github.com/deghosal-2026/hiveplane/issues/123) · [#140](https://github.com/deghosal-2026/hiveplane/issues/140)

- [x] #98 — Field test corpus (docs-agent + incident-agent corpora; ≥5 deterministic tasks each; negative tasks)
- [x] #123 — Corpus-fixture coupling spec (decides the #137 replay-keying scheme and the #140 corpus/agent reconciliation) → [D20](../../design/corpus-fixture-coupling.md)
- [x] #140 — Reconcile corpora with real agent behavior (incident-agent can't complete; negative tasks require unimplemented actions) — `a5e2a57`

**Status:** Complete. All P2b issues closed.

> ✅ **Contract risk resolved by #109.** `examples/incident_agent.py:43` calls `pagerduty.acknowledge` as `ActionClass.DESTRUCTIVE`. The `PolicyEngine` short-circuits to `ALLOW` in the `SANDBOX` context (`engine.py`, sandbox.allow) **before** the destructive-escalation branch, so the benchmark (which always runs in `SANDBOX`) approves the ack inline — no pause. The `AdapterTaskExecutor` also **auto-approves** any pause it observes (D20), which covers the docs-agent `review_gate` `__interrupt__` and any approval-gated escalation. Deny stays manual (S6 field-test action). Proven by `tests/test_certification_adapter_e2e.py`.

**Exit:** every corpus task is satisfiable by its real agent; a deliberately broken agent provably fails at least one critical task (benchmark is not theater).

#### P2c — Real certification (the thesis)

**Issues:** [#131](https://github.com/deghosal-2026/hiveplane/issues/131) · [#129](https://github.com/deghosal-2026/hiveplane/issues/129) · [#109](https://github.com/deghosal-2026/hiveplane/issues/109)

- [x] #131 — Tool seeding script and CLI (register tools referenced by workloads) — `registry/seeding.py` + `hiveplane tools seed` + `scripts/seed-tools.sh`; every example workload registers after seeding
- [x] #129 — Approval flow must re-dispatch the escalated tool call on resume — `0ead92d` (closed)
- [x] #109 — Adapter-backed benchmark certification (execute real agents against corpora; `EXECUTOR=adapter`) — `86e2e15`. Three workloads certify at production threshold via the real path; a regressed agent fails a critical task. Also adds: `DispatchingAdapter` + `HIVEPLANE_EXECUTION__ADAPTER=auto` (routes raw-worker and langgraph workloads in one stack), benchmark auto-approval in `AdapterTaskExecutor` (D20), per-workload certification executor factory, and a `LangGraphAdapter` fix so the run task reaches the graph under the `task` key (D20) plus a `Command` factory bug fix. Verified by `tests/test_certification_adapter_e2e.py`, `tests/test_benchmark_auto_approval.py`, `tests/test_adapter_dispatch.py`.

**Status:** Complete. All P2c issues closed.

**Exit:** all three workloads certify at production threshold with the real agent in the loop; a deliberately regressed agent (wrong model / bad prompt / injection-fooled) fails at least one critical task.

#### P2d — Remaining control-loop hardening

**Issues:** [#110](https://github.com/deghosal-2026/hiveplane/issues/110) · [#111](https://github.com/deghosal-2026/hiveplane/issues/111) · [#122](https://github.com/deghosal-2026/hiveplane/issues/122) · [#124](https://github.com/deghosal-2026/hiveplane/issues/124) · [#130](https://github.com/deghosal-2026/hiveplane/issues/130)

- [ ] #110 — Wire sandbox resource caps into the adapter execution path (RLIMIT_AS/CPU + wall-clock watchdog) — S6; independent of #109
- [ ] #111 — Implement startup recovery & durable resume (S8 unblocked; depends on closed design #106)
- [ ] #122 — Durable LangGraph checkpoint (replace InMemorySaver) — **after #111**; S8 for docs-agent specifically
- [ ] #124 — Trace story renders model-call spans (observability; independent)
- [ ] #130 — Real readiness probe (`/readyz` checks stores, migrations, adapter; independent; #144 surfaced it as the disabled-adapter surface)

**Exit:** caps demonstrably kill seeded hogs; a paused run resumes after control-plane restart; the trace story shows model calls; `/readyz` is honest.

### P3 — Docker Test Track

**Issues:** [#143](https://github.com/deghosal-2026/hiveplane/issues/143) · [#93](https://github.com/deghosal-2026/hiveplane/issues/93) · [#94](https://github.com/deghosal-2026/hiveplane/issues/94) · [#95](https://github.com/deghosal-2026/hiveplane/issues/95) · [#96](https://github.com/deghosal-2026/hiveplane/issues/96) · [#59](https://github.com/deghosal-2026/hiveplane/issues/59)

- [ ] #143 — Copy `deploy/testdata` into the Docker image (fixtures + replay files missing in container) — **first:** nothing below works without fixtures in-container
- [ ] #93 — Docker test cases, dummy data, and setup (layered `tests/docker/`, `deploy/testdata/`, Ollama + fake-webhook compose profile) — needs #143
- [ ] #94 — Docker test execution (one-command runner, structured results, CI exit codes) — needs #93 + #143
- [ ] #95 — Scenario screenshots for user guide (`docs/field-test/v0.1.0/screenshots/<scenario-id>/<step>-<name>.png`, regenerable) — needs #94
- [ ] #96 — Docker test results summary (`DOCKER_TEST_REPORT.md`) — needs #94
- [ ] #59 — Nightly simulator + CI regression harness (keeps evidence fresh; needs #94) — #103 closed as a duplicate of this

**Exit:** one command runs the full Docker test pass; screenshots regenerate deterministically; nightly CI green.

### P4 — Field Test & Release Evidence

**Issues:** [#99](https://github.com/deghosal-2026/hiveplane/issues/99) · [#121](https://github.com/deghosal-2026/hiveplane/issues/121) · [#56](https://github.com/deghosal-2026/hiveplane/issues/56) · [#57](https://github.com/deghosal-2026/hiveplane/issues/57) · [#58](https://github.com/deghosal-2026/hiveplane/issues/58) · [#100](https://github.com/deghosal-2026/hiveplane/issues/100) · [#101](https://github.com/deghosal-2026/hiveplane/issues/101) · [#102](https://github.com/deghosal-2026/hiveplane/issues/102)

- [ ] #99 — Field test setup (seed scripts, env wiring, one-command bring-up) — needs the P3 stack working
- [ ] #121 — Seeded demo script (register → certify → run → intervene → deliver; deterministic, fake provider) — bring-up helper
- [ ] #56 — Define and register three real workloads — the subjects of the test
- [ ] #57 — Certification and governance field scenarios (S1-S5)
- [ ] #58 — Lifecycle, intervention, fan-out, and report (S5-S9)
- [ ] #100 — Field test execution (S1-S9 with evidence: run IDs, attestations, logs, screenshots) — needs #56, #57, #58
- [ ] #101 — Field test report (populate `FIELD_TEST_REPORT.md`; A1-A20 evidence) — needs #100
- [ ] #102 — Field test learnings and takeaways — needs #100

**Exit:** every scenario reproduces deterministically; `FIELD_TEST_REPORT.md` published with release-gate evidence.

---

## Scenarios

| # | Scenario | Evidence required | Enabled by |
|---|----------|-------------------|------------|
| S1 | Certify all three agents via benchmark | attestations signed + verified on read | P2c (#109), P1 (#107, #108) |
| S2 | Uncertified agent attempts production | refused with specific error | existing admission gate |
| S3 | Model-swap (cert on A, run on B) | blocked | P0/P1 (#104, #107) |
| S4 | Seeded manifest change regresses | promotion gate blocks, diff produced | existing workflow (#105 review) |
| S5 | Over-budget run | blocked/escalated | existing budget service (+#107 pricing) |
| S6 | Destructive tool call | sandbox caps + approval required | P2d (#110), P2c (#129 approve path) |
| S7 | Large tool output | shaped before reaching agent | existing shaping pipeline |
| S8 | Pause → restart control plane → resume | context intact | P2d (#111, #122) |
| S9 | Result fan-out | delivered to Slack + webhook | existing fan-out (+#93 fake receivers) |

**Done when:** every scenario reproduces deterministically, `FIELD_TEST_REPORT.md` is published, and the nightly simulator + CI harness keep the evidence fresh.

---

## Dependencies

```
P0 (Design & Planning)
  └─> P1 (LLM & Agent Enablement)
        └─> P2 (Control-Loop Hardening)
              ├─> P2a (Wiring fixes — the audit chain)        [complete]
              ├─> P2b (Corpus & provider reconciliation)      [complete]
              ├─> P2c (Real certification — the thesis)
              │       #131 ─> #129 ─> #109
              │       (#109 first resolves the incident-agent
              │        destructive-ack contract from P2b)
              └─> P2d (Remaining hardening)
                      #110 (S6 caps)        ─ independent
                      #111 ─> #122 (S8)     ─ recovery then checkpoint
                      #124 (trace story)    ─ independent
                      #130 (/readyz)        ─ independent
                    └─> P3 (Docker Test Track)
                            #143 ─> #93 ─> #94 ─> {#95, #96, #59}
                              └─> P4 (Field Test & Release Evidence)
                                    #99 ─> #121 ─> #56 ─> #57 ─> #58
                                          └─> #100 ─> {#101, #102}
                                      └─> Part 13 (Release)
  └─ #114 feeds every phase (WBS/docs stay in sync)
```

### Edge-level dependency chains

- **LLM track:** #104 → #107 → #115 (LLM seam) → #108 (real agents) → #117 (bridge) → #109 (real certification)
- **Tool data:** #116 (tool executor) unblocks #108 and #117 — agents need real tool data to reason about; #134 wires it; #131 seeds the registry so admission accepts tool calls
- **Approval path:** #129 (re-dispatch on resume) unblocks the incident-agent destructive `ack` and S6's approve-then-complete
- **Durability:** #106 (design) → #111 (recovery) → #122 (durable LangGraph checkpoint, docs-agent S8)
- **Containers:** #112, #113 run in parallel with the LLM track; #143 ships the fixtures into the image
- **Test plans:** #92/#97 are written in P0 and executed in P3/P4
- **Real-LLM audit chain (2026-09-20):** #123 (coupling spec) decides the #137 replay-keying scheme and the #140 corpus/agent reconciliation → #134 + #135 + #136 + #138 + #139 + #141/#142 + #144 (all closed, P2a/P2b) precede #109 — real certification is impossible until the agents can actually run end-to-end. #143 (P3) ships the fixtures into the image.
- **#109 (closed):** destructive `ack` is allowed inline in the `SANDBOX` context (policy `sandbox.allow` precedes the escalation branch), and the executor auto-approves any pause (D20). #109 also adds `DispatchingAdapter` + `HIVEPLANE_EXECUTION__ADAPTER=auto` so raw-worker and langgraph workloads run in one stack, a per-workload certification executor factory, and the `LangGraphAdapter` `task`-key / `Command`-factory fixes.

---

## Explicitly Deferred (do not re-litigate in M23)

Container sandbox backend (Docker/gVisor network namespaces), trigger ingestion/auto-start, MCP transport (mcp-fabric), drift detector + auto-quarantine, agent health service, `rubric`/`schema_match` corpus checks, Teams/Jira fan-out transports — all v0.2+ per the PRD roadmap and execution-path design.

## Exit Gate (M23)

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] Update all relevant docs affected by this milestone
- [ ] Verify all issues in this milestone are done
- [ ] Close all completed issues
- [ ] Commit and push changes

## See Also

- [Field test plan](../../field-test/v0.1.0/field-test-plan.md)
- [Field test report](../../field-test/v0.1.0/FIELD_TEST_REPORT.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)
