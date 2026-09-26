# PRD 05: Features

## TLDR

v0.1.0 proves the control loop with real LLM-backed agents, certification, safe execution, and tool-output governance. **v0.2.0 is the final big release — the Complete Fleet OS.** The fleet runs itself (triggers, pipelines, GitOps reconciliation), defends itself (drift + injection + policy + kill switches), explains itself (cost/ROI, reporting, showback), and scales itself (tenancy, distributed workers, Helm). Everything once planned for v0.3.0/v0.4.0 ships in v0.2.0; after v0.2.0 the project moves to maintenance mode.

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
- **attestation transparency log** — append-only, verifiable certification history
- **public attestation verification** — verify any attestation by ID without auth; delivered results link to it
- **certification expiry / renewal windows** — scheduled re-certification for production agents
- **shadow runs** — candidate vs. certified side-by-side on the same corpus, outcome diff
- **canary routing + auto-promote** — route x% of live triggers to a candidate version; promote on clean canary (progressive delivery for agents)
- **model experiment campaigns** — route traffic across model configs; benchmark-scored winner selection
- **production feedback loop → corpus** — operators mark runs good/bad/failed-with-lesson; flagged failures become new corpus cases automatically included in the next certification (the benchmark learns from production)
- **online eval sampling** — LLM-judge scoring on a sampled percentage of production runs; feeds health and drift with real-world signal between scheduled re-certifications
- **workload provenance & agent signing** — sign agent bundles at registration; verify code identity at admission and on export/import (behavior certification + code identity together)

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
- **trigger rules** — webhook (HMAC-verified), alert, GitHub PR event, and cron ingest that auto-start runs; trigger DSL with event filters, cooldowns, dedup keys, rate limits, and payload→task templating; trigger→admission rules (trusted triggers auto-admit in staging, approval-gated in production); full trigger history and audit trail
- **scheduled and watch modes** — 24/7 operator mode (deployment verification, periodic health checks)
- **state diff and replay helpers** — replay a run frame-by-frame for debugging; run forking (copy state → edit → re-run); A/B replay; run-to-run diff
- **idempotency keys** on task submission — safe duplicate-submission dedup
- **run deadlines** — per-run SLA deadline with breach alerts
- **dead-letter queue for triggers** — failed trigger deliveries parked and replayable (`hiveplane triggers replay`)

## Theme: Policy & Governance

- deny-by-default tool permissions
- approval and escalation flows with evidence
- **context-aware policy** — staging-vs-production, data sensitivity (public vs. PII), and blast-radius scoring; decisions carry a reason and originating rule
- **team policy packs** — versioned, inheritable, linted, distributable policy bundles applied across a team's workloads
- **prompt-injection and adversarial input defense** — input and tool-output scanning at the boundary, taint marks, provenance tags; repeated attempts feed auto-quarantine
- **tool kill switch** — instant fleet-wide disable of any tool (bad MCP tool response, zero-day)
- **time-window policies** — business-hours-only destructive actions, blackout calendars
- **policy what-if / dry-run** — evaluate a decision without executing it
- **escalation and on-call routing** — no approval response in N minutes → page the next operator
- every operator action attributed and auditable; tamper-evident audit log

## Theme: Safe Execution

- **execution isolation** — sandboxed run context for destructive or production-affecting actions
- **per-run resource caps** — memory, CPU, wall-clock, and output-size limits
- **tool-output shaping** — server-side filtering, truncation, and JSON tree traversal to keep large tool payloads out of context windows
- budget enforcement per run and per day, evaluated at admission and on each usage event
- secrets redacted from logs, traces, and audit events
- **egress allow-lists** — per-workload outbound network policy
- **context-window budgets** — per-run max-context enforced live; per-step context accounting; breach → pause, not crash
- **spend-velocity guards** — burn-rate anomaly detection → auto-pause + alert

## Theme: LLM Provider & Agent Contract

