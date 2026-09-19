# WBS v0.1.0 — Part 12: Field Test

**Milestone:** M23 · **Issues:** #56-#59, #92-#133 (45 open) · **Phases:** P0-P4

## Goal

Run three real, LLM-backed agents through the certified control loop and produce evidence for every v0.1.0 release gate.

> **Re-planning note (2026-09-19).** The original M23 (#56-#59) assumed the WBS had shipped LLM integration, real agent execution in certification, sandbox cap enforcement, and durable resume. A PRD/design-vs-code audit found none of these exist: agents are canned stubs, certification uses a `ReferenceExecutor` stand-in, resource caps are not wired into the adapter path, and nothing reconnects runs after a control-plane restart. A second deep review found three more architectural gaps: `WorkerContext` has no LLM invocation seam (#115), `ToolGateway` doesn't execute tools — the agent fabricates the output (#116), and the certification `TaskExecutor` protocol is disconnected from `RawWorkerAdapter` (#117). M23 was therefore re-planned into five phases; design docs and enabling implementations (P0-P2) precede the docker test track (P3) and the field test itself (P4).

---

## Phases

### P0 — Design & Planning (spec-first)

**Issues:** [#132](https://github.com/deghosal-2026/hiveplane/issues/132) · [#133](https://github.com/deghosal-2026/hiveplane/issues/133) · [#104](https://github.com/deghosal-2026/hiveplane/issues/104) · [#105](https://github.com/deghosal-2026/hiveplane/issues/105) · [#106](https://github.com/deghosal-2026/hiveplane/issues/106) · [#92](https://github.com/deghosal-2026/hiveplane/issues/92) · [#97](https://github.com/deghosal-2026/hiveplane/issues/97) · [#114](https://github.com/deghosal-2026/hiveplane/issues/114)

- [ ] #132 — **Update PRD for new v0.1.0 scope** (LLM, agent seams, persistence, durability, auto-migration, approval re-dispatch — add to features, success-metrics, roadmap, security-baseline)
- [ ] #133 — **Update existing design docs for new functionalities + fix inaccuracies** (runtime-adapter, certification-pipeline, execution-sandbox, execution-path, state-store, result-fanout — add Implementation Status / Prerequisites blocks; document new seams; fix false claims)
- [ ] #104 — Design: LLM provider integration & model-identity enforcement (local Ollama/OMLX + cloud OpenAI + fake for CI; runtime model identity; pricing; telemetry)
- [ ] #105 — Design: certification must execute the real agent (adapter-backed benchmark executor)
- [ ] #106 — Design: durable runs — startup recovery & restart-resume
- [ ] #92 — Docker test plan (image build, per-layer test matrix, dummy data, LLM strategy)
- [ ] #97 — Field test plan (phases → S1-S9/A1-A20, LLM matrix, repeatability)
- [ ] #114 — Update WBS index + field-test docs for added scope

**Exit:** PRD and design docs accurately document the new v0.1.0 scope; designs approved and reviewed against PRD T11/DD-10; both test plans committed; WBS self-consistent. **#132 and #133 are the first items to complete — they unblock all implementation.**

### P1 — LLM & Agent Enablement

**Issues:** [#107](https://github.com/deghosal-2026/hiveplane/issues/107) · [#115](https://github.com/deghosal-2026/hiveplane/issues/115) · [#116](https://github.com/deghosal-2026/hiveplane/issues/116) · [#108](https://github.com/deghosal-2026/hiveplane/issues/108) · [#112](https://github.com/deghosal-2026/hiveplane/issues/112) · [#113](https://github.com/deghosal-2026/hiveplane/issues/113)

- [ ] #107 — Implement LLM provider integration (local + cloud + fake for CI)
- [ ] #115 — LLM seam in WorkerContext (agent contract change: `ctx.complete()` routes through boundary, auto-reports usage, verifies model identity, emits model-call spans)
- [ ] #116 — Tool execution layer (mock dispatcher with fixtures: agents get real tool data instead of fabricating outputs)
- [ ] #108 — Real LLM-backed example agents (repo-agent, docs-agent, incident-agent — incl. the missing `examples/incident_agent.py`)
- [ ] #112 — Container image & compose readiness for agent runs (examples in image, langgraph extra, postgres run store)
- [ ] #113 — Align example manifests with v0.1.0 scope (teams/jira fan-out → slack/webhook; annotate inert triggers/mcp_servers)
- [ ] #118 — Wire app factory to persistence layer + auto-migration + compose adapter env (critical: control plane doesn't work in Docker today)
- [ ] #119 — Fix `hiveplane init` scaffolding (working entrypoint + agent + corpus)
- [ ] #120 — Environment config profiles (CI fake / local Ollama / cloud OpenAI)
- [ ] #125 — Persist attestation signing keypair across restarts (critical: all attestations break on restart)
- [ ] #126 — Persist certification store (attestations + cert records) (critical: certified workloads become uncertified on restart)
- [ ] #127 — CLI certify must accept and pin model_identity (critical: model pinning impossible from CLI)
- [ ] #128 — Persist budget store (per-day aggregate enforcement across restarts) (critical: daily budget resets on restart)

**Exit:** all three workloads register, load in the built image, and run end-to-end against local + fake providers; agents call the LLM through the boundary, get real tool data, and report real usage and model identity.

### P2 — Control-Loop Hardening

**Issues:** [#98](https://github.com/deghosal-2026/hiveplane/issues/98) · [#117](https://github.com/deghosal-2026/hiveplane/issues/117) · [#109](https://github.com/deghosal-2026/hiveplane/issues/109) · [#110](https://github.com/deghosal-2026/hiveplane/issues/110) · [#111](https://github.com/deghosal-2026/hiveplane/issues/111)

- [ ] #98 — Field test corpus (docs-agent + incident-agent corpora; ≥5 deterministic tasks each; negative tasks)
- [ ] #117 — AdapterTaskExecutor bridge (connect certification `TaskExecutor` to `RawWorkerAdapter.submit()` — corpus tasks run as real agent runs)
- [ ] #109 — Adapter-backed benchmark certification (execute real agents against corpora; `EXECUTOR=adapter`; depends on #117)
- [ ] #110 — Wire sandbox resource caps into the adapter execution path (RLIMIT_AS/CPU + wall-clock watchdog)
- [ ] #111 — Implement startup recovery & durable resume (S8 unblocked)
- [ ] #122 — Durable LangGraph checkpoint (replace InMemorySaver)
- [ ] #123 — Corpus-fixture coupling spec (ensure certification is meaningful)
- [ ] #124 — Trace story renders model-call spans
- [ ] #129 — Approval flow must re-dispatch the escalated tool call on resume
- [ ] #130 — Real readiness probe (/readyz checks stores, migrations, adapter)
- [ ] #131 — Tool seeding script and CLI (register tools referenced by workloads)

**Exit:** all three workloads certify at production threshold with the real agent in the loop; caps demonstrably kill seeded hogs; a paused run resumes after control-plane restart.

### P3 — Docker Test Track

**Issues:** [#93](https://github.com/deghosal-2026/hiveplane/issues/93) · [#94](https://github.com/deghosal-2026/hiveplane/issues/94) · [#95](https://github.com/deghosal-2026/hiveplane/issues/95) · [#96](https://github.com/deghosal-2026/hiveplane/issues/96) · [#59](https://github.com/deghosal-2026/hiveplane/issues/59)

- [ ] #93 — Docker test cases, dummy data, and setup (layered `tests/docker/`, `deploy/testdata/`, Ollama + fake-webhook compose profile)
- [ ] #94 — Docker test execution (one-command runner, structured results, CI exit codes)
- [ ] #95 — Scenario screenshots for user guide (`docs/field-test/v0.1.0/screenshots/<scenario-id>/<step>-<name>.png`, regenerable)
- [ ] #96 — Docker test results summary (`DOCKER_TEST_REPORT.md`)
- [ ] #59 — Nightly simulator + CI regression harness (keeps evidence fresh)

**Exit:** one command runs the full Docker test pass; screenshots regenerate deterministically; nightly CI green.

### P4 — Field Test & Release Evidence

**Issues:** [#99](https://github.com/deghosal-2026/hiveplane/issues/99) · [#56](https://github.com/deghosal-2026/hiveplane/issues/56) · [#57](https://github.com/deghosal-2026/hiveplane/issues/57) · [#58](https://github.com/deghosal-2026/hiveplane/issues/58) · [#100](https://github.com/deghosal-2026/hiveplane/issues/100) · [#101](https://github.com/deghosal-2026/hiveplane/issues/101) · [#102](https://github.com/deghosal-2026/hiveplane/issues/102)

- [ ] #99 — Field test setup (seed scripts, env wiring, one-command bring-up)
- [ ] #56 — Define and register three real workloads
- [ ] #57 — Certification and governance field scenarios
- [ ] #58 — Lifecycle, intervention, fan-out, and report
- [ ] #100 — Field test execution (S1-S9 with evidence: run IDs, attestations, logs, screenshots)
- [ ] #101 — Field test report (populate `FIELD_TEST_REPORT.md`; A1-A20 evidence)
- [ ] #102 — Field test learnings and takeaways

**Exit:** every scenario reproduces deterministically; `FIELD_TEST_REPORT.md` published with release-gate evidence.

---

## Scenarios

| # | Scenario | Evidence required | Enabled by |
|---|----------|-------------------|------------|
| S1 | Certify all three agents via benchmark | attestations signed + verified on read | P2 (#109), P1 (#107, #108) |
| S2 | Uncertified agent attempts production | refused with specific error | existing admission gate |
| S3 | Model-swap (cert on A, run on B) | blocked | P0/P1 (#104, #107) |
| S4 | Seeded manifest change regresses | promotion gate blocks, diff produced | existing workflow (#105 review) |
| S5 | Over-budget run | blocked/escalated | existing budget service (+#107 pricing) |
| S6 | Destructive tool call | sandbox caps + approval required | P2 (#110) |
| S7 | Large tool output | shaped before reaching agent | existing shaping pipeline |
| S8 | Pause → restart control plane → resume | context intact | P2 (#111) |
| S9 | Result fan-out | delivered to Slack + webhook | existing fan-out (+#93 fake receivers) |

**Done when:** every scenario reproduces deterministically, `FIELD_TEST_REPORT.md` is published, and the nightly simulator + CI harness keep the evidence fresh.

---

## Dependencies

```
P0 (Design & Planning)
  ├─> P1 (LLM & Agent Enablement)
  │       └─> P2 (Control-Loop Hardening)
  │               └─> P3 (Docker Test Track)
  │                       └─> P4 (Field Test & Release Evidence) ─> Part 13 (Release)
  └─ #114 feeds every phase (WBS/docs stay in sync)
```

- #104 → #107 → #115 (LLM seam) → #108 (real agents) → #117 (bridge) → #109 (real certification)
- #116 (tool executor) unblocks #108 and #117 — agents need real tool data to reason about
- #106 → #111 (durable design → recovery)
- #112, #113 run in parallel with the LLM track
- #92/#97 (plans) are written in P0 and executed in P3/P4

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
