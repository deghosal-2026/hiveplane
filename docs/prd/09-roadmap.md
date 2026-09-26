# PRD 09: Roadmap

## TLDR

Two releases: v0.1.0 proved the certified control loop (shipped 2026-09-25). **v0.2.0 is the final big release — the Complete Fleet OS** — absorbing everything once planned for v0.3.0/v0.4.0 plus the expanded feature catalog. The fleet runs itself (triggers, pipelines, GitOps reconciliation), defends itself (drift, injection, policy, kill switches), explains itself (cost/ROI, reporting), and scales itself (tenancy, distributed workers, Helm). After v0.2.0: maintenance mode. Certification remains the thesis, not a feature.

## Timeline

| Version | Theme | Focus |
|---------|-------|-------|
| v0.1.0 | **Certified control loop** | ✅ SHIPPED 2026-09-25 — registry, run lifecycle, budget, safe execution, certification pipeline (benchmark + attestation + admission gate), minimal UI, init + demo |
| v0.2.0 | **The Complete Fleet OS — final big release** | Everything: autonomy (triggers, pipelines), immune system (promotion gate, drift quarantine, shadow/canary), defense (injection, policy, kill switch), health (SLO, burn, probes), MCP v2, secrets + RBAC, 9-channel fan-out, cost/ROI + showback + metering, multi-tenancy, Helm + distributed workers, API v2 + SDK + plugins, reporting, `ask` copilot, GitOps reconciliation |
| post-v0.2.0 | **Maintenance mode** | Docs, community, articles, security patches — no further feature releases |

## v0.1.0 — Certified Control Loop

**The thesis ships here.** An agent is registered, certified against a benchmark, and only then allowed to run in production.

### Ships

- registry, manifest validation, versioning, `--dry-run`
- **LLM provider seam** (local Ollama/OMLX + cloud OpenAI + fake for CI) and **agent invocation/tool execution seams**
- **benchmark runner + certification engine + signed attestation**
- **adapter-backed certification** — the benchmark runs the real agent entrypoint
- **durable signing keypair** — attestations verify across restarts
- **certification status enforced at admission** (uncertified → refused)
- task submission, run state machine, pause/resume/cancel, durable state
- **durable resume** — a paused run survives a control-plane restart
- budget enforcement per run/day
- deny-by-default policy + approvals + audit
- **approval re-dispatch** — an escalated tool call executes after approval
- execution isolation + resource/output caps
- tool-output shaping
- raw-worker + LangGraph adapters, conformance suite
- **PostgreSQL-backed stores** (registry, budget, certifications, attestations, approvals) + **auto-migration on startup**
- OTel traces/metrics/logs, trace-linked debug context
- CLI + minimal UI (fleet list, run detail, approval queue, certification dashboard, spend view)
- `hiveplane init` + seeded demo + Docker Compose

### Done When

- Three real agents registered
- At least one certified for production via benchmark, with the real agent executing in the benchmark
- An uncertified agent is refused admission to production
- A real model call flows through the control plane (local or cloud)
- Operators can inspect and stop any run from one surface
- Budget enforcement blocks an over-budget run
- Attestation is signed and verified on read, and still verifies after a restart
- A paused run survives a restart and resumes
- Docker Compose starts a functional stack with one command (auto-migration included)

## v0.2.0 — The Complete Fleet OS (final big release)

The last big release. Everything planned for v0.3.0 and v0.4.0 is absorbed here, plus the expanded catalog. Sequenced as **M25–M62 (38 milestones, 19 parts)** — roughly 3× v0.1.0; estimate **4–5 months**. See the [v0.2.0 WBS](../wbs/v0.2.0/wbs-v0.2.0-index.md) for the full breakdown. If load-shedding is ever needed, the natural cut order (last out): federation → demo profile → global search → onboarding wizard. The crown jewels that must never be cut: pipelines + canary, GitOps reconciliation, context budgets, `ask`.

### Ships — by pillar

