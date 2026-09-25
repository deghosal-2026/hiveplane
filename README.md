# HivePlane — Control Plane for Production Agent Fleets

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#mvp-010)
[![PyPI](https://img.shields.io/badge/pypi-hiveplane-blue.svg)](https://pypi.org/project/hiveplane/)

> A Kubernetes-like control plane for AI agents: register agents, define budgets and permissions, route tasks, inspect workflow state, and intervene when a run becomes unsafe or uneconomical.

---

## Install & Quick Start

```bash
pip install hiveplane          # Python 3.12+
hiveplane init my-fleet        # scaffold a working project
cd my-fleet
hiveplane validate workload.yaml
hiveplane certify workload.yaml
hiveplane submit --workload workload --task "summarize open PRs"
hiveplane runs list
```

Run the full local stack (API, UI, Postgres, Redis, telemetry) with Docker Compose:

```bash
scripts/dev-up.sh
```

See the [User Guide](docs/USER_GUIDE.md) for the operator workflow, and the
[v0.1.0 release notes](docs/release/v0.1.0/release-notes.md) for what ships in this release.

---

## Why This Project Exists

Agent frameworks solve orchestration inside one workflow. They do not solve the fleet-level operating model. Once a team runs multiple agents across CI, incident response, repo analysis, delivery workflows, docs, and governance, the same operational pain appears everywhere:

- each agent has different budget logic
- each agent logs differently
- approvals are inconsistent
- spend visibility is fragmented
- pausing or replaying a run is custom per agent
- ownership is unclear when something goes wrong

That fragmentation is exactly the kind of systems problem a control plane should solve. The control plane becomes the place where teams define desired state for agent workloads and observe actual runtime state: who owns each agent, what capabilities it has, what tools it may call, how much it may spend, what approvals it requires, and how it is behaving right now.

The deeper reason this project matters is strategic. The AI ecosystem has many builders and not enough operators. A mature open-source control plane for agents stands out because it answers the question advanced teams now care about: not "can the agent do something useful?" but "can I run a fleet of them safely, predictably, and transparently?"

**Painful truth:** Most teams can build one impressive agent. Very few can operate 10 agents with consistent policy, observability, and spend discipline. The bottleneck is not model quality — it is platform operations.

---

## Who It's For

### Primary User

Platform engineering teams running or planning to run multiple internal AI agents.

### Secondary Users

- SRE or AI platform teams responsible for runtime reliability and governance
- engineering enablement teams building reusable AI workflows
- OSS maintainers building multi-agent platforms and wanting a better operating model

### Not For

- hobbyists who only run one simple chat-style agent
- teams looking for a no-code business assistant tool
- users who want a thin wrapper over one framework without platform concerns

---

## User Problems

### Problem 1: No Shared Runtime Model

Each agent is operated differently. That creates local optima and global confusion.

### Problem 2: Weak Governance

Tool permissions, approval boundaries, cost budgets, and escalation policies are all implemented differently or not at all.

### Problem 3: Poor Debuggability

When an agent misbehaves, operators often need to jump across separate dashboards, log formats, and ad hoc scripts.

### Problem 4: No Fleet-Level Visibility

Teams can see agent-by-agent behavior, but cannot answer simple questions like:

- which agents are expensive but low-value
- which teams are using which agents
- which agents fail verification most often
- which policies generate the most escalations

---

## Vision / Final State

An engineering organization runs 8-20 internal agents. Every one of them is registered in HivePlane as a first-class workload.

For each agent, the platform defines:

- owner team
- runtime adapter
- allowed tools
- model strategy or router policy
- per-run and per-day budgets
- escalation contacts
- required approvals
- observability contract

An operator opens the console and sees:

- all agents in the fleet
- health and recent failures
- active runs and queued runs
- budget burn by team and agent
- pending approvals
- policy violations and escalations

If a workflow becomes unsafe, the operator can pause it, inspect its state, resume it with modified context, or terminate it. If a new agent is added, it plugs into the same runtime contract instead of inventing custom operational behavior.

The end state is not "more agents." It is "agents that are operable like a platform."

---

## Core Workflows

### Workflow 1: Register a New Agent

1. Platform engineer defines the agent workload manifest.
2. Manifest includes owner, runtime type, tool permissions, budget rules, approval requirements, and observability metadata.
3. HivePlane validates the manifest and stores desired state.
4. Agent appears in the fleet catalog and can accept runs.

### Workflow 2: Submit and Track a Run

1. Client submits task to control plane.
2. Control plane assigns run ID and runtime adapter.
3. State transitions are persisted from queued → running → paused/completed/failed.
4. Budget burn and tool activity are tracked during execution.
5. Operator or caller can inspect the run in real time.

### Workflow 3: Policy or Budget Intervention

1. A run exceeds budget or hits a guarded tool call.
2. Policy engine marks the run for escalation.
3. Operator receives alert and sees evidence.
4. Operator can approve continuation, edit state, or stop the run.

### Workflow 4: Fleet Review

1. Team lead opens dashboard weekly.
2. Reviews spend, usage, failures, and approvals by team.
3. Identifies low-value or risky agents.
4. Tightens budgets or adjusts policies from one place.

---

## Architecture Direction

### Core Components

1. **Registry Service** — stores agent definitions, owners, budgets, policies, runtime adapter type, and metadata.
2. **Execution API** — accepts new tasks, exposes run status, and serves intervention actions.
3. **Runtime Adapters** — translate between control-plane concepts and actual runtime frameworks like LangGraph or raw Python workers.
4. **Policy Engine** — evaluates tool permissions, budget thresholds, and approval rules.
5. **State Store** — persistent run state, desired state, audit history, policy outcomes, and operator actions.
6. **Telemetry Pipeline** — OTel traces, Prometheus metrics, structured logs, and audit events.
7. **Operator UI** — fleet dashboard, run detail pages, approval queue, spend views, and incident-friendly search.

### Design Principles

- agent runtimes stay pluggable
- the workload contract stays stable
- operator actions are auditable
- policy should be visible, not hidden in code
- the system must be useful locally before it claims scale

---

## Local-First OSS Stack

### Core Stack

- API: `FastAPI`
- State store: `PostgreSQL`
- Queue/signaling: `Redis` or `NATS`
- Runtime examples: `LangGraph`, raw Python workers
- Telemetry: `OpenTelemetry Collector`, `Tempo`, `Prometheus`, `Grafana`
- UI: server-rendered `FastAPI` + `Jinja2` (Python); a React SPA is planned (see [Operator UI design](docs/design/operator-ui-design.md))
- Packaging: `Docker Compose` first, `k3d` second

### Why This Stack

Every part can run locally, is widely understood, and reinforces the cloud-native + AI-native platform story. None of it requires paid infrastructure to validate the project.

---

## Data / Tools / Integrations

### Inputs

- agent workload manifests
- task submission payloads
- runtime state updates
- tool call metadata
- spend and token usage telemetry
- operator approvals and overrides

### Outputs

- fleet dashboard views
- run status APIs
- audit trails
- budget alerts
- approval queues
- trace-linked debug context

### Good Initial Integrations

- LangGraph agent examples
- LoopGuard-like budget/intervention hooks
- TierForge-like cost accounting
- Slack webhook for approvals
- GitHub issue or PR event integration for agent runs

---

## Product Scope

### In Scope

- fleet registry and workload model
- run lifecycle management
- budget and quota enforcement
- approval and intervention hooks
- trace, metric, and audit integration
- simple but real operator UI

### Out of Scope for Initial Versions

- building a new agent framework
- replacing model providers
- generalized workflow authoring UI
- full enterprise IAM complexity
- autonomous self-healing logic for every failure mode

## Non-Goals

- Compete with LangGraph, AutoGen, or similar orchestration frameworks
- Become a generic workflow engine for non-AI workloads
- Support every agent runtime in v1
- Solve agent evaluation and observability completely inside the same repo

---

## MVP 0.1.0

### Must-Have

- agent registry with owner, runtime type, and policy metadata
- task submission API with persistent run state
- budget enforcement per run
- pause, resume, and cancel controls
- basic audit log
- trace and metric export
- minimal operator UI with fleet list and run detail

### Nice-to-Have if Time Allows

- Slack notification on approval-needed state
- simple policy packs by team
- example adapter for one LangGraph workflow and one raw worker

### What Makes `0.1.0` Good Enough

If three real agents can run through the same lifecycle and operators can meaningfully inspect and stop them from one place, the MVP is real.

---

## Milestones

### v0.2.0

- approval queue UI
- richer policy conditions
- budget analytics by agent/team
- stronger adapter contract

### v0.3.0

- multi-runtime support
- state diff and replay helpers
- reliability metrics and SLO hooks

### v0.4.0

- multi-tenant support
- ROI dashboards
- Helm chart and reference cluster deployment

---

## Success Metrics

### Product Metrics

- number of agents successfully onboarded
- median time to inspect and stop a bad run
- percentage of runs with complete audit trail
- budget overrun incidents caught before manual discovery

### OSS Metrics

- GitHub stars and issues from real platform users
- external experiments or adoption writeups
- article engagement and discussion quality

---

## Risks / Hard Parts

- abstracting runtimes without becoming vague
- not over-designing for scale too early
- making policy visible and usable instead of annoying
- proving value beyond a dashboard
- resisting the temptation to build too much UI before the control loop is solid

---

## Build Order / First Step

Take 2-3 existing agents you already own. Define one common workload manifest for all of them. Then build the smallest control loop:

1. register workload
2. submit task
3. persist state transitions
4. enforce budget
5. stop run from operator API

**Why this first:** because it proves the category with real workloads instead of speculative architecture.

---

## Development

### Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### Quality gates

```bash
make test    # pytest
make cov     # pytest with coverage report (> 92% required)
make lint    # ruff
make type    # mypy (strict)
make check   # lint + type + cov
make test-e2e  # operator UI browser tests (Playwright, Chromium)
```

CI (`.github/workflows/ci.yml`) runs the same gates on Python 3.12 and 3.13, fails the build below 92% coverage, and runs the operator UI browser tests in a separate job.

### Local stack

```bash
scripts/dev-up.sh        # copies .env.example -> .env, builds, starts the stack
docker compose ps
docker compose down
```

Services: API (`:8100`), Operator UI (`:3001`), PostgreSQL (`:5432`), Redis (`:6379`), OpenTelemetry Collector (`:4317` gRPC / `:4318` HTTP), Tempo (`:3200`), Prometheus (`:9090`), Grafana (`:3000`). Every service has a healthcheck. Host port 8000 is reserved for a local OMLX (`mlx_lm.server`) endpoint.

### Configuration

Settings load from environment variables prefixed `HIVEPLANE_`, with `__` separating nested sections (for example `HIVEPLANE_DATABASE__HOST`), falling back to a `.env` file and then defaults. See `.env.example` for the full reference.

## Documentation

Full index: [docs/README.md](docs/README.md).

**Guides**

- [User Guide](docs/USER_GUIDE.md) — operator workflow
- [Adapters](docs/ADAPTERS.md) — adapter contract, conformance, sandbox, shaping, model binding
- [Observability](docs/observability.md) — signals, agent health, certification metrics, cost showback
- [Workload Manifest Format Spec](docs/workloads/manifest-format-spec.md) · [JSON Schema](docs/workloads/manifest.schema.json)
- [Contributing Workloads](docs/workloads/CONTRIBUTING.md)
- [Example workloads](examples/workloads/README.md) · [Scripts](scripts/README.md)

**Release v0.1.0**

- [Release Notes](docs/release/v0.1.0/release-notes.md)
- [CHANGELOG](CHANGELOG.md)
- [Field Test Report](docs/field-test/v0.1.0/FIELD_TEST_REPORT.md) · [Field Test Plan](docs/field-test/v0.1.0/field-test-plan.md)
- [Docker Test Report](docs/field-test/v0.1.0/DOCKER_TEST_REPORT.md) · [Docker Test Plan](docs/field-test/v0.1.0/docker-test-plan.md)
- [Security Audit](docs/release/v0.1.0/security-audit.md)
- [WBS v0.1.0](docs/wbs/v0.1.0/wbs-v0.1.0-index.md)

**Design & requirements**

- [PRDs](docs/prd/) — why, architecture, landscape, users, features, security, metrics, risks, roadmap
- [Design documents](docs/design/) — subsystem designs and [design decisions](docs/design/design-decisions.md)

## Project

- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Security Policy](SECURITY.md)
- [License](LICENSE)

## License

MIT — see [LICENSE](LICENSE).
