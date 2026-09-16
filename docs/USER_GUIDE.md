# HivePlane User Guide

> Status: draft — to be completed with v0.1.0.

## What HivePlane Is

HivePlane is a control plane for operating a fleet of AI agents as first-class workloads. You register each agent, declare who owns it, what tools it may call, what it may spend, and what approvals it requires — then observe and intervene from one operator surface.

## Prerequisites

- Docker (for the reference local stack)
- Python 3.11+ (for the API and worker examples)

## Quick Start

```bash
# 1. Start the local control plane
docker compose up -d

# 2. Register an agent workload
hiveplane register examples/workloads/example-agent.yaml

# 3. Submit a task
hiveplane submit --agent example-agent --task "summarize open PRs"

# 4. Inspect the run
hiveplane runs list
hiveplane runs show <run-id>

# 5. Intervene
hiveplane runs pause <run-id>
hiveplane runs resume <run-id>
hiveplane runs stop <run-id>
```

## Core Concepts

| Concept | Meaning |
|---------|---------|
| **Agent workload** | A registered agent with an owner, runtime, tools, budget, and approval policy |
| **Manifest** | The declarative YAML that defines a workload |
| **Run** | One execution of a workload, with persistent state |
| **Policy** | Rules governing tool permissions, budgets, and approvals |
| **Adapter** | The bridge between HivePlane and a runtime (LangGraph, raw worker) |

See [prd/04-users-and-cujs.md](prd/04-users-and-cujs.md) for critical user journeys.

## Execution API

The run lifecycle is exposed over HTTP:

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/runs` | Submit a run for admission |
| `GET` | `/runs` | List runs (`?workload=`, `?state=`) |
| `GET` | `/runs/{id}` | Inspect a run |
| `GET` | `/runs/{id}/events` | Read the attributed event log |
| `GET` | `/runs/{id}/usage` | Read usage reports |
| `POST` | `/runs/{id}/pause` | Pause a running run |
| `POST` | `/runs/{id}/resume` | Resume a paused run |
| `POST` | `/runs/{id}/stop` | Stop a run immediately |

Submission runs admission checks in order — certification status, model-identity
binding, budget, policy, and sandbox requirement. A refusal returns `403` with the
failing step and reason. Illegal transitions return `409`, and unknown runs `404`.

Run state is persisted through a pluggable store (in-memory, or JSON file for
local durability), every transition is recorded as an attributed event, and
terminal runs fan out to the destinations configured in `spec.fan_out` (Slack and
generic webhook in v0.1.0).

> Status: the policy, budget, and sandbox engines are wired in. PostgreSQL
> persistence lands with the state-store milestone.

## Policy and Approvals

Policy is evaluated deny-by-default and returns an explainable decision (the
originating `rule`, a `reason`, and a blast-radius score):

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/policy/evaluate` | Evaluate policy for a context (tool, environment, sensitivity) |
| `GET` | `/policy-packs` | List team policy packs |
| `POST` | `/policy-packs` | Register a team policy pack |
| `GET` | `/approvals` | List approval requests (`?status=`, `?workload=`) |
| `GET` | `/approvals/{id}` | Inspect an approval request |
| `POST` | `/approvals/{id}/approve` | Approve and resume the paused run |
| `POST` | `/approvals/{id}/deny` | Deny and fail the paused run |

A destructive tool, an explicit `require_approval`, or an action class listed in
`spec.approvals.required_for` escalates a run: admission pauses it, requests an
approval, and fans out to `spec.fan_out.on_escalation`. Approving resumes the
run; denying fails it with the recorded reason.

## Budget

Usage is priced from the exact model identity via a per-model cost table
(token prices per 1,000 tokens); an unknown model fails loudly rather than
silently costing zero. Spend is enforced at three levels:

- **Per run** — `spec.budget.per_run_usd`
- **Per day** — `spec.budget.per_day_usd` (per workload)
- **Per team** — `spec.budget.per_team_usd` (aggregate across the team)

Admission checks day and team headroom before a run is queued. As usage is
recorded, spend accumulates against the run, the workload's day total, and the
team's day total; a run that exceeds its limit transitions to `failed` with the
budget reason recorded. Every usage event is attributed for showback
(`budget.models.CostAttribution`).

## Execution Sandbox and Output Shaping

Sandboxed runs (a workload with `spec.sandbox.enabled`, or any run in the
`sandbox` context) are provisioned an isolated execution context when they start
and torn down on every terminal transition. The local backend runs the workload
in a subprocess with:

- **Resource caps** — memory (`RLIMIT_AS`), CPU (`RLIMIT_CPU`), and a wall-clock
  watchdog; a run that exceeds the wall-clock cap is terminated and reported as
  `failed` with reason `timeout`.
- **Restricted egress** — `EgressGuard` enforces the manifest allowlist and
  always denies cloud metadata endpoints (`169.254.169.254`).
- **Isolated filesystem** — an ephemeral scratch directory, removed on exit.

Tool outputs are shaped before they reach the agent via `ShapingPipeline`:
filter (redact/mask), truncate to `max_bytes` (head/tail/summary), a cumulative
per-run output budget, and an injection scan. High-confidence injection patterns
are blocked; lower-confidence patterns escalate; benign output passes unchanged.

## Certification

Certification is the thesis: an agent earns production admission by passing a
reproducible benchmark corpus, and the result is a signed attestation.

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/certifications` | Run a workload's corpus and certify it |
| `GET` | `/certifications` | List records (`?workload=`, `?status=`) |
| `GET` | `/certifications/{id}` | Show a record (certification, attestation, result) |
| `GET` | `/certifications/compare/{v1}/{v2}` | Per-task regression diff between two certifications |

From the CLI:

```bash
hiveplane certify repo-agent --context staging
hiveplane certs list --workload repo-agent
hiveplane certs show <certification-id>
hiveplane certs compare <v1> <v2>
```

A corpus is a versioned `corpus.yaml` (see
[corpus-format.md](workloads/corpus-format.md)). The runner evaluates
deterministic `exact_match` and `action_audit` checks; the engine applies
staging/production thresholds and advances status (`uncertified → provisional →
certified`, or `quarantined` on a failed re-certification). Each certification
produces an Ed25519-signed attestation stored append-only and verified on every
read. Production admission requires the referenced attestation to verify and the
run's model identity to match the attestation — a model swap is refused.

> Local runs use a `ReferenceExecutor` that replays each task's declared outcome
> until runtime adapters (Part 8) land. It exercises the full flow but does not
> measure a real agent.

## Configuration

Configuration options and environment variables are documented as they land in v0.1.0.

## Troubleshooting

To be completed alongside v0.1.0.

## See Also

- [Docs index](README.md)
- [Workload manifest format](design/workload-manifest-design.md)
- [Adapters](ADAPTERS.md)