| Pillar | Contents |
|---|---|
| A. Autonomy | Triggers (webhook/PR/alert/cron/watch), trigger DSL (payload→task templating), dedup, cooldowns, trigger→admission rules (staging auto-admit / prod gated), trigger history + audit, freeze windows, DLQ + replay, idempotency keys, run deadlines |
| B. Orchestration | Workload pipelines (DAG, parent-child, handoffs, pipeline budgets), task routing, smart task router, agent-as-tool composition (budget/policy/cert propagation), A2A interop (stretch) |
| C. Immune system | Promotion gate, re-certification, regression diff, drift + auto-quarantine + reinstatement, expiry windows, attestation transparency log + public verification, production feedback → corpus, online eval sampling, workload provenance & agent signing |
| D. Progressive delivery | Shadow runs, canary routing, auto-promote, model experiment campaigns |
| E. Defense | Injection defense + taint/provenance, context-aware policy + what-if, team policy packs, tool kill switch, time windows, egress allow-lists, context-window budgets, spend-velocity guards |
| F. Health & reliability | Health model, SLO/error budget, burn throttle, retries, circuit breakers, synthetic probes, MTTR, plane self-monitoring, load test |
| G. Tools | MCP registry v2: live transport, dynamic discovery, `tools add`, trust levels |
| H. Secrets & identity | Per-tenant encrypted secret store, injection + rotation, RBAC-lite, scoped API keys, access audit |
| I. Delivery | 9 fan-out channels, Slack interactive approvals, mobile approvals, escalation/on-call, notification preferences |
| J. Cost & ROI | Tenants/teams, showback, cost-per-task, ROI flags, forecasts + overrun prediction, budget periods + alerts, pre-admission estimates, model-tier routing, spend caps, result cache, chargeback metering API, ROI dashboards |
| K. Fleet control | Desired-state reconciliation (GitOps), `hiveplane worker` distributed execution, backlog autoscaling, preemption + QoS, priorities, backpressure, maintenance windows, worker identity (signed tokens/mTLS), leader election (HA), chaos/game-day mode |
| L. Tenancy & scale | Full multi-tenant isolation, Helm chart, k3d reference deploy, backup/restore, Homebrew, signed + SBOM'd releases with SLSA-style provenance, air-gapped install bundle, federation (stretch), demo profile |
| M. Artifacts & portability | Artifact store (S3/MinIO) + retention, export/import bundles, `hiveplane wrap` |
| N. Corpus tooling | Authoring CLI + templates, versioning, benchmark profiles (fast/full) |
| O. API & extensibility | REST API v2 (OpenAPI), Python SDK, agent-as-service endpoints, per-tenant rate limits, fleet-events webhook, plugin hooks (triggers/channels/policy checks) |
| P. Reporting & compliance | Weekly fleet digest, audit export, compliance evidence pack, retention/PII purge |
| Q. Operator surface | `ask` NL copilot (a certified workload itself), incident mode, global search, onboarding wizard, queue visualizer, live run view, approval UI v2, CLI (health/cost/report/replay/top/logs/completions) |
| R. Runtime breadth | PydanticAI + OpenAI Agents SDK/CrewAI (stretch) adapters, conformance suite v2 |
| S. Polish | alpha → beta, migration guide, docs overhaul + runbook, mega-demo, field test 25 scenarios + 50-concurrent load test, article prep (5–6 posts) |

### Done When

1. Seeded drifting agent auto-quarantined, team notified, reinstated after re-cert
2. Promotion gate blocks a regression with a replayable diff
3. Triggers fire from ≥3 sources with dedup/cooldown proven
4. Seeded injection blocked; repeated attempts quarantine the agent
5. Slack approvals + fan-out to ≥3 channels; approvals work from mobile
6. Per-tenant budget/policy/key isolation verified; viewer role cannot approve
7. Helm chart deploys the full stack to a k3d cluster
8. Health dashboard shows readiness/failure/SLO burn; burn-through throttles; a circuit breaker trips and recovers
9. Showback attributes cost by tenant → team → agent with cost-per-completed-task
10. Frame-by-frame replay + run diff works; a forked run re-runs with edited state
11. A pipeline runs a multi-agent DAG end-to-end with per-step gates
12. Canary routes 10% to a candidate and auto-promotes on clean results
13. A secret never appears in logs/traces/agent context (verified by test)
14. A dead trigger replays from the DLQ; a circuit breaker trips and recovers
15. SDK + API v2 round-trip a full run; a plugin hook fires
16. Weekly digest auto-generates; an artifact is stored, linked, and retained per policy
17. Git deletes an agent → the plane deregisters it; git changes a cert threshold → re-cert fires (reconciliation)
18. An urgent run preempts a best-effort run with attribution
19. A worker daemon executes a run on a second host; kill it → lease expiry reassigns the run
20. Context budget exceeded → run pauses cleanly with accounting shown
21. A cache hit reuses a result and shows savings; re-cert invalidates it
22. A synthetic probe flags decay before the drift threshold trips
23. `ask` answers 5 live-state questions, itself under budget + cert
24. Incident mode halts the fleet in <5s and broadcasts
25. An attestation verifies publicly by ID; the kill switch disables a tool fleet-wide instantly
26. Signed image + SBOM published with the release; retention purge deletes tenant data on schedule
27. An operator-flagged failed run becomes a corpus case included in the next certification
28. Sampled production runs receive judge scores; a quality dip alerts before scheduled re-cert
29. A modified agent bundle fails admission on provenance-signature mismatch
30. A worker without a signed token is refused
31. Agent-as-tool calls propagate budget/policy/certification to the nested run
32. A second controller replica does not double-reconcile (leader election verified)
33. Chaos drills: kill a worker mid-run → lease reassigns the run; revoke a cert mid-flight → run halts
34. A per-workload service endpoint serves a run through all gates; over-limit tenants get 429s

## Former v0.3.0 / v0.4.0 — absorbed into v0.2.0

Reliability & defense (agent health, SLO, injection defense, multi-runtime) and scale & tenancy (multi-tenant, ROI dashboards, Helm chart, distribution hardening) ship as part of the v0.2.0 Complete Fleet OS. There are no v0.3.0/v0.4.0 feature releases.

## Release Cadence

Each version ships with: release notes (`docs/release/`), field test report (`docs/field-test/`), updated WBS (`docs/wbs/`), and a benchmark corpus update.

## See Also

- [Features](05-features.md)
- [Success metrics](07-success-metrics.md)
- [Risks](08-risks.md)
- [v0.1.0 WBS](../wbs/v0.1.0/wbs-v0.1.0-index.md)
