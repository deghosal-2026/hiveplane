# HivePlane v0.1.0 Release Notes

**Release date:** 2026-09-25
**Tag:** `v0.1.0` (alpha / pre-release)
**Milestone:** M24 — Release Readiness

---

## What is HivePlane?

HivePlane is an open-source control plane for operating AI agent fleets. It registers
agents as first-class workloads, enforces budgets and tool policies, tracks run state,
and lets operators inspect and intervene when a run becomes unsafe or uneconomical.

Think of it as Kubernetes for agents: frameworks build one workflow; HivePlane operates
many. Its thesis is **certification**: an agent must prove itself against a reproducible
benchmark before it is admitted to production, and is re-certified when it drifts.

---

## Highlights

- **Certified control loop** — register → certify → gate → run → intervene → deliver.
- **Signed attestations** (Ed25519) verified on read; production admission requires a
  valid, unexpired certification.
- **Model-identity binding** — the runtime model is reported from actual inference and
  checked against the attestation; a mismatch blocks the run (model-swap defense).
- **Budget, policy, and sandbox enforcement** — deny-by-default tools, per-run/per-day
  budgets, isolated execution with restricted egress, and tool-output shaping + injection
  scanning.
- **Operators in control** — pause/resume/cancel, approvals, durable pause across
  restarts, complete audit trail, and a fleet dashboard.
- **One-command local stack** — Docker Compose with Postgres, Redis, OTel Collector,
  Tempo, Prometheus, and Grafana.

See [CHANGELOG.md](../../../CHANGELOG.md) for the full, categorized list.

---

## Field test results

The v0.1.0 field test exercises the certified control loop with **real agents** — two
deterministic exectrace agents (`support-agent` via raw-worker, `eval-judge` via
LangGraph) plus four negative fixtures — against the live stack on local inference.

**Overall: PASS — 10/10 scenarios, 0 failed.** All acceptance criteria A1–A20 hold:
certification with signed attestations, uncertified refused, model-swap blocked,
regression caught by re-certification, budget enforcement, shaping truncation, the
destructive approval path, durable re-attach and resume across a restart, fan-out, and
the operator surface. The container-layer suite passed **25/25**.

Full report: [FIELD_TEST_REPORT.md](../../field-test/v0.1.0/FIELD_TEST_REPORT.md).

---

## Security

Pre-release audit: trufflehog full-history scan clean (0 findings), `pip-audit` clean on
all declared dependencies (52 deps, 0 vulnerabilities), and no key material tracked.
See [security-audit.md](security-audit.md) and [SECURITY.md](../../../SECURITY.md).

---

## Installation

```bash
pip install hiveplane
```

Or run the full local stack:

```bash
git clone https://github.com/deghosal-2026/hiveplane.git
cd hiveplane
scripts/dev-up.sh          # copies .env.example -> .env, builds, starts the stack
```

Requirements: Python 3.12+ (for the package); Docker Compose for the full stack.

## Quick start

```bash
hiveplane init my-fleet          # scaffold a working project
cd my-fleet
hiveplane validate workload.yaml
hiveplane certify workload.yaml
hiveplane submit --workload workload --task "summarize open PRs"
hiveplane runs list
```

---

## Known limitations

- Coverage gate is **92%** (local 93%; CI reports 95.45% with Postgres).
- Tier 2 platform coverage is one certified agent per framework (broader coverage deferred).
- Multi-tenant support, ROI dashboards, and Helm/cluster deployment are deferred to v0.4.0.
- Distribution conveniences (Homebrew, standalone binary, GitHub Action) are deferred.

---

## Documentation

- [CHANGELOG.md](../../../CHANGELOG.md)
- [README](../../../README.md) · [User Guide](../../USER_GUIDE.md) · [Adapters](../../ADAPTERS.md) · [Observability](../../observability.md)
- [Field Test Report](../../field-test/v0.1.0/FIELD_TEST_REPORT.md)
- [Security Audit](security-audit.md)
- [WBS v0.1.0](../../wbs/v0.1.0/wbs-v0.1.0-index.md)
