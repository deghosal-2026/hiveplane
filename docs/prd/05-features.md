# PRD 05: Features

## TLDR

v0.1.0 proves the control loop with real LLM-backed agents, certification, safe execution, and tool-output governance. v0.2.0 makes the fleet self-operating (triggers, policy packs, eval gates, write-back, cost showback, drift detection). v0.3.0 adds reliability and defense. v0.4.0 scales to multi-tenancy and clusters.

Features are grouped by theme. The **certification pipeline** is the thesis — it is what makes HivePlane different from every competitor.

---

## Theme: Certification Pipeline (THE THESIS)

This is the SWE-bench layer. Agents earn the right to operate in production by passing a reproducible benchmark. No competitor does this.

- **benchmark corpus** — each workload type references a benchmark corpus of tasks with known expected outcomes and deterministic pass/fail checks
- **benchmark runner** — runs the workload against its corpus in a controlled, isolated environment (fixed model, fixed inputs, no network side effects unless explicitly allowed)
- **certification engine** — evaluates benchmark results against thresholds (pass rate, no critical failures, latency bounds); assigns status: `uncertified` → `provisional` → `certified` → `quarantined`
- **signed attestation** — each certification is an attestation: benchmark version, model, eval results, timestamp, environment, signer; stored immutably and auditable
- **promotion gate** — refuses to promote a manifest change to production until re-certification passes; compares new vs. previous certification; blocks regressions
- **regression diff** — on a failed re-certification, shows which tasks regressed vs. the previous run, with replayable traces for each
- **drift detector** — schedules periodic re-certification for production agents; if performance decays below threshold, auto-quarantines the agent and notifies the owning team
- **certification dashboard** — fleet view of certification status, pass rates, trends, last-certified timestamps, and quarantine history
- **certification API** — `hiveplane certify <workload>`, `hiveplane certs list`, `hiveplane certs show <id>`, `hiveplane certs compare <v1> <v2>`

## Theme: Workload Model & Registry

- agent registry with owner, runtime type, model strategy, policy metadata, trigger rules, and certification status
- declarative workload manifest with strict validation and JSON Schema export
- manifest versioning (append-only history)
- `--dry-run` registration that reports what would be enforced without admitting runs
- **certification status enforced at admission** — the registry refuses to admit a run to a production context unless the workload is `certified`

## Theme: Run Lifecycle & Execution

- task submission API with persistent run state
- run state machine: queued → running → paused/completed/failed/cancelled
- pause, resume, and cancel controls with attribution
- durable run state — a paused run survives a control-plane restart
- **trigger rules** — webhook, alert, GitHub PR event, and cron ingest that auto-start runs
- **scheduled and watch modes** — 24/7 operator mode (deployment verification, periodic health checks)
- **state diff and replay helpers** — replay a run frame-by-frame for debugging

## Theme: Policy & Governance

- deny-by-default tool permissions
- approval and escalation flows with evidence
- **context-aware policy** — staging-vs-production, data sensitivity (public vs. PII), and blast-radius scoring; decisions carry a reason and originating rule
- **team policy packs** — versioned, distributable policy bundles applied across a team's workloads
- **prompt-injection and adversarial input defense** — input scanning and deterministic blocking at the boundary
- every operator action attributed and auditable; tamper-evident audit log

## Theme: Safe Execution

- **execution isolation** — sandboxed run context for destructive or production-affecting actions
- **per-run resource caps** — memory, CPU, wall-clock, and output-size limits
- **tool-output shaping** — server-side filtering, truncation, and JSON tree traversal to keep large tool payloads out of context windows
- budget enforcement per run and per day, evaluated at admission and on each usage event
- secrets redacted from logs, traces, and audit events

## Theme: LLM Provider & Agent Contract

- **LLM provider seam** — a provider abstraction for model invocation: local (Ollama/OMLX-style OpenAI-compatible endpoint), cloud (OpenAI), and a deterministic fake/replay provider for CI; selected by configuration, never hardcoded
- **agent invocation contract** — `WorkerContext.complete()` routes model calls through the control-plane boundary: real token usage and cost are reported from the provider response, model-call spans are emitted, and cooperative pause/cancel is honored
- **tool execution layer** — the tool-call boundary executes tools (fixture-backed for v0.1.0; MCP transport in v0.2.0) so agents receive real data instead of fabricating tool outputs
- **runtime model identity** — the model actually used is reported by the provider and checked against the certification binding (T11), not self-reported by the caller
- **durable execution** — paused runs and LangGraph checkpoints survive a control-plane restart

## Theme: Tools & MCP

- **MCP tool registry** — onboard tools via the MCP layer (built on mcp-fabric); each tool gets a trust level and a stable tool ID
- workloads reference tools by ID; policy decides who may call them and under what conditions
- **tool trust levels** — read-only vs. destructive classification enforced at the request boundary
- reference adapters: raw Python worker + LangGraph example

## Theme: Observability & Health