- **LLM provider seam** — a provider abstraction for model invocation: local (Ollama/OMLX-style OpenAI-compatible endpoint), cloud (OpenAI), and a deterministic fake/replay provider for CI; selected by configuration, never hardcoded
- **agent invocation contract** — `WorkerContext.complete()` routes model calls through the control-plane boundary: real token usage and cost are reported from the provider response, model-call spans are emitted, and cooperative pause/cancel is honored
- **tool execution layer** — the tool-call boundary executes tools (fixture-backed for v0.1.0; MCP transport in v0.2.0) so agents receive real data instead of fabricating tool outputs
- **runtime model identity** — the model actually used is reported by the provider and checked against the certification binding (T11), not self-reported by the caller
- **durable execution** — paused runs and LangGraph checkpoints survive a control-plane restart

## Theme: Tools & MCP

- **MCP tool registry** — onboard tools via the MCP layer (built on mcp-fabric); each tool gets a trust level and a stable tool ID
- **live MCP transport + dynamic tool discovery** — connect real MCP servers; `hiveplane tools add` onboarding; manifest tool allow-lists
- workloads reference tools by ID; policy decides who may call them and under what conditions
- **tool trust levels** — read-only vs. destructive classification enforced at the request boundary
- reference adapters: raw Python worker + LangGraph + **PydanticAI + OpenAI Agents SDK/CrewAI (stretch)**, conformance suite v2

## Theme: Observability & Health

- OpenTelemetry-native traces, metrics, logs, and audit events, correlated by run
- trace-linked debug context per run (planning, tool calls, model calls, retries, approvals, cost)
- fleet metrics: runs by state, budget burn, failures, escalations, intervention latency
- **agent health model** — readiness, recent failure rate, SLO status, and drift as first-class fleet signals
- **reliability metrics and SLO hooks** — per-workload availability and quality SLOs
- **error-budget burn-rate** — burn-through → auto-throttle or quarantine (shared immune-system machinery)
- **retry policies with backoff** and **per-tool/per-workload circuit breakers** — trip → recover
- **synthetic probes** — periodic ping-tasks measuring live quality/readiness; early drift warning before the decay threshold
- **production quality scores** — online-eval judge results surface as a first-class health signal alongside readiness, failure rate, SLO burn, and drift
- **plane self-monitoring** — the plane exports its own Prometheus metrics and ships official Grafana dashboard JSONs
- **approval analytics** — approval latency by approver, bottleneck detection, trends
- **load test** — field test sustains ≥50 concurrent runs with throughput numbers in the report

## Theme: Cost & ROI

- budget burn visible per run, workload, and team
- **cost showback** — attribute spend to teams and agents, detect waste, and flag expensive-but-low-value agents
- **ROI flags** — spend vs. observed value/outcome, surfaced in the weekly fleet review
- **cost-per-completed-task** (not per-call) — real cost including retries, failed loops, and escalations
- **budget periods** (day/week/month) + **threshold alerts** (Slack at 80%) + burn **forecasts with overrun prediction**
- **pre-admission cost estimate** — expected cost by task type shown before admission
- **model-tier routing** — cheap model in staging, strong model in prod
- **per-tenant spend caps** with hard stop
- **chargeback metering API** — usage endpoints for tenant billing
- **result cache with attestation** — same task + agent version + config → cached result; invalidated on re-cert; cache-hit savings visible in showback

## Theme: Result Delivery

- **result fan-out** — on completion/failure/escalation, push results to Slack, Teams, Jira, GitHub PR comment, PagerDuty, Discord, Linear, email, or webhook, with a trace link and certification attestation link
- approval notifications via Slack/webhook
- **Slack interactive approvals** — approve/reject from chat
- **mobile-friendly approvals** — email/PagerDuty links to a lightweight approve page
- **notification preferences** — per-team channel routing, batching, quiet hours
- operators do not have to come to HivePlane to learn something happened

## Theme: Operator Surface

- CLI: register, submit, runs, approvals, triggers, tools, **certify, certs**, **health, cost, report, replay, ask, top, logs**, completions
- **`hiveplane ask` — NL operator copilot** — "why did run 42 fail?", "show team X spend last week"; itself a registered, certified, budgeted workload (the thesis, dogfooded)
- **incident mode** — `hiveplane fleet pause`: global halt, drain triggers, broadcast to owners; <5s fleet stop
- UI: fleet list, run detail, approval queue (bulk, delegation, comments), **certification dashboard**, spend view, agent health, **cost explorer, ROI + health dashboards, trigger log, queue visualizer, run timeline, diff viewer, global search, onboarding wizard**
- **UI login + RBAC-lite** — operator roles (admin/approver/viewer), per-tenant membership
- `hiveplane init` — scaffold a new project with example workloads, a seeded demo, and a sample benchmark corpus
- **onboarding wizard** — guided first run: connect model → register → certify → first trigger
- Docker Compose one-command start; PyPI distribution

