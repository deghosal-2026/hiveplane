# HivePlane v0.1.0 Release Notes

**Release date:** TBD
**Tag:** v0.1.0
**Milestone:** M19 — Release Readiness

---

## What is HivePlane?

HivePlane is an open-source control plane for operating AI agent fleets. It registers agents as first-class workloads, enforces budgets and tool policies, tracks run state, and lets operators inspect and intervene when a run becomes unsafe or uneconomical.

Think of it as Kubernetes for agents: frameworks build one workflow; HivePlane operates many.

---

## What's New in v0.1.0

### Core Control Loop

- Agent registry with owner, runtime type, and policy metadata
- Task submission API with persistent run state
- Run state machine: queued → running → paused/completed/failed
- Budget enforcement per run and per day
- Pause, resume, and cancel controls
- Auditable operator actions

### Runtime Adapters

- Raw Python worker reference adapter
- LangGraph example adapter
- Adapter conformance suite

### Observability

- OpenTelemetry-native traces, metrics, logs, and audit events
- Fleet metrics: runs by state, budget burn, failures, escalations
- Trace-linked debug context per run

### Operator Surface

- CLI: register, submit, runs, approvals
- Minimal UI: fleet list, run detail, approval queue, spend view

### Operations

- Docker Compose reference stack (one-command start)
- PostgreSQL state store with migrations
- Example workloads and a seeded demo

---

## Field Test Results

_To be completed after M18._

---

## Security

- Deny-by-default tool policy
- Budget enforcement before expensive work
- Tamper-evident audit log
- Secrets redacted from logs, traces, and audit events

---

## Known Limitations

- v0.1.0 targets three workloads and two adapters
- Multi-tenant support deferred to v0.4.0
- ROI dashboards deferred to v0.4.0
- Helm chart and cluster deployment deferred to v0.4.0

---

## Installation

```bash
docker compose up -d
pip install -e .
```

## Quick Start

```bash
hiveplane register examples/workloads/example-agent.yaml
hiveplane submit --agent example-agent --task "summarize open PRs"
hiveplane runs list
```

## Documentation

- [User Guide](../../USER_GUIDE.md)
- [Adapters](../../ADAPTERS.md)
- [Observability](../../observability.md)
- [Field Test Plan](../../field-test/v0.1.0/field-test-plan.md)
- [WBS v0.1.0](../../wbs/v0.1.0/wbs-v0.1.0-index.md)
