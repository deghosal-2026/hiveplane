# v0.2.0 — Work Breakdown Structure

**Goal:** Ship the Complete Fleet OS — the final big release. The fleet runs itself (triggers, pipelines, GitOps reconciliation), defends itself (drift, injection, policy, kill switches), explains itself (cost/ROI, reporting), and scales itself (tenancy, distributed workers, Helm). Everything once planned for v0.3.0 and v0.4.0 is absorbed here.

**Timeline:** 4–5 months

**Total scope:** 38 milestones (M25–M62) · 19 parts

> **Milestone exit gate:** Every milestone must pass the [Milestone Exit Gate](#milestone-exit-gate) before the next begins. No exceptions.
> **Issue tracking:** All work is tracked on GitHub with the `[0.2.0]` prefix, grouped by milestone M25–M62. Work items below use stable local IDs (`M25-01`) and are filed as issues at milestone start.
> **Working agreement:** one milestone at a time; exit gate before advancing; adapters stay behind the contract; nothing framework-specific leaks into core; every operator action is attributed and audited.
> **Scope authority:** [PRD 05 Features](../../prd/05-features.md) and [PRD 09 Roadmap](../../prd/09-roadmap.md) are the source of truth for *what* ships. This WBS is the source of truth for *build order*.

---

## Parts

| Part | Title | Milestones |
|------|-------|------------|
| [1](wbs-v0.2.0-part1-foundation.md) | Foundation & Reconciliation | M25–M26 |
| [2](wbs-v0.2.0-part2-triggers.md) | Autonomy: Triggers & Scheduling | M27–M28 |
| [3](wbs-v0.2.0-part3-orchestration.md) | Multi-Agent Orchestration | M29–M30 |
| [4](wbs-v0.2.0-part4-runtime-promotion.md) | Runtime Breadth & Promotion Gate | M31–M32 |
| [5](wbs-v0.2.0-part5-drift-quarantine.md) | Regression Diff, Drift & Quarantine | M33–M34 |
| [6](wbs-v0.2.0-part6-provenance-learning.md) | Provenance & the Learning Loop | M35–M36 |
| [7](wbs-v0.2.0-part7-progressive-delivery.md) | Progressive Delivery | M37–M38 |
| [8](wbs-v0.2.0-part8-defense-policy.md) | Defense & Policy | M39–M40 |
| [9](wbs-v0.2.0-part9-guards-health.md) | Runtime Guards & Agent Health | M41–M42 |
| [10](wbs-v0.2.0-part10-probes-mcp.md) | Probes, Self-Monitoring & MCP v2 | M43–M44 |
| [11](wbs-v0.2.0-part11-secrets-workers.md) | Secrets, Identity & Distributed Workers | M45–M46 |
| [12](wbs-v0.2.0-part12-scheduling-ha.md) | Scheduling, HA & Chaos | M47–M48 |
| [13](wbs-v0.2.0-part13-cost-roi.md) | Cost, Showback & ROI | M49–M50 |
| [14](wbs-v0.2.0-part14-delivery-ui.md) | Delivery & Operator UI v2 | M51–M52 |
| [15](wbs-v0.2.0-part15-cli-artifacts.md) | CLI, Copilot & Artifacts | M53–M54 |
| [16](wbs-v0.2.0-part16-corpus-api.md) | Corpus Tooling & API/SDK | M55–M56 |
| [17](wbs-v0.2.0-part17-reporting-tenancy.md) | Reporting & Multi-Tenancy | M57–M58 |
| [18](wbs-v0.2.0-part18-distribution-replay.md) | Distribution & Replay | M59–M60 |
| [19](wbs-v0.2.0-part19-field-test-release.md) | Field Test & Release Readiness | M61–M62 |
| **Total** | | **M25–M62 (38 milestones)** |

---

## Milestone Map

```
Part 1    Part 2     Part 3      Part 4       Part 5        Part 6       Part 7
M25 M26   M27 M28    M29 M30     M31 M32      M33 M34       M35 M36      M37 M38
 │   │     │   │      │   │       │   │        │   │         │   │        │   │
 └───┴─────┴───┴──────┴───┴───────┴───┴────────┴───┴─────────┴───┴────────┴───┘
   Foundation ─ Triggers ─ Orchestration ─ Runtime/Promotion ─ Drift ─ Provenance ─ Delivery
                                                                      │
Part 8       Part 9       Part 10     Part 11      Part 12      Part 13      Part 14
M39 M40      M41 M42      M43 M44     M45 M46      M47 M48      M49 M50      M51 M52
 │   │        │   │        │   │       │   │        │   │        │   │        │   │
 └───┴────────┴───┴────────┴───┴───────┴───┴────────┴───┴────────┴───┴────────┴───┘
   Defense ─ Guards/Health ─ Probes/MCP ─ Secrets/Workers ─ Sched/HA ─ Cost/ROI ─ Delivery/UI
                                                                      │
Part 15      Part 16      Part 17        Part 18         Part 19
M53 M54      M55 M56      M57 M58        M59 M60         M61 M62
 │   │        │   │        │   │          │   │           │   │
 └───┴────────┴───┴────────┴───┴──────────┴───┴───────────┴───┘
   CLI/Artifacts ─ Corpus/API ─ Reporting/Tenancy ─ Distribution/Replay ─ Field Test/Release
```

---

## Dependencies

```
Part 1 (Foundation + Reconciliation)
  └─> Part 2 (Triggers) ─────────────────┐
  └─> Part 3 (Orchestration) ────────────┤
  └─> Part 4 (Runtime + Promotion) ──────┤
        Part 4 ─> Part 5 (Drift/Quarantine)
        Part 5 ─> Part 6 (Provenance/Learning)
        Part 6 ─> Part 7 (Progressive Delivery)
  └─> Part 8 (Defense/Policy) ───────────┤
        Part 8 ─> Part 9 (Guards/Health)
  └─> Part 10 (Probes/MCP)
  └─> Part 11 (Secrets/Workers) ─────────┤
        Part 11 ─> Part 12 (Scheduling/HA)
  └─> Part 13 (Cost/ROI)
  └─> Part 14 (Delivery/UI) ─────────────┤
  └─> Part 15 (CLI/Artifacts)
  └─> Part 16 (Corpus/API)
  └─> Part 17 (Reporting/Tenancy)
  └─> Part 18 (Distribution/Replay)
All ─> Part 19 (Field Test → Release)
```

**Critical path:** Part 1 → Part 4 → Part 5 → Part 6 → Part 7 → Part 19. The certification/immune system and the learning loop gate the progressive-delivery and release work.

**Parallelizable tracks after Part 1:** Triggers (2), Orchestration (3), Defense (8), Cost (13), Delivery (14), Corpus/API (16), Reporting (17) can proceed concurrently once foundation lands, subject to review capacity.

---

## Milestone Exit Gate

Every milestone (M25–M62) must pass this gate before the next milestone begins:

- [ ] **All tests in the system pass:** `pytest`
- [ ] **Code coverage total > 95%:** `pytest --cov=src/hiveplane --cov-report=term-missing`
- [ ] **Ruff clean:** `ruff check`
- [ ] **Mypy strict clean:** `mypy src/ tests/`
- [ ] **All documentation affected by this milestone updated** (PRD, WBS, design doc, `USER_GUIDE.md`, `CHANGELOG.md` as applicable)
- [ ] **All issues in this milestone are done**
- [ ] **All completed issues are closed**
- [ ] **Commit and push changes**

> **No milestone is "done" until the gate is green.** A milestone with failing tests, coverage below 95%, lint/type errors, or stale docs is not complete — it is in progress.

---

## Final Release Gate (v0.2.0)

Before tagging v0.2.0, ALL of the following must be true (full list: [PRD 09 Roadmap](../../prd/09-roadmap.md) — 34 gates):

- [ ] All M25–M62 milestones complete and exit gates passed
- [ ] Drifting agent auto-quarantined, team notified, reinstated after re-cert
- [ ] Promotion gate blocks a regression with a replayable diff
- [ ] Triggers fire from ≥3 sources with dedup/cooldown proven
- [ ] Seeded injection blocked; repeated attempts quarantine the agent
- [ ] Slack approvals + fan-out to ≥3 channels; mobile approvals work
- [ ] Per-tenant budget/policy/key isolation verified; viewer cannot approve
- [ ] Helm chart deploys the full stack to a k3d cluster
- [ ] Health dashboard shows readiness/failure/SLO burn; burn-through throttles; circuit breaker trips and recovers
- [ ] Showback attributes cost by tenant → team → agent with cost-per-completed-task
- [ ] Frame-by-frame replay + run diff works; a forked run re-runs with edited state
- [ ] Multi-agent pipeline runs end-to-end with per-step gates
- [ ] Canary routes 10% to a candidate and auto-promotes on clean results
- [ ] A secret never appears in logs/traces/agent context (verified by test)
- [ ] A dead trigger replays from the DLQ
- [ ] SDK + API v2 round-trip a full run; a plugin hook fires
- [ ] Weekly digest auto-generates; an artifact is stored, linked, retained per policy
- [ ] Git deletes an agent → plane deregisters it; git changes a cert threshold → re-cert fires
- [ ] Urgent run preempts a best-effort run with attribution
- [ ] Worker daemon executes a run on a second host; kill it → lease expiry reassigns
- [ ] Context budget exceeded → run pauses cleanly with accounting shown
- [ ] Cache hit reuses a result and shows savings; re-cert invalidates it
- [ ] Synthetic probe flags decay before the drift threshold trips
- [ ] `ask` answers 5 live-state questions, itself under budget + cert
- [ ] Incident mode halts the fleet in <5s and broadcasts
- [ ] Attestation verifies publicly by ID; kill switch disables a tool fleet-wide instantly
- [ ] Signed image + SBOM published; retention purge deletes tenant data on schedule
- [ ] Operator-flagged failed run becomes a corpus case in the next certification
- [ ] Sampled production runs receive judge scores; quality dip alerts before re-cert
- [ ] Modified agent bundle fails admission on provenance-signature mismatch
- [ ] Worker without a signed token is refused
- [ ] Agent-as-tool calls propagate budget/policy/certification to the nested run
- [ ] Second controller replica does not double-reconcile (leader election verified)
- [ ] Chaos drills recover or halt correctly (kill worker mid-run; revoke cert mid-flight)
- [ ] Per-workload service endpoint serves a run through all gates; over-limit tenants get 429s
- [ ] Lint strict clean, mypy strict, zero errors
- [ ] Test coverage total > 95%
- [ ] Field test: 25 scenarios + load test sustaining ≥50 concurrent runs
- [ ] Docs overhaul complete (user guide, tutorials, architecture tour, operator runbook, migration guide)
- [ ] Status promoted alpha → beta; `CHANGELOG.md`, release notes, and GitHub release published
- [ ] PyPI + Homebrew published; signed, SBOM'd, cosign-signed artifacts with SLSA-style provenance

---

## See Also

- [PRD 05 Features](../../prd/05-features.md)
- [PRD 07 Success Metrics](../../prd/07-success-metrics.md)
- [PRD 09 Roadmap](../../prd/09-roadmap.md)
- [v0.1.0 WBS](../v0.1.0/wbs-v0.1.0-index.md)
- [Design docs](../../design/README.md)