## Theme: Distribution & First Run

- **first-run demo** — seeded workload that exercises the full loop: register → certify → trigger → run → intervene → deliver, in minutes
- **mega-demo** — `hiveplane init` seeds a tenant that exercises register → certify → trigger → pipeline → defend → deliver → account end-to-end
- **PyPI + Homebrew tap**; signed, SBOM'd, cosign-signed release artifacts with SLSA-style provenance
- **Helm chart and reference cluster deployment** — verified on k3d/kind
- **backup/restore** — control-plane state export/restore (migrations, DR)
- **demo profile** — pre-baked datasets + seeded failure scenarios for screenshots and talks
- **air-gapped install bundle** — offline images + seed data for restricted networks
- **federation (stretch)** — register remote planes, aggregate fleet view

---

## Theme: Multi-Agent Orchestration

- **workload pipelines** — DAG of chained agents: parent-child runs, fan-in/fan-out, handoffs with output→input mapping, pipeline-level budgets
- **task routing rules** — event-type → workload; pipeline templates; per-step approval gates
- **smart task router** — submit a plain-language task; a cheap classifier routes it to the best certified workload (one endpoint, whole fleet)
- **agent-as-tool composition** — call a certified workload as a tool inside another run; budget, policy, and certification checks propagate to the nested call
- **A2A interop (stretch)** — Agent2Agent protocol support for cross-plane agent interoperability

## Theme: Fleet Control & Scheduling

- **desired-state reconciliation (fleet-as-code / GitOps)** — declared desired fleet state in git; the controller continuously reconciles actual vs. desired: register missing agents, re-cert drifted configs, quarantine unmanaged workloads, enforce policy-pack versions
- **`hiveplane worker` daemon** — remote workers register, heartbeat, and execute runs under leases; lease expiry → automatic reassignment (distributed execution)
- **backlog autoscaling** — worker concurrency scales with queue depth, capped by a cost ceiling (cost-aware HPA)
- **priority queues + preemption** — urgent runs preempt best-effort runs with attribution; QoS classes (guaranteed / burstable / best-effort)
- **per-workload concurrency limits** and **backpressure** — run rejection with reasons
- **maintenance windows / freeze** — triggers pause, runs drain
- **dead-letter queue + replay** — failed trigger deliveries parked and replayable
- **worker identity** — signed worker tokens / mTLS between plane and workers; unauthenticated hosts are refused (rogue-host defense)
- **leader election (HA)** — multiple controller replicas coordinate, one reconciles; documented HA model (single-plane state, stateless workers)
- **chaos/game-day mode** — `hiveplane chaos`: seeded failure drills (kill a worker mid-run, revoke a cert mid-flight, force budget exhaustion, inject tool failures) to test fleet resilience on demand

## Theme: Secrets & Identity

- **per-tenant encrypted secret store** — runtime injection (env/file), never enters agent context, rotation support
- **RBAC-lite** — operator roles (admin/approver/viewer), scoped API keys, per-tenant membership, UI login
- **access audit** — operator login history, API-key usage analytics

## Theme: Artifacts & Portability

- **run artifact store** — local + S3/MinIO, retention policies, artifact links in fan-out and UI
- **`hiveplane export/import`** — shareable bundles: manifest + corpus + policy pack
- **`hiveplane wrap`** — convert an existing LangGraph/CrewAI/OpenAI-SDK app into a workload + adapter scaffold

## Theme: Corpus & Benchmark Tooling

- **corpus authoring CLI** + templates, corpus versioning and sharing
- **benchmark profiles** — fast (dev loop) / full (promotion), scheduled corpus expansion

## Theme: API, SDK & Extensibility

