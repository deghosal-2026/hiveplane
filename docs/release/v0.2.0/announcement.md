# HivePlane v0.2.0 — The Complete Fleet OS (announcement draft)

**Status:** draft for publication on release.

---

**HivePlane v0.2.0 is out.** It is the final big release — the Complete Fleet OS — and the
project is now in maintenance mode.

HivePlane is the control plane for production agent fleets. Its thesis has not changed:
**production is earned, not assumed.** An agent must pass a reproducible benchmark before it
runs in production, and be re-certified when it drifts. v0.2.0 keeps that thesis and makes the
fleet run itself, defend itself, explain itself, and scale itself.

## What's in it

- **Autonomy** — event triggers (webhook, Alertmanager, GitHub, cron) with replay protection,
  freezes, and DLQ replay; multi-agent pipelines with per-step gates; smart routing and
  agent-as-tool.
- **An immune system** — artifact-hash–bound certification, a signed transparency log with
  public verification, a promotion gate that blocks regressions, and drift **auto-quarantine**
  with certification-gated reinstatement.
- **Progressive delivery** — shadow runs, canary routing with auto-promote/abort, and model
  experiments.
- **Defense** — deterministic injection scanning, taint tracking, deny-by-default egress, a
  context-aware policy engine, and an instant fleet-wide tool kill switch.
- **Health & cost** — SLO/error budgets with burn-through, synthetic probes, per-tenant
  showback, cost-per-completed-task, and ROI flags.
- **Scale** — multi-tenancy, encrypted secrets with rotation, RBAC-lite, distributed workers,
  priority scheduling with preemption, leader election, and chaos drills.
- **Interface** — REST API v2 + Python SDK, an expanded fan-out with interactive approvals, an
  operator UI with live runs, and `ask`, a read-only natural-language copilot.
- **Distribution** — a Helm chart, k3d reference deployment, backup/restore, an air-gap bundle,
  and a signed release supply chain.

## Proven, not promised

The v0.2.0 field test drives the whole system end to end against a live stack: **36/36
scenarios**, **67/67 container/API/UI tests**, and a **50/50 concurrency load test**. It found
**ten product defects** the unit suite could not — and every one is fixed and re-verified.
Full report: `docs/field-test/v0.2.0/FIELD_TEST_REPORT.md`.

## Get it

```bash
pip install hiveplane==0.2.0
```

- Release notes: `docs/release/v0.2.0/release-notes.md`
- Release: https://github.com/deghosal-2026/hiveplane/releases/tag/v0.2.0
- Upgrading from v0.1.0: `docs/release/v0.2.0/migration-guide.md`
- Tutorials: `docs/tutorials/` · Architecture tour: `docs/architecture-tour.md`

## What's next

Maintenance mode: documentation, community, articles, and security patches — no further
feature releases. See the backlog.

---

### Publication checklist (for the maintainer)

- [ ] Publish this post (project blog / dev.to / HN / r/kubernetes / LocalLLaMA)
- [ ] Announce on the community channel(s)
- [ ] Link the six-part article series (`docs/articles/`)
- [ ] Pin the release and open the maintenance backlog
