# HivePlane v0.1.0 — Field Test Plan

> Status: plan — to be executed in M23 (re-planned into phases P0-P4).
> Updated 2026-09-20: expanded from 3 workloads to 113 agents across 12 platforms,
> with proven field-tested agents from agent-tooltrust, agent-eval-forge, and
> agent-exec-trace.

## Prerequisites (M23)

The original plan assumed the WBS shipped LLM integration, real agent execution in certification,
sandbox enforcement, and durable resume. It did not. Before this plan can execute, M23 must land:

- **LLM provider seam** (#104/#107/#115) and **real agents** (#108) — otherwise runs are stubs.
- **Tool execution** (#116) — otherwise agents fabricate tool output.
- **Adapter-backed certification** (#105/#117/#109) — otherwise the benchmark is theater.
- **Persistence wiring + auto-migration** (#118/#125/#126/#128) — otherwise state is lost on restart.
- **Sandbox caps** (#110) and **durable resume** (#111) — for S6 and S8.
- **Corpora** for all three workloads (#98) and **container readiness** (#112).

The [Docker test plan](docker-test-plan.md) validates the same stack at the container layer and
produces the screenshots referenced here.

## Objective

Prove the certified control loop with real agent workloads across diverse agentic platforms. An
agent is registered, certified against a benchmark, and only then allowed to run in production.
The field test exercises certification, sandbox execution, tool-output shaping, budget
enforcement, intervention, durability, and fleet review — all the v0.1.0 release gates.

## Agent Inventory

All agents and corpora live under `field_test/` in the HivePlane repository.

### Tier 1 — Primary workloads (certification + governance scenarios)

Three workloads drive the core certification and governance scenarios (S1–S9). Each is adapted
from a real, LLM-backed agent we built and validated, and runs through HivePlane's existing
adapters (`raw-worker` or `langgraph`).

| Workload | Source | Adapter | What it does | Stresses |
|----------|--------|---------|--------------|----------|
| `repo-agent` | ai-code-guardian | raw-worker | Classifies PR risk, flags high-risk PRs via `mcp.github.create_pr_comment` | read-only tool policy, usage reporting, certification at production threshold |
| `docs-agent` | release-narrator | langgraph | Drafts docs, categorizes as `tutorial\|reference\|changelog\|bugfix`, review-gate interrupt | LangGraph adapter, state transitions, output shaping on large payloads |
| `incident-agent` | oncall-rag + ai-loopguard + ai-incident-commander | raw-worker | Queries Prometheus, acknowledges PagerDuty [destructive, escalates], triages severity | destructive tool in sandbox, approval flow, budget escalation, fan-out delivery |

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

Each primary workload has a HivePlane-format `corpus.yaml` with `exact_match` and `action_audit`
checks, authored per the [D20 corpus-fixture coupling spec](../../design/corpus-fixture-coupling.md).

| Corpus | Location | Version | Tasks | Check types | Critical negs |
|--------|----------|---------|-------|-------------|---------------|
| `repo-agent-corpus` | `field_test/corpora/repo-agent/hiveplane-corpus.yaml` | v2 | 11 (7 pos + 4 neg) | `exact_match` (risk: low/medium/high), `action_audit` (required: `create_pr_comment`; forbidden: `merge_pull_request`) | 3 |
| `docs-agent-corpus` | `field_test/corpora/docs-agent/hiveplane-corpus.yaml` | v2 | 9 (6 pos + 3 neg) | `exact_match` (category: tutorial/reference/changelog/bugfix), `action_audit` (required: `read_issue`; forbidden: `create_pr`) | 2 |
| `incident-agent-corpus` | `field_test/corpora/incident-agent/hiveplane-corpus.yaml` | v1 | 9 (6 pos + 3 neg) | `exact_match` (severity: critical/warning/info), `action_audit` (required: `prometheus.query`; forbidden: `pagerduty.resolve_incident`) | 2 |

Coupling fixtures (D20): tool fixtures in `deploy/testdata/tools/` (6 files), model replay in
`deploy/testdata/llm/replay.json` (29 keys, CI hermetic profile). Every task chains:
`task.input → fixture → agent prompt → expected model output → check`.

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
├── agents/
│   ├── repo_agent/              ← Tier 1: ai-code-guardian (raw-worker)
│   ├── docs_agent/              ← Tier 1: release-narrator (langgraph)
│   ├── incident_agent/          ← Tier 1: oncall-rag + loopguard + commander (raw-worker)
│   ├── self_edit_agent/         ← Tier 3: agent-self-edit (custom)
│   ├── cauterule/               ← Tier 3: CauterRule (CrewAI + PydanticAI + MCP)
│   ├── planner_critic/          ← Tier 3: planner-critic-engine (AutoGen + OAI SDK + PydanticAI)
│   ├── mcplex/                  ← Tier 3: mcplex (MCP server reference)
│   └── proven/
│       ├── tooltrust/           ← Tier 2: 83 agents, 10 frameworks
│       │   ├── *.py             (12 framework shims)
│       │   ├── agents.yaml      (83-agent roster)
│       │   ├── scenarios.yaml   (30 decision scenarios)
│       │   ├── download_field_agents.sh  (re-clone vendor/ repos)
│       │   └── vendor/          (gitignored — 12 upstream repos, 463 MB)
│       ├── evalforge/           ← Tier 2: 19 adapters, 8 frameworks
│       │   ├── config/         (19 wrapper shims + 19 JSON configs)
│       │   ├── scenarios/      (11 scenario packs)
│       │   ├── adapters/       (trajectory extractors)
│       │   ├── list.txt        (19-agent clone manifest)
│       │   └── agents/          (gitignored — 13 upstream repos, 1.2 GB)
│       └── exectrace/           ← Tier 2: 4 validated + 6 reference
│           ├── agent-raw/      (validated: raw Python)
│           ├── agent-pydantic/  (validated: PydanticAI v2)
│           ├── agent-weather/   (validated: PydanticAI v1)
│           ├── request-triage/  (validated: LangGraph)
│           ├── agent-mcp/       (gitignored reference: LangGraph + MCP)
│           ├── agent-crew/      (gitignored reference: CrewAI)
│           └── ...
├── corpora/
│   ├── repo-agent/              (fixtures + hiveplane-corpus.yaml)
│   ├── docs-agent/              (10 repos data + hiveplane-corpus.yaml)
│   └── incident-agent/          (runbook corpus + eval.json + hiveplane-corpus.yaml)
└── __init__.py
```

### Vendored repos (gitignored, re-cloned on demand)

| Source | Vendor dir | Size | Re-clone command |
|--------|-----------|------|------------------|
| tooltrust (12 upstream repos) | `proven/tooltrust/vendor/` | 463 MB | `cd proven/tooltrust && bash download_field_agents.sh` |
| evalforge (13 upstream repos) | `proven/evalforge/agents/` | 1.2 GB | `cd proven/evalforge && bash setup.sh` (or clone from `list.txt`) |
| exectrace (4 reference agents) | `proven/exectrace/{agent-crew,agent-chatbot,agent-react,agent-mcp}/` | 36 MB | copy from agent-exec-trace repo |

## Phases

### Phase 1 — Baseline Validation

- [ ] Docker Compose stack starts with one command (`docker compose up -d`)
- [ ] `hiveplane init` scaffolds a working project in < 5 minutes
- [ ] Registry, state store, telemetry pipeline, and certification engine are reachable
- [ ] All three Tier 1 workloads register successfully (`hiveplane register`)
- [ ] Tier 2 platform-coverage agents register (one per framework, via `run()` wrapper)
- [ ] `--dry-run` registration reports what would be enforced without admitting runs
- [ ] MCP Tool Registry is seeded with the tools referenced by each workload
- [ ] Vendored upstream repos re-cloned via download scripts (`field_test/agents/proven/*/`)

### Phase 2 — Certification

**Tier 1 (primary, required for release gate):**

- [ ] Run `hiveplane certify repo-agent` — benchmark executes in isolated environment
- [ ] Run `hiveplane certify docs-agent` — benchmark executes in isolated environment
- [ ] Run `hiveplane certify incident-agent` — benchmark executes in isolated environment
- [ ] Verify all three pass at `production_threshold` and status becomes `certified`
- [ ] Verify each certification produces a **signed attestation** (benchmark version, model, eval results, timestamp, environment, signer)
- [ ] Verify `hiveplane certs show <id>` displays the full attestation
- [ ] Verify `hiveplane certs list` shows all three with `certified` status
- [ ] Verify an **uncertified** workload (register a fourth without certifying) is **refused admission** to a production context
- [ ] Verify a **model swap** (change `model.identity` without re-certification) is **blocked** at run start

**Tier 2 (platform coverage, demonstrates framework-agnostic certification):**

- [ ] Certify ≥ 1 agent per framework (LangGraph, CrewAI, PydanticAI, OpenAI Agents SDK, AutoGen, smolagents, LlamaIndex, Google ADK) against the proven scenario packs
- [ ] Verify each platform agent passes its framework-specific scenario pack
- [ ] Verify certifications bind to the model identity used (T11 enforcement across frameworks)

### Phase 3 — Lifecycle

- [ ] Submit a task to each certified Tier 1 workload via `hiveplane submit`
- [ ] Verify `queued → running → completed` state transitions and event logs
- [ ] Verify usage is reported and priced (token usage, cost per run)
- [ ] Verify run state is persisted and queryable via `hiveplane runs list` and `hiveplane runs show <id>`
- [ ] Verify trace-linked debug context renders the execution story (planning, tool calls, model calls, cost)
- [ ] Submit a task to ≥ 1 Tier 2 platform agent; verify same lifecycle

### Phase 4 — Governance

- [ ] **Budget**: Seed an over-budget run on `incident-agent`; verify it is **blocked** before expensive work
- [ ] **Budget**: Verify daily aggregate budget enforcement refuses new runs when daily ceiling is hit
- [ ] **Sandbox**: Trigger a destructive tool call on `incident-agent` (e.g. `pagerduty.acknowledge`); verify it executes in the **isolated sandbox context**
- [ ] **Sandbox**: Verify resource caps are enforced (seed a run that exceeds `wall_clock_seconds` → killed)
- [ ] **Sandbox**: Verify egress is restricted (seed a call to a non-allowlisted host → blocked)
- [ ] **Approval**: Verify the destructive tool call **requires approval** before execution
- [ ] **Approval**: Test approve path → run resumes and completes (re-dispatch per #129)
- [ ] **Approval**: Test deny path → run fails with attributed denial
- [ ] **Output shaping**: Seed `docs-agent` with a tool that returns a large payload; verify it is **truncated** per `max_bytes_per_tool_call`
- [ ] **Output shaping**: Verify filter rules are applied (secrets redacted, patterns matched)
- [ ] **Output shaping**: Verify cumulative output budget is enforced (total output exceeding `max_output_bytes` → run paused)

### Phase 5 — Intervention & Durability

- [ ] Pause a running run via `hiveplane runs pause <id>`; verify it stops advancing
- [ ] Restart the control plane (`docker compose restart`); verify the paused run **resumes** with context intact
- [ ] Resume the paused run; verify it continues to completion
- [ ] Stop a run via `hiveplane runs stop <id>`; verify cancellation is **audited**
- [ ] **Fan-out**: Verify completed runs deliver results to configured `fan_out` destinations (Slack/webhook)
- [ ] **Fan-out**: Verify failed runs deliver to `on_failure` destinations (e.g. Jira)
- [ ] **Fan-out**: Verify escalated runs deliver to `on_escalation` destinations
- [ ] Verify fan-out messages include a **trace link** and **attestation link**

### Phase 6 — Review

- [ ] Fleet view shows all workloads (Tier 1 + Tier 2) with health, failures, and spend by worker and team
- [ ] **Certification dashboard** shows certification status, pass rates, last-certified timestamps across all platforms
- [ ] **Spend view** shows cost showback by team and agent, including cost-per-completed-task
- [ ] **Agent health** shows readiness, failure rate, and SLO status per workload
- [ ] Trace-linked debug context renders the full execution story for any run
- [ ] Audit trail is **complete** for every run in the field test (100%)

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
| A21 | ≥ 1 agent certified per platform (8 frameworks) | 8/8 | Phase 2 |

## LLM Configuration

The field test exercises a **hybrid** provider strategy. The model actually used is reported by
the provider and checked against the certification binding (T11); a mismatch blocks the run.

| Environment | Provider | Endpoint | Secrets | Determinism |
|-------------|----------|----------|---------|-------------|
| CI / nightly | `fake` (replay) | in-process | none | fully deterministic |
| Local field test | `local` (OMLX) | `host.docker.internal:8000/v1` (native: `127.0.0.1:8000/v1`) | none | temperature 0 |
| Cloud validation (optional) | `cloud` (OpenAI) | `api.openai.com` | `OPENAI_API_KEY` | temperature 0 |

- At least one workload must run on **local** and at least one on **cloud** (when a key is
  available); all three must run on **fake** for repeatable CI.
- Tier 2 platform agents run on **local** (OMLX Qwen) or **fake** (replay); cloud is optional.
- `spec.model.identity` in each manifest must match the provider actually used.
- **Model aliases are required for real providers**: the server-reported model name must map to
  the canonical identity the run is bound to via `HIVEPLANE_MODEL__MODEL_ALIASES` (cloud:
  `gpt-4o-2024-08-06` → `openai/gpt-4o/2024-08-06`; local OMLX: the served model tag → the local
  canonical identity, e.g. `mlx-community/Qwen2.5-7B-Instruct-4bit` →
  `omlx/qwen2.5-7b-instruct/4bit`, and certify with `--model-identity omlx/qwen2.5-7b-instruct/4bit`).
  The provider also maps the bound identity back to the served name on request. Without a
  matching alias every model call raises `ModelIdentityMismatchError` — the T11 model-swap
  defense firing on legitimate calls.
- **Host port 8000 is reserved for OMLX.** The control-plane API maps to host port 8100
  (`API_PORT`) and the UI to 3001; no HivePlane service binds 8000.
- Local/fake models are priced at zero in the budget table; cloud models use real prices.

Models validated by the proven agents (for reference when configuring LLM profiles):

| Model | Used by | Tier |
|-------|---------|------|
| `Qwen3-4B-Instruct-2507-4bit` (local MLX) | tooltrust (all), exectrace | Tier 2 |
| `gpt-oss-20b` (OpenRouter) | tooltrust (v0.2.0) | Tier 2 |
| `glm-5` (z-ai) | tooltrust (retries, best tier-1) | Tier 2 |
| `deepseek-v4-flash` (deepseek) | tooltrust (v0.2.0 comparison) | Tier 2 |
| `gpt-4o-mini` (OpenAI) | evalforge (all 19) | Tier 2 |
| `Qwen2.5-1.5B-4bit` (local MLX) | exectrace (PydanticAI agents) | Tier 2 |
| `omlx/qwen2.5-7b-instruct/4bit` | HivePlane Tier 1 | Tier 1 |

See [D17: LLM Provider Design](../../design/llm-provider-design.md) (#104).

## Scenario Map (S1-S9)

| # | Scenario | Phase | Prereqs |
|---|----------|-------|---------|
| S1 | Certify all three agents via benchmark | 2 | #109, #117, #98, #107/#115 |
| S2 | Uncertified agent attempts production | 2 | admission gate (exists) |
| S3 | Model-swap (cert on A, run on B) | 2 | #104/#107, #127 |
| S4 | Seeded manifest change regresses | 2 | promotion gate (exists) |
| S5 | Over-budget run | 4 | budget service + #107 pricing |
| S6 | Destructive tool call | 4 | #110, #116, #129 |
| S7 | Large tool output | 4 | shaping (exists) + #116 |
| S8 | Pause → restart → resume | 5 | #111, #118 |
| S9 | Result fan-out | 5 | fan-out (exists) + #93 webhook sink |

## Repeatability Procedure

1. `docker compose --profile <ci|local|cloud> up -d` (one command).
2. Re-clone vendored upstream agent repos:
   - `cd field_test/agents/proven/tooltrust && bash download_field_agents.sh`
   - `cd field_test/agents/proven/evalforge && bash setup.sh` (or clone from `list.txt`)
3. Seed fixtures and register workloads (setup script, #99):
   - Register Tier 1 workloads (`repo-agent`, `docs-agent`, `incident-agent`)
   - Register ≥ 1 Tier 2 agent per framework (via `run()` wrapper manifests)
4. Certify all Tier 1 (Phase 2) and ≥ 1 Tier 2 per framework.
5. Run scenarios S1-S9, capturing evidence per scenario.
6. Capture screenshots into `screenshots/<scenario-id>/` (docker-test-plan §8).
7. Restart mid-scenario for S8; verify persistence and attestation verification.
8. Write results into [FIELD_TEST_REPORT.md](FIELD_TEST_REPORT.md).

Anyone can rerun by following the directory; the nightly simulator + CI harness (#59) keep the
evidence fresh.

## Reporting

Results, raw metrics, and learnings are recorded in [FIELD_TEST_REPORT.md](FIELD_TEST_REPORT.md).