- OpenTelemetry-native traces, metrics, logs, and audit events, correlated by run
- trace-linked debug context per run (planning, tool calls, model calls, retries, approvals, cost)
- fleet metrics: runs by state, budget burn, failures, escalations, intervention latency
- **agent health model** — readiness, recent failure rate, SLO status, and drift as first-class fleet signals
- **reliability metrics and SLO hooks** — per-workload availability and quality SLOs

## Theme: Cost & ROI

- budget burn visible per run, workload, and team
- **cost showback** — attribute spend to teams and agents, detect waste, and flag expensive-but-low-value agents
- **ROI flags** — spend vs. observed value/outcome, surfaced in the weekly fleet review
- **cost-per-completed-task** (not per-call) — real cost including retries, failed loops, and escalations

## Theme: Result Delivery

- **result fan-out** — on completion/failure/escalation, push results to Slack, Teams, Jira, GitHub PR comment, or webhook, with a trace link and certification attestation link
- approval notifications via Slack/webhook
- operators do not have to come to HivePlane to learn something happened

## Theme: Operator Surface

- CLI: register, submit, runs, approvals, triggers, tools, **certify, certs**
- minimal UI: fleet list, run detail, approval queue, **certification dashboard**, spend view, agent health
- `hiveplane init` — scaffold a new project with example workloads, a seeded demo, and a sample benchmark corpus
- Docker Compose one-command start; PyPI distribution

## Theme: Distribution & First Run

- **first-run demo** — seeded workload that exercises the full loop: register → certify → trigger → run → intervene → deliver, in minutes
- **PyPI package + Homebrew** (later)
- **Helm chart and reference cluster deployment** (v0.4)

---

## Version Mapping

### v0.1.0 — Prove the Control Loop (with Certification)

**The thesis ships here.** An agent is registered, certified against a benchmark, and only then allowed to run.

- registry, manifest validation, versioning, `--dry-run`
- **LLM provider seam** — local (Ollama/OMLX), cloud (OpenAI), and fake/replay provider for CI (new)
- **agent invocation + tool execution seams** — `WorkerContext.complete()` calls the model through the boundary; tools execute and return real (fixture-backed) data (new)
- **benchmark runner + certification engine + signed attestation** (new)
- **adapter-backed certification** — the benchmark executes the real agent entrypoint against the corpus (new)
- **durable signing keypair** — attestations remain verifiable across restarts (new)
- **certification status enforced at admission** (new)
- task submission, run state machine, pause/resume/cancel, durable state
- **durable resume** — a paused run survives a control-plane restart (new)
- budget enforcement per run/day
- deny-by-default policy + approvals + audit
- **approval re-dispatch** — an escalated tool call executes after human approval (new)
- execution isolation + resource/output caps (new)
- tool-output shaping (new)
- raw-worker + LangGraph adapters, adapter conformance suite
- **PostgreSQL-backed stores** — registry, budget, certifications, attestations, and approvals, not just run state (new)
- **auto-migration** — schema migrations run on startup so a fresh stack is functional on first boot (new)
- OTel traces/metrics/logs, trace-linked debug context, fleet metrics
- CLI + minimal UI (fleet list, run detail, approval queue, **certification dashboard**, spend view)
- `hiveplane init` + seeded demo + Docker Compose

### v0.2.0 — Self-Operating Fleet

- trigger rules (webhook/alert/PR/cron) + scheduled/watch modes (new)
- **promotion gate + re-certification + regression diff** (new)
- **drift detector + auto-quarantine** (new)
- context-aware policy + team policy packs (new)
- MCP tool registry + tool trust levels (new)
- result fan-out (Slack/Teams/Jira/PR/webhook) (new)
- cost showback + ROI flags + cost-per-completed-task (new)
- state diff/replay helpers (new, pulled forward)
- approval queue UI, richer policy conditions, budget analytics by team
- stronger adapter contract

### v0.3.0 — Reliability & Defense

- agent health model (readiness, failure rate, SLO, drift) (new)
- reliability metrics and SLO hooks (new)
- prompt-injection / adversarial input defense (new)
- multi-runtime support
- replay helpers (if not already shipped)

### v0.4.0 — Scale & Tenancy

- multi-tenant support
- ROI dashboards (fleet-wide)
- Helm chart and reference cluster deployment
- PyPI + Homebrew distribution hardening

---

## In Scope (Overall)

- fleet registry and workload model
- **LLM provider seam and agent invocation/tool execution contract**
- **certification pipeline (benchmark, attestation, promotion gate, drift detection)**
- run lifecycle management, including triggers and scheduled modes
- budget enforcement and cost showback
- policy, approvals, intervention, and safe execution isolation
- tool-output shaping and MCP tool registry
- trace, metric, audit, and health integration
- result fan-out and a real operator UI

## Out of Scope (Initial Versions)

- building a new agent framework
- **building or training models** — HivePlane integrates with existing providers (OpenAI-compatible local and cloud endpoints); it does not replace them
- generalized workflow authoring UI
- full enterprise IAM complexity
- autonomous self-healing logic for every failure mode
- incident RCA, postmortems, service maps, or observability collection (adjacent tools)
