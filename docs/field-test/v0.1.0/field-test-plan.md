# HivePlane v0.1.0 — Field Test Plan

> Status: **executing** (M23 P4). Updated 2026-09-25: **Tier 1 was rewired to the
> deterministic exectrace agents** (`support-agent` = agent-raw via raw-worker shim,
> `eval-judge` = judge graph via LangGraph shim) plus the negative fixtures
> (`uncertified-agent`, `model-swap-agent`, `regressed-agent`); the heavyweight trio
> (`repo-agent`, `docs-agent`, `incident-agent`) was demoted to Tier 3 (see the report's
> Learnings for why). Partial results: S1/S2/S4/S9 pass, S3 fail (real finding), S5/S7
> blocked by design, S6/S8 incomplete — see [FIELD_TEST_REPORT.md](FIELD_TEST_REPORT.md).
> The Tier 1 model is `Qwen3-4B-Instruct-2507-4bit` (canonical
> `omlx/qwen3-4b-instruct-2507/4bit`). The Tier 2 platform-coverage inventory below is
> optional and deferred (see "Scope").

## Scope (v0.1.0 field test)

**Required (release gate):** the **Tier 1** workloads (`support-agent`, `eval-judge` — real
downloaded exectrace agents run through HivePlane adapter shims) plus the three negative
fixtures (`uncertified-agent`, `model-swap-agent`, `regressed-agent`), through the certified
control loop, covering scenarios S1–S9 and acceptance criteria A1–A20. This is the same
stack the [Docker test plan](docker-test-plan.md) validates at the container layer.

> **2026-09-25 rewire note:** the original Tier 1 (`repo-agent`, `docs-agent`,
> `incident-agent`) was replaced. Those heavyweight standalone apps were not drop-in
> runnable (missing `langgraph.checkpoint.sqlite`, `incident_commander` package layout,
> external `openai` SDK, human-in-the-loop CLI flows) and their model-dependent
> classification made certification nondeterministic. They remain on disk as Tier 3
> references.

**Optional / deferred:** the **Tier 2** platform-coverage inventory (tooltrust, evalforge,
exectrace — 113 agents, 12 frameworks) demonstrates framework-agnostic certification. It is
**not** run through the v0.1.0 field test and **A21 (8/8 frameworks) is not a v0.1.0 release
gate**; the wider certification story is tracked separately. Their scenario packs remain
valid inputs for future certification work.

## Prerequisites (M23) — satisfied

The original plan assumed the WBS shipped LLM integration, real agent execution in certification,
sandbox enforcement, and durable resume. It did not, so M23 was re-planned and these landed in
P0–P3. **All prerequisites below are complete**, so P4 can execute:

- ✅ **LLM provider seam** (#104/#107/#115) and **real agents** (#108)
- ✅ **Tool execution** (#116)
- ✅ **Adapter-backed certification** (#105/#117/#109)
- ✅ **Persistence wiring + auto-migration** (#118/#125/#126/#128)
- ✅ **Sandbox caps** (#110) and **durable resume** (#111)
- ✅ **Corpora** for all three workloads (#98) and **container readiness** (#112)

**This field test is against the real downloaded agents** — `support-agent` (exectrace
`agent-raw`) and `eval-judge` (exectrace `agent-eval-graph`) execute through the real
adapters (`raw-worker` and `langgraph`) on the real stack; their logic is deterministic so
certification measures the control plane, not model drift. The
[Docker test plan](docker-test-plan.md) already validated the container/API/UI layers separately
and is complete; it is not re-run here.

## Objective

Prove the certified control loop with **real downloaded agents**. The two Tier 1 workloads
(`support-agent`, `eval-judge`) run through the full lifecycle: each is registered, certified
against a benchmark, and only then allowed to run in production. The field test exercises
certification, sandbox execution, destructive-tool approval, interrupts, durability, and
fan-out — all the v0.1.0 release gates.

## Agent Inventory

All agents and corpora live under `field_test/` in the HivePlane repository.

### Tier 1 — Primary workloads (certification + governance scenarios)

Two real workloads drive the core certification and governance scenarios (S1–S9), plus three
negative fixtures for the admission/regression gates. Each Tier 1 agent is a real downloaded
exectrace agent wired to HivePlane through a thin shim under `field_test/shims/`.

| Workload | Source | Adapter | Shim | What it does | Stresses |
|----------|--------|---------|------|--------------|----------|
| `support-agent` | exectrace `agent-raw` | raw-worker | `field_test/shims/support_agent.py` | Answers KB questions; escalates unknowns through the approval-gated destructive `pagerduty.acknowledge` | destructive tool + approval/re-dispatch, read-first action audit, certification at production threshold |
| `eval-judge` | exectrace `agent-eval-graph` | langgraph | `field_test/shims/eval_judge.py` | Deterministic judge loop: run tests → judge → escalate → human-review `interrupt()` → verdict | LangGraph adapter, interrupt → paused run, durable checkpoint resume, action audit |

Negative fixtures (same stack, deliberately wrong):

| Workload | Purpose | Shim |
|----------|---------|------|
| `uncertified-agent` | S2 — must be refused production admission (never certified) | support-agent |
| `model-swap-agent` | S3 — pinned to one model identity; running on another must be blocked | support-agent |
| `regressed-agent` | S4 — naive agent that must fail certification (no read-first, no escalation) | `field_test/shims/regressed_agent.py` |

### Tier 2 — Platform coverage agents (certification across frameworks)

These agents demonstrate that HivePlane certifies and governs agents built on diverse agentic
frameworks — not just our three primary workloads. Each runs through `raw-worker` (thin `run()`
wrapper) or `langgraph` (native). They come from three proven field-test projects that already
validated them against real models.

#### agent-tooltrust (83 agents, 10 frameworks)

Source: `field_test/agents/proven/tooltrust/` — 12 framework shims (`*.py`), `agents.yaml`
(83-agent roster), `scenarios.yaml` (30 decision scenarios). Upstream repos vendored under
`vendor/` (gitignored, re-cloned via `download_field_agents.sh`).

| Framework | Agents | Source repo (vendored) | Models validated |
|-----------|--------|------------------------|------------------|
| LangGraph | 6 | `langchain-ai/langgraph` | Qwen3-4B-Instruct-2507, gpt-oss-20b |
| PydanticAI | 10 | `pydantic/pydantic-ai` | Qwen3-4B-Instruct-2507, gpt-oss-20b |
| CrewAI | 10 | `crewAIInc/crewAI-examples` | Qwen3-4B-Instruct-2507, gpt-oss-20b, glm-5 |
| OpenAI Agents SDK | 8 | `openai/openai-agents-python` | Qwen3-4B-Instruct-2507, gpt-oss-20b |
| AutoGen | 8 | `ag2ai/ag2` | Qwen3-4B-Instruct-2507, gpt-oss-20b, glm-5 |
| smolagents | 10 | `_awesome-quickstart` + `DeepSearchAgents` + `smolcc` | Qwen3-4B-Instruct-2507 |
| LlamaIndex | 8 | `run-llama/llama_index` (sparse checkout) | Qwen3-4B-Instruct-2507, gpt-oss-20b, glm-5 |
| Google ADK | 8 | `google/adk-python` + `sokart/adk-walkthrough` | Qwen3-4B-Instruct-2507, gpt-oss-20b, glm-5 |
| SWE-bench | 5 | in-repo fixture (`swe_bench_tasks.yaml`) | deterministic (no LLM) |
| ToolTrust MCP | 10 | in-repo (`agent_tooltrust` package) | deterministic (no LLM) |

Scenarios: 30 tool-authorization decisions (allow/audit/escalate/deny) across tool × environment ×
data-class matrix. Plan A: 83/83 (100%). Plan B: 116/123 (94%).

#### agent-eval-forge (19 adapters, 8 frameworks)

Source: `field_test/agents/proven/evalforge/` — 19 wrapper shims + 19 JSON configs, 11 scenario
packs. Upstream repos vendored under `agents/` (gitignored, re-cloned via `list.txt`).

| Framework | Adapters | Model validated | Pass rate |
|-----------|---------|------------------|-----------|
| Google ADK | 3 (`adk-official`, `adk-qs`, `adk-sokart`) | gpt-4o-mini | ~97% post-fix |
| AutoGen | 2 (`ag-official`, `ag-azure`) | gpt-4o-mini | 100% |
| CrewAI | 3 (`crew-examples`, `crew-qs`, `crew-quickstarts`) | gpt-4o-mini | ~97% post-fix |
| LangGraph | 2 (`lg-official`, `lg-azure`) | gpt-4o-mini | 100% |
| LlamaIndex | 2 (`li-official`, `li-azure`) | gpt-4o-mini | ~97% post-fix |
| OpenAI Agents | 2 (`oa-official`, `oa-azure`) | gpt-4o-mini | 100% |
| PydanticAI | 2 (`pai-official`, `pai-azure`) | gpt-4o-mini | ~97% post-fix |
| smolagents | 3 (`sm-deepsearch`, `sm-qs`, `sm-smolcc`) | gpt-4o-mini | ~97% post-fix |

Scenarios: 7 tool-calling scenarios per adapter (weather, calculator, time, no-tool-needed,
disallowed-tool, multi-step) = 133 total. 11 scenario packs under `scenarios/`.

#### agent-exec-trace (4 validated + 6 reference)

Source: `field_test/agents/proven/exectrace/` — full agent code on disk.

| Agent | Framework | Validated | Model | Task |
|-------|-----------|-----------|-------|------|
| `agent-raw` | Raw Python (custom) | ✅ V1+V2 | MLX Qwen 3.5-4B | Support ticket triage |
| `agent-pydantic` | PydanticAI v2 | ✅ V1+V2 | MLX Qwen 2.5-1.5B | Weather lookup |
| `agent-weather` | PydanticAI v1 | ✅ V2 | MLX Qwen 2.5-1.5B | Weather lookup (v1 shim) |
| `request-triage` | LangGraph | ✅ V1+V2 | mock tools | Request triage state machine |
| `agent-mcp` | LangGraph + MCP | reference | — | ReAct agent with MCP tools |
| `agent-crew` | CrewAI | reference | — | 16 example crews |
| `agent-react` | LangGraph ReAct | reference | — | ReAct template |
| `agent-chatbot` | LangGraph | reference | — | Chatbot |
| `agent-eval-graph` | LangGraph | reference | — | Judge graph |
| `agent-github` | PydanticAI v1 | blocked | — | GitHub agent (v1 API) |

### Tier 3 — Local source agents (platform-diversity references)

Seven additional agents adapted from our own repos, demonstrating frameworks not covered by the
proven sets.

| Agent | Source repo | Platforms exercised |
|-------|-------------|---------------------|
| `repo_agent` | ai-code-guardian | Custom/rules |
| `docs_agent` | release-narrator | LangGraph + interrupt gates |
| `incident_agent` | oncall-rag + ai-loopguard + ai-incident-commander | Custom + escalation gates |
| `self_edit_agent` | agent-self-edit | Custom loop (no framework) |
| `cauterule` | CauterRule | CrewAI + PydanticAI + MCP |
| `planner_critic` | planner-critic-engine | AutoGen + OAI Agents SDK + PydanticAI + MCP |
| `mcplex` | mcplex | MCP-native server |

### Platform coverage summary

12 distinct agentic platforms are represented across all tiers:

| Platform | Tier 1 | Tier 2 (tooltrust) | Tier 2 (evalforge) | Tier 2 (exectrace) | Tier 3 |
|----------|--------|--------------------|--------------------|--------------------|--------|
| LangGraph | docs-agent | 6 agents | 2 adapters | 4 agents | — |
| Custom/no-framework | repo-agent, incident-agent | — | — | 1 agent | self_edit, repo_agent |
| CrewAI | — | 10 agents | 3 adapters | 1 reference | cauterule |
| PydanticAI | — | 10 agents | 2 adapters | 2 agents | cauterule, planner_critic |
| OpenAI Agents SDK | — | 8 agents | 2 adapters | — | planner_critic |
| AutoGen | — | 8 agents | 2 adapters | — | planner_critic |
| smolagents | — | 10 agents | 3 adapters | — | — |
| LlamaIndex | — | 8 agents | 2 adapters | — | — |
| Google ADK | — | 8 agents | 3 adapters | — | — |
| MCP-native | — | 10 agents | — | 1 reference | mcplex, cauterule |
| Raw Python | — | — | — | 1 agent | — |
| SWE-bench | — | 5 agents | — | — | — |

## Corpora and Scenario Packs

### HivePlane benchmark corpora (Tier 1 certification)

Each Tier 1 workload has a HivePlane-format corpus with `exact_match` and `action_audit`
checks, authored per the [D20 corpus-fixture coupling spec](../../design/corpus-fixture-coupling.md).
Corpora are resolved from each manifest's `benchmark_corpus` relative to
`HIVEPLANE_CERTIFICATION__CORPORA_DIR` (**now `field_test/`** for the field-test profile —
set in `.env.local`, `docker-compose.yml`, and baked into the image via the `Dockerfile`).

| Corpus | Location | Version | Tasks | Check types | Critical negs |
|--------|----------|---------|-------|-------------|---------------|
| `support-agent-corpus` | `field_test/corpora/support-agent/hiveplane-corpus.yaml` | v1 | 5 (4 pos + 1 neg) | `exact_match` (status/account_tier), `action_audit` (required: `mcp.github.read_issue`; forbidden: `create_pr`, `delete_repo`) | 1 |
| `eval-judge-corpus` | `field_test/corpora/eval-judge/hiveplane-corpus.yaml` | v1 | 4 (3 pos + 1 neg) | `exact_match` (verdict: PASS/FAIL/HUMAN:True), `action_audit` (required: `read_issue`; forbidden: `create_pr`, `delete_repo`) | 1 |
| `regressed-agent-corpus` | `field_test/corpora/regressed-agent/hiveplane-corpus.yaml` | v1 | 5 (mirror of support-agent corpus) | same as support-agent — the naive agent must fail it | 1 |

Because both Tier 1 agents are deterministic, every task chains
`task.input → agent logic → stable output → check` with no model in the loop; the escalation
task (`support-agent` pos-004) additionally chains the full governance path
(read → escalate → benchmark auto-approve → re-dispatch → complete).

### Source-repo corpora (Tier 1 agent fixtures)

| Corpus | Location | Contents |
|--------|----------|----------|
| repo-agent | `field_test/corpora/repo-agent/` | PR diff fixtures (`pr_diff_ai.txt`, `pr_diff_human.txt`), policy fixtures (`guardian-policy-{valid,invalid}.yml`) from ai-code-guardian |
| docs-agent | `field_test/corpora/docs-agent/` | 10 repos of cached GitHub data (angular, react, node, next.js, vscode, docker-compose, prometheus, grafana, kubernetes, terraform) from release-narrator |
| incident-agent | `field_test/corpora/incident-agent/` | Runbook corpus (google-sre, grafana, kubernetes, public-runbooks) + `eval.json` (14 Q&A pairs, MRR + Recall@5) from oncall-rag |

### Proven-agent scenario packs (Tier 2 certification)

These scenario packs ship with the proven agents and are already validated against real models.
To use them in HivePlane certification, they are wrapped as HivePlane `corpus.yaml` tasks (part
of the #109 adapter-backed certification work).

| Scenario pack | Location | Format | Scenarios | What they test |
|---------------|----------|--------|-----------|----------------|
| tooltrust | `field_test/agents/proven/tooltrust/scenarios.yaml` | YAML (tool × env × data_class → expected decision) | 30 | Tool-authorization decisions: allow/audit/escalate/deny |
| evalforge | `field_test/agents/proven/evalforge/scenarios/*.yaml` | YAML (11 framework packs) | 133 (7×19) | Tool-calling correctness: weather, calculator, time, no-tool, disallowed, multi-step |
| exectrace | `field_test/agents/proven/exectrace/request-triage/seeds.py` + `fixtures/*.json` | Python seeds + JSON traces | 3 (normal/loop/high-cost) | Execution traces: support triage, weather, request triage |

## Test Setup

### Directory structure

```
field_test/
├── workloads/                  ← Tier 1 + negative-fixture manifests (registered by setup)
│   ├── support-agent.yaml      (raw-worker → shims/support_agent)
│   ├── eval-judge.yaml         (langgraph → shims/eval_judge)
│   ├── uncertified-agent.yaml  (S2 fixture)
│   ├── model-swap-agent.yaml   (S3 fixture)
│   └── regressed-agent.yaml    (S4 fixture)
├── shims/                      ← HivePlane adapter shims over the real agents
│   ├── support_agent.py       (agent-raw → run(task, ctx))
│   ├── eval_judge.py          (judge graph → LangGraph with durable checkpointer)
│   └── regressed_agent.py     (naive S4 agent)
├── corpora/
│   ├── support-agent/hiveplane-corpus.yaml
│   ├── eval-judge/hiveplane-corpus.yaml
│   ├── regressed-agent/hiveplane-corpus.yaml
│   ├── repo-agent/             (Tier 3 fixtures — release-narrator data)
│   ├── docs-agent/             (Tier 3 fixtures)
│   └── incident-agent/         (Tier 3 fixtures)
├── agents/
│   ├── proven/exectrace/
│   │   ├── agent-raw/          ← Tier 1: real support agent (copied into the image)
│   │   └── agent-eval-graph/   ← Tier 1: real judge graph (copied into the image)
│   ├── repo_agent/             ← Tier 3: ai-code-guardian
│   ├── docs_agent/             ← Tier 3: release-narrator
│   ├── incident_agent/         ← Tier 3: oncall-rag + loopguard + commander
│   └── proven/{tooltrust,evalforge}/  (Tier 2 — gitignored vendor data)
└── v0.1.0/
    ├── docker/                  (container-layer evidence — complete)
    └── results/                 (REAL-AGENT field-test evidence, S1–S9 — P4)
```

### Vendored repos (gitignored, re-cloned on demand)

| Source | Vendor dir | Size | Re-clone command |
|--------|-----------|------|------------------|
| tooltrust (12 upstream repos) | `proven/tooltrust/vendor/` | 463 MB | `cd proven/tooltrust && bash download_field_agents.sh` |
| evalforge (13 upstream repos) | `proven/evalforge/agents/` | 1.2 GB | `cd proven/evalforge && bash setup.sh` (or clone from `list.txt`) |
| exectrace (4 reference agents) | `proven/exectrace/{agent-crew,agent-chatbot,agent-react,agent-mcp}/` | 36 MB | copy from agent-exec-trace repo |

## Phases

**Evidence:** every scenario captures raw evidence (run ids, events, usage, attestations, command
output, logs) into **`field_test/v0.1.0/results/`** (see [Reporting](#reporting)). The concise
narrative is written to `FIELD_TEST_REPORT.md`.

**API address:** the Docker stack serves the API on `http://localhost:8100`
(`HIVEPLANE_API_URL`), the UI on `http://localhost:3001`, and OMLX on host port 8000. Pass
`--api-url http://localhost:8100` when a CLI command defaults to `:8000`.

### Phase 1 — Baseline Validation

- [x] Docker Compose stack starts with one command (`scripts/field-test.sh`; also `docker compose --env-file .env.local --profile local --profile test up -d`) — **A17 pass**
- [ ] `hiveplane init` scaffolds a working project in < 5 minutes — **not exercised (A18 open)**
- [x] Registry, state store, telemetry pipeline, and certification engine are reachable (verified via `/readyz` + S1)
- [x] All Tier 1 workloads register (`field_test/workloads/{support-agent,eval-judge}.yaml` via `scripts/field-test-setup.sh`)
- [ ] `hiveplane register <manifest> --dry-run` reports what would be enforced — **not exercised**
- [x] MCP Tool Registry is seeded with the tools referenced by each workload (`scripts/seed-tools.sh field_test/workloads`)
- [x] Model identity resolves via `HIVEPLANE_MODEL__MODEL_ALIASES` (no `ModelIdentityMismatchError` at run/cert time)
- [ ] _Optional:_ vendored Tier 2 repos re-cloned (deferred — see Scope)

### Phase 2 — Certification (the thesis)

The lifecycle is **staging → provisional → production → certified** (DD-09). A production
certification is *deferred* until the workload has survived `min_production_runs_survived`
production runs while provisional. For the field test this gate is set to `0`
(`HIVEPLANE_CERTIFICATION__PRODUCTION__MIN_PRODUCTION_RUNS_SURVIVED=0` in `.env.local`) so the loop
completes in one run; the benchmark thresholds themselves (`0.90` pass rate, zero critical
failures) are **not** relaxed.

- [x] `support-agent` staging → `provisional`; production → `certified` (**S1 pass**)
- [x] `eval-judge` staging → `provisional`; production → `certified` (**S1 pass**, 100% pass rate both contexts)
- [x] Each certification produces a **signed attestation** (benchmark version, model, eval results, timestamp, environment, signer) — evidence: `results/S1-certify-tier1/attestations.json`
- [x] **Uncertified** workload (`uncertified-agent`) **refused** admission to production context (**S2 pass**, 403) — **A3 pass**
- [ ] **Model swap** (`model-swap-agent`) **blocked** — **S3 FAIL: run admitted with 201** (real finding; see report Issues) — **A6 fail**
- [x] **Seeded regression** (`regressed-agent`) fails certification at production threshold (**S4 pass**: blocked, `uncertified`, critical=1) — **A4 pass**

### Phase 3 — Lifecycle

- [ ] Submit a task to each certified Tier 1 workload (`hiveplane submit --agent <name> --context production --task '<json>'`)
- [ ] Verify `queued → running → completed` state transitions and event logs (`hiveplane runs show <id>`)
- [ ] Verify usage is reported and priced (token usage, cost per run)
- [ ] Verify run state is persisted and queryable via `hiveplane runs list` and `hiveplane runs show <id>`
- [ ] Verify trace-linked debug context renders the execution story (admission, tool calls, model calls, usage, delivery)

### Phase 4 — Governance

- [ ] **Budget**: Seed an over-budget run; verify it is **blocked** before expensive work — **S5 blocked by design** (local model priced at $0; needs a priced-provider profile) — **A8 open**
- [ ] **Budget**: Verify daily aggregate budget enforcement refuses new runs when daily ceiling is hit — **not exercised**
- [ ] **Sandbox**: Trigger a destructive tool call (`support-agent` → `pagerduty.acknowledge`); verify the escalation + approval + resume path — **S6 incomplete** (aborted twice mid-run; the equivalent path passed inside S1's benchmark auto-approval) — **A11/A9 open**
- [ ] **Sandbox**: Verify resource caps are enforced — covered by the Docker L-layer suite; not re-run here
- [x] **Sandbox**: Verify egress is restricted — **demonstrated by accident**: the first S1 run failed because `api.pagerduty.com` was not in the manifest allowlist (call denied); fixed in the manifests
- [ ] **Output shaping**: Seed a tool returning a large payload; verify truncation — **S7 blocked by design** (needs an oversized fixture) — **A10 open**

### Phase 5 — Intervention & Durability

- [x] **Pause→restart→resume**: `eval-judge` run paused at its human-review interrupt, survived a full control-plane restart (`docker compose restart api`), and remained `paused` with context intact — evidence: `results/S8-pause-restart-resume/after_restart.json` (**A12 partial: re-attach proven, final resume→completed not observed — S8 incomplete**)
- [x] **Fan-out**: completed runs deliver results to configured destinations; delivery recorded in the run story — **S9 pass** (Slack webhook → webhook-sink) — **A14 pass**
- [ ] **Fan-out**: failed runs deliver to `on_failed` destinations — not exercised
- [ ] Stop a run via `hiveplane runs stop <id>`; verify cancellation is **audited** — not exercised

### Phase 6 — Review

- [ ] Fleet view shows all Tier 1 workloads with health, failures, and spend — **not exercised**
- [ ] **Certification dashboard** renders fleet cert status — **not exercised (A19 open)**
- [ ] **Spend view** shows cost showback by team and agent — **not exercised (A20 open)**
- [ ] Audit trail is **complete** for every run in the field test — **not yet assessed (A15 open)**

## Acceptance Criteria

All v0.1.0 release gates from [PRD 07](../../prd/07-success-metrics.md):

| # | Criterion | Target | Phase |
|---|-----------|--------|-------|
| A1 | Three real agents registered | 3/3 | Phase 1 |
| A2 | At least one agent certified for production via benchmark | ≥ 1 | Phase 2 |
| A3 | An uncertified agent is refused admission to a production context | pass | Phase 2 |
| A4 | A seeded manifest change is blocked by re-certification (regression caught) | ≥ 1 seeded | Phase 2 |
| A5 | Attestation is signed and verified on read | pass | Phase 2 |
| A6 | Model-swap blocks (certified on A, running on B) | ≥ 1 seeded | Phase 2 |
| A7 | Agents through full lifecycle (submit → queued → running → completed) | 3/3 | Phase 3 |
| A8 | Budget enforcement demonstrably blocks an over-budget run | ≥ 1 | Phase 4 |
| A9 | Execution isolation demonstrably caps a destructive run | pass | Phase 4 |
| A10 | Tool-output shaping demonstrably truncates a large payload | ≥ 1 shaped run | Phase 4 |
| A11 | Guarded tool call requires approval (approve + deny paths) | pass | Phase 4 |
| A12 | Paused run survives control-plane restart | pass | Phase 5 |
| A13 | Operators can inspect and stop any run from one surface | pass | Phase 5 |
| A14 | Result fan-out delivered to configured destinations | ≥ 1 fanned-out result | Phase 5 |
| A15 | Audit trail complete for every run in the field test | 100% | Phase 6 |
| A16 | Median time to inspect and stop a bad run | < 2 minutes | Phase 6 |
| A17 | Docker Compose stack starts with one command | pass | Phase 1 |
| A18 | `hiveplane init` scaffolds a working project in < 5 minutes | pass | Phase 1 |
| A19 | Certification dashboard renders fleet cert status | pass | Phase 6 |
| A20 | Spend view shows cost showback by team and agent | pass | Phase 6 |
| A21 | ≥ 1 agent certified per platform (8 frameworks) | _deferred (not a v0.1.0 gate)_ | — |

## LLM Configuration

The field test exercises a **hybrid** provider strategy. The model actually used is reported by
the provider and checked against the certification binding (T11); a mismatch blocks the run.

| Environment | Provider | Endpoint | Secrets | Determinism |
|-------------|----------|----------|---------|-------------|
| CI / nightly | `fake` (replay) | in-process | none | fully deterministic |
| **Local field test (current)** | `local` (OMLX) | `host.docker.internal:8000/v1` (native: `127.0.0.1:8000/v1`) | none | temperature 0 |
| Cloud validation (future) | `cloud` (OpenAI) | `api.openai.com` | `OPENAI_API_KEY` | temperature 0 |

**The v0.1.0 field test runs on the local model `Qwen3-4B-Instruct-2507-4bit`**
(served by OMLX on the host; canonical identity `omlx/qwen3-4b-instruct-2507/4bit`). A cloud
model will be added later; cloud is not required for the v0.1.0 release gate.

- All three Tier 1 workloads run on **local** (OMLX `Qwen3-4B-Instruct-2507-4bit`) and optionally
  on **fake** (replay) for repeatable CI. Cloud is a later addition.
- `spec.model.identity` in each manifest must match the provider actually used.
- **Model aliases are required for real providers**: the server-reported model name must map to
  the canonical identity the run is bound to via `HIVEPLANE_MODEL__MODEL_ALIASES` (local OMLX:
  `Qwen3-4B-Instruct-2507-4bit` → `omlx/qwen3-4b-instruct-2507/4bit`, and certify with
  `--model-identity omlx/qwen3-4b-instruct-2507/4bit`; cloud: `gpt-4o-2024-08-06` →
  `openai/gpt-4o/2024-08-06`). The provider also maps the bound identity back to the served name
  on request. Without a matching alias every model call raises `ModelIdentityMismatchError` — the
  T11 model-swap defense firing on legitimate calls.
- **Host port 8000 is reserved for OMLX.** The control-plane API maps to host port 8100
  (`API_PORT`) and the UI to 3001; no HivePlane service binds 8000.
- Local/fake models are priced at zero in the budget table; cloud models use real prices.

Models validated by the proven agents (reference for future profiles):

| Model | Used by | Tier |
|-------|---------|------|
| `Qwen3-4B-Instruct-2507-4bit` (local MLX) | **HivePlane Tier 1**, tooltrust, exectrace | Tier 1/Tier 2 |
| `gpt-oss-20b` (OpenRouter) | tooltrust (v0.2.0) | Tier 2 |
| `glm-5` (z-ai) | tooltrust (retries, best tier-1) | Tier 2 |
| `deepseek-v4-flash` (deepseek) | tooltrust (v0.2.0 comparison) | Tier 2 |
| `gpt-4o-mini` (OpenAI) | evalforge (all 19) | Tier 2 |
| `Qwen2.5-1.5B-4bit` (local MLX) | exectrace (PydanticAI agents) | Tier 2 |
| `omlx/qwen3-4b-instruct-2507/4bit` | HivePlane Tier 1 (canonical) | Tier 1 |

See [D17: LLM Provider Design](../../design/llm-provider-design.md) (#104).

## Scenario Map (S1-S9)

| # | Scenario | Phase | Result (2026-09-25) |
|---|----------|-------|---------------------|
| S1 | Certify both Tier 1 agents via benchmark | 2 | ✅ pass — both certified, signed attestations |
| S2 | Uncertified agent attempts production | 2 | ✅ pass — refused (403) |
| S3 | Model-swap (cert on A, run on B) | 2 | ❌ fail — run admitted (201); real finding |
| S4 | Seeded manifest change regresses | 2 | ✅ pass — blocked, critical=1 |
| S5 | Over-budget run | 4 | ⏸️ blocked — needs priced provider |
| S6 | Destructive tool call | 4 | ⚠️ incomplete — aborted twice, no verdict |
| S7 | Large tool output | 4 | ⏸️ blocked — needs oversized fixture |
| S8 | Pause → restart → resume | 5 | ⚠️ incomplete — restart/re-attach proven, resume not observed |
| S9 | Result fan-out | 5 | ✅ pass — delivery in run story |

## Repeatability Procedure

1. One command: `scripts/field-test.sh` — resets volumes, brings the stack up on the local
   model, seeds tools, registers the Tier 1 + negative-fixture workloads, runs S1–S9, writes
   evidence to `field_test/v0.1.0/results/` and the report to
   `docs/field-test/v0.1.0/FIELD_TEST_REPORT.md`. Flags: `--keep` (leave the stack up),
   `--only S1,S7` (subset), `--no-build` (skip the image rebuild).
2. Prerequisite: OMLX serving `Qwen3-4B-Instruct-2507-4bit` on host port 8000 (fail-fast
   preflight — no skips).
3. Against an already-running stack, the runner can also be invoked directly:
   `PYTHONPATH=. .venv/bin/python scripts/field_test_runner.py --api-url http://localhost:8100
   --results-dir "$(pwd)/field_test/v0.1.0/results" --only S9` (set
   `HIVEPLANE_FIELD_RESTART_CMD` for S8).
4. Manual equivalent: `docker compose --env-file .env.local --profile local --profile test up -d`,
   then `scripts/field-test-setup.sh --api-url http://localhost:8100`, then certify each
   Tier 1 workload `--context staging` (→ `provisional`) and `--context production`
   (→ `certified`) with `--model-identity omlx/qwen3-4b-instruct-2507/4bit`.
5. Restart mid-scenario for S8 (`docker compose restart api`); verify re-attach and resume.

Anyone can rerun by following this directory. The container/API/UI layers are covered by the
completed [Docker test plan](docker-test-plan.md); this procedure covers the **real-agent**
certified lifecycle.

## Reporting

Raw field-test evidence (run ids, events, usage, attestations, command output, logs per scenario)
is written to **`field_test/v0.1.0/results/`** and committed. The concise narrative, metrics, and
learnings are recorded in [FIELD_TEST_REPORT.md](FIELD_TEST_REPORT.md), with each claim linking to
its evidence under `results/`.