- **REST API v2** — OpenAPI, pagination everywhere; **Python SDK**
- **agent-as-service endpoints** — per-workload HTTP endpoint served through the plane (auth, budget, admission gates, and policy all apply)
- **per-tenant API rate limits** — request quotas with 429s per tenant
- **fleet-events webhook** — operators subscribe to run/approval/drift events
- **plugin hooks** — custom trigger sources, fan-out channels, policy checks (the community extends it after the last release)

## Theme: Reporting & Compliance

- **weekly fleet digest** — auto markdown/Slack/email: spend, drift, approvals, top/bottom ROI agents
- **audit export** (CSV/JSON) + **compliance evidence pack** — approvals + attestation history + spend
- **data retention & PII** — per-tenant retention policies, log PII scrubbing, scheduled tenant purge (right-to-delete)

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

### v0.2.0 — The Complete Fleet OS (final big release)

Everything below ships in v0.2.0. The former v0.3.0 (reliability & defense) and v0.4.0 (scale & tenancy) scopes are absorbed here.

- trigger rules (webhook/alert/PR/cron) + scheduled/watch modes + trigger DSL (payload→task templating), dedup, cooldowns, trigger→admission rules (staging auto-admit / prod gated), trigger history + audit, freeze windows, DLQ, idempotency keys, run deadlines
- **promotion gate + re-certification + regression diff + drift auto-quarantine + reinstatement**
- **certification learns from production: operator feedback → corpus, online eval sampling, workload provenance & agent signing**
- **progressive delivery: shadow runs, canary routing, auto-promote, model experiment campaigns**
- **multi-agent orchestration: pipelines, routing, handoffs, smart task router, agent-as-tool composition, A2A interop (stretch)**
- **fleet control: desired-state reconciliation (GitOps), `hiveplane worker` distributed execution, backlog autoscaling, preemption + QoS, priorities, backpressure, maintenance windows, worker identity, leader election (HA), chaos/game-day mode**
- context-aware policy + team policy packs + what-if + tool kill switch + time windows + escalation/on-call
- **defense: injection scanning + taint marks + provenance + egress allow-lists + context-window budgets + spend-velocity guards**
- **health: readiness, failure rate, MTTR, SLO, error-budget burn throttle, retries, circuit breakers, synthetic probes, plane self-monitoring, load test**
- MCP registry v2: live transport, dynamic discovery, `tools add`, trust levels
- **secrets & identity: per-tenant encrypted secret store, rotation, RBAC-lite, scoped API keys, access audit**
- fan-out (Slack/Teams/Jira/PR/PagerDuty/Discord/Linear/email/webhook) + Slack interactive approvals + mobile approvals + escalation/on-call + notification preferences
- **cost: showback, cost-per-completed-task, ROI flags, forecasts, budget periods + alerts, pre-admission estimates, model-tier routing, spend caps, result cache, chargeback metering API**
- **tenancy: full multi-tenant isolation (per-tenant budgets, policies, keys), teams as first-class entities, ROI + health dashboards**
- **artifacts & portability: artifact store (S3/MinIO), export/import bundles, `hiveplane wrap`**
- **corpus tooling: authoring CLI, versioning, benchmark profiles**
- **API v2 + Python SDK + agent-as-service endpoints + per-tenant rate limits + fleet-events webhook + plugin hooks**
- **reporting: weekly digest, audit export, compliance evidence pack, retention/PII purge**
- **operator surface: `ask` NL copilot, incident mode, global search, onboarding wizard, queue visualizer, live run view, approval UI v2, CLI depth**
- state diff / replay + run forking + A/B replay
- PydanticAI + OpenAI Agents SDK/CrewAI (stretch) adapters; conformance suite v2
- **scale & distribution: Helm chart, k3d reference deploy, backup/restore, Homebrew, signed + SBOM'd releases, demo profile, air-gapped install bundle, federation (stretch)**
- alpha → beta status; v0.1.0 → v0.2.0 migration guide; docs overhaul + operator runbook
- field test: 25 scenarios + load test (≥50 concurrent runs); article series prep (5–6 posts)

### Post-v0.2.0 — Maintenance Mode

- docs, community, articles, security patches, dependency updates
- no further feature releases

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
