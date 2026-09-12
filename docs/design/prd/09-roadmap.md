# PRD 09: Roadmap

## TLDR

Four versions take HivePlane from a certified control loop to a self-operating, drift-aware, multi-tenant fleet platform. Certification ships in v0.1.0 because it is the thesis, not a feature.

## Timeline

| Version | Theme | Focus |
|---------|-------|-------|
| v0.1.0 | **Certified control loop** | Registry, run lifecycle, budget, safe execution, **certification pipeline (benchmark + attestation + admission gate)**, minimal UI, init + demo |
| v0.2.0 | **Self-operating fleet** | Triggers, promotion gate + re-certification, drift detection + auto-quarantine, context-aware policy, MCP tool registry, result fan-out, cost showback |
| v0.3.0 | **Reliability & defense** | Agent health/SLO, drift hardening, injection defense, multi-runtime |
| v0.4.0 | **Scale & tenancy** | Multi-tenant, ROI dashboards, Helm chart, cluster deployment |

## v0.1.0 — Certified Control Loop

**The thesis ships here.** An agent is registered, certified against a benchmark, and only then allowed to run in production.

### Ships

- registry, manifest validation, versioning, `--dry-run`
- **benchmark runner + certification engine + signed attestation**
- **certification status enforced at admission** (uncertified → refused)
- task submission, run state machine, pause/resume/cancel, durable state
- budget enforcement per run/day
- deny-by-default policy + approvals + audit
- execution isolation + resource/output caps
- tool-output shaping
- raw-worker + LangGraph adapters, conformance suite
- OTel traces/metrics/logs, trace-linked debug context
- CLI + minimal UI (fleet list, run detail, approval queue, certification dashboard, spend view)
- `hiveplane init` + seeded demo + Docker Compose

### Done When

- Three real agents registered
- At least one certified for production via benchmark
- An uncertified agent is refused admission to production
- Operators can inspect and stop any run from one surface
- Budget enforcement blocks an over-budget run
- Attestation is signed and verified on read

## v0.2.0 — Self-Operating Fleet

### Ships

- trigger rules (webhook/alert/PR/cron) + scheduled/watch modes
- **promotion gate + re-certification + regression diff**
- **drift detector + auto-quarantine**
- context-aware policy + team policy packs
- MCP tool registry + tool trust levels
- result fan-out (Slack/Teams/Jira/PR/webhook)
- cost showback + ROI flags + cost-per-completed-task
- state diff/replay helpers
- approval queue UI, richer policy, budget analytics

### Done When

- Drift detector auto-quarantines a seeded drifting agent
- Promotion gate blocks a regression and produces a replayable diff
- Triggers fire from ≥ 2 sources
- Result fan-out delivers to ≥ 2 channels
- Cost showback attributes spend by team and agent

## v0.3.0 — Reliability & Defense

### Ships

- agent health model (readiness, failure rate, SLO, drift)
- reliability metrics and SLO hooks
- prompt-injection / adversarial input defense
- multi-runtime support (≥ 3 adapter types)
- replay helpers (if not already shipped)

### Done When

- Agent health dashboard shows readiness, failure rate, SLO, drift
- Injection defense blocks a seeded injection via tool output
- Multi-runtime support verified

## v0.4.0 — Scale & Tenancy

### Ships

- multi-tenant support
- ROI dashboards (fleet-wide)
- Helm chart and reference cluster deployment
- PyPI + Homebrew distribution hardening

### Done When

- Multi-tenant isolation verified
- Helm chart deploys to a reference cluster
- ROI dashboard shows fleet-wide spend vs. outcome

## Release Cadence

Each version ships with: release notes (`docs/release/`), field test report (`docs/field-test/`), updated WBS (`docs/wbs/`), and a benchmark corpus update.

## See Also

- [Features](05-features.md)
- [Success metrics](07-success-metrics.md)
- [Risks](08-risks.md)
- [v0.1.0 WBS](../../wbs/v0.1.0/wbs-v0.1.0-index.md)
