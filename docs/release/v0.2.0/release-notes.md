# HivePlane v0.2.0 Release Notes

**Release date:** 2026-10-01
**Tag:** `v0.2.0` (beta)
**Milestones:** M25–M62 — the Complete Fleet OS

---

## What is HivePlane?

HivePlane is an open-source control plane for operating AI agent fleets. It registers agents
as first-class workloads, enforces certification, budgets, and tool policy, tracks run state,
and lets operators inspect and intervene when a run becomes unsafe or uneconomical.

Its thesis is **certification**: an agent must prove itself against a reproducible benchmark
before it is admitted to production, and is re-certified when it drifts. v0.2.0 keeps that
thesis and makes the fleet **run itself, defend itself, explain itself, and scale itself**.

---

## Highlights

Every capability once planned for v0.3.0 and v0.4.0 is absorbed into this release.

- **Tenancy foundation** — a `tenant_id` on every table, a single `TenantContext` boundary,
  per-tenant budgets/policy packs/keys/secrets, tenant lifecycle and quotas, and an
  adversarial isolation suite. A viewer can never approve; cross-tenant access looks like
  "not found".
- **Autonomy** — event triggers (webhook, Alertmanager, GitHub, cron, watch) with HMAC
  replay protection, dedup/cooldown/rate limiting, freezes and DLQ replay; validated
  multi-agent pipelines with per-step gates, budgets, and schema-checked handoffs; a smart
  task router and agent-as-tool with depth/cycle guards.
- **The immune system** — artifact-hash–bound certification, an Ed25519 transparency log
  with public verification, workload-bundle provenance signing, a promotion gate that
  blocks regressions with a replayable diff, and a drift detector that auto-quarantines and
  requires a fresh certification to reinstate.
- **Progressive delivery** — shadow runs (no delivery, no side effects), deterministic
  canary routing with blast-radius caps and auto-promote/auto-abort, and model experiments.
- **Defense** — deterministic injection scanning at the tool boundary, taint tracking that
  blocks destructive calls on untrusted input, deny-by-default port-aware egress with cloud
  metadata always blocked, context-aware policy with what-if dry-runs, versioned team packs,
  and an instant fleet-wide tool kill switch.
- **Runtime guards & health** — context-window budget, spend-velocity guard, retry policies,
  circuit breakers, SLO/error-budget accounting with burn-through action, synthetic probes
  that flag decay before drift trips, and plane self-monitoring.
- **Scale** — MCP Registry v2 (live stdio/HTTP transport), encrypted secrets with rotation,
  RBAC-lite and scoped API keys, distributed workers with signed identity and leases, a
  priority scheduler with QoS and checkpoint-safe preemption, leader election, and chaos
  drills.
- **Explanation** — cost showback, per-tenant budgets and alerts, cost-per-completed-task,
  ROI with `expensive_low_value` flags, an attestation-bound result cache, weekly digests,
  audit export with integrity proofs, signed compliance evidence packs, retention/PII
  scrubbing, and tenant purge.
- **Interface** — REST API v2 with cursor pagination and a structured error envelope, a
  typed Python SDK, agent-as-service, plugin hooks, an expanded nine-channel fan-out with
  interactive/mobile approvals, an operator UI v2 with SSE live runs, and `ask` — a
  read-only natural-language copilot.
- **Distribution** — a Helm chart (full stack) verified by lint/render, k3d reference
  runbook, backup/restore with signed manifests, air-gap bundle, Homebrew formula, and a
  release supply chain (SPDX SBOM, cosign-signed images, SLSA-style provenance).

See [CHANGELOG.md](../../../CHANGELOG.md) for the full, categorized list.

---

## Field test results

The v0.2.0 field test drives the Complete Fleet OS end to end — real downloaded agents on
the real control plane with real PostgreSQL, real HTTP, and real fan-out.

**Overall: PASS — 36/36 scenarios (S1–S31 + H1–H5), 67/67 container/API/UI tests, and a
50/50 concurrency load test.** Every one of the release gates is demonstrated by at least
one scenario. The suite is zero-skip: a missing model or missing evidence is a failure, not
a skip.

The pass count is the least interesting outcome. The field test found **ten product
defects** the unit suite could not — including pipeline child runs created but never
started, fan-out with no API surface, a secret serialization failure under Postgres, and
the absence of an HTTP bootstrap to mint the first API key. All are fixed and re-verified.
The unifying lesson: *every defect lives at a seam; end-to-end tests catch seams that unit
tests stop at.*

Full report: [FIELD_TEST_REPORT.md](../../field-test/v0.2.0/FIELD_TEST_REPORT.md) ·
[DOCKER_TEST_REPORT.md](../../field-test/v0.2.0/DOCKER_TEST_REPORT.md).

---

## Security

- Production admission requires a valid, unexpired certification **and** a verified,
  untampered agent bundle; a model swap is refused.
- Secrets are encrypted at rest, injected at the execution boundary, and redacted from
  context, logs, traces, audit, fan-out, and artifacts (verified by test).
- Injection and egress defense run at the tool boundary; repeated attempts auto-quarantine.
- Signed evidence (attestations, bundles, backups, audit exports, purge records) is
  verifiable offline; the audit log is tamper-evident and exportable with a Merkle proof.
- Pre-release secret/dependency audit and the full security posture are documented in
  [security-audit.md](security-audit.md) and [SECURITY.md](../../../SECURITY.md).

---

## Installation

```bash
pip install hiveplane==0.2.0
```

Or run the full local stack:

```bash
git clone https://github.com/deghosal-2026/hiveplane.git
cd hiveplane
scripts/dev-up.sh          # copies .env.example -> .env, builds, starts the stack
```

Requirements: Python 3.12+ (for the package); Docker Compose for the full stack. A Helm
chart and a k3d reference deployment are available under `deploy/`.

## Quick start

```bash
hiveplane init my-fleet          # scaffold a working project
cd my-fleet
hiveplane validate workload.yaml
hiveplane certify workload.yaml
hiveplane submit --agent my-fleet --task '{"goal": "summarize open PRs"}'
hiveplane runs list
```

New to HivePlane? Follow the [Tutorials](../../tutorials/).

---

## Upgrading from v0.1.0

v0.2.0 changes the storage schema, the runtime-adapter contract, and (when auth is enabled)
the authorization model. **Back up first — migration `0003` is forward-only.** Read the
[Migration Guide](migration-guide.md) before upgrading.

---

## Status and maintenance

HivePlane is now **beta** — feature-complete for the Complete Fleet OS, with the certified
control loop proven end to end. After this release the project enters **maintenance mode**:
documentation, community, articles, and security patches, with no further feature releases.

## Documentation

- [CHANGELOG.md](../../../CHANGELOG.md)
- [User Guide](../../USER_GUIDE.md) · [Architecture Tour](../../architecture-tour.md) · [Tutorials](../../tutorials/) · [Operator Runbook](../../runbooks/operator-runbook.md)
- [Migration Guide](migration-guide.md) · [Field Test Report](../../field-test/v0.2.0/FIELD_TEST_REPORT.md)
- [Security Audit](security-audit.md) · [WBS v0.2.0](../../wbs/v0.2.0/wbs-v0.2.0-index.md)
