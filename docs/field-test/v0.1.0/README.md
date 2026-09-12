# HivePlane v0.1.0 — Field Test Directory

This directory contains the field test plan, execution artifacts, and reports for M18. It is organized for **repeatability** — anyone can run the field test again by following this directory.

---

## Directory Structure

```
docs/field-test/v0.1.0/
├── README.md                 ← This file — directory index
├── field-test-plan.md        ← Master field test plan (6 phases, 20 acceptance criteria)
└── FIELD_TEST_REPORT.md      ← Results (published after M18)
```

---

## What Each File Contains

| File | Content | Status |
|------|---------|--------|
| `field-test-plan.md` | 6-phase plan: baseline, certification, lifecycle, governance, intervention & durability, review; 20 acceptance criteria covering all v0.1.0 release gates | ✅ Plan |
| `FIELD_TEST_REPORT.md` | Execution results: certification, sandbox, shaping, fan-out, observability metrics, learnings | ⬜ Pending |

---

## Field Test Goal

Run **three real agents** through the **certified control loop** and prove the v0.1.0 thesis: an agent is registered, certified against a benchmark, and only then allowed to run in production.

The field test covers six phases:

1. **Baseline** — Docker Compose starts, three workloads register, MCP tools seeded
2. **Certification** — all three workloads certified via benchmark; attestations signed; uncertified refused production; model-swap blocked
3. **Lifecycle** — submit, run, state transitions, usage reporting
4. **Governance** — over-budget blocked, destructive tool sandboxed + approved, large output shaped
5. **Intervention & Durability** — pause, restart, resume, stop, fan-out delivery
6. **Review** — fleet view, certification dashboard, spend showback, agent health, audit completeness

---

## How To Run Field Tests

### Prerequisites

```bash
docker compose up -d
pip install -e ".[dev]"
```

### Procedure

```bash
# Phase 1: Register the three field-test workloads
hiveplane register examples/workloads/repo-agent.yaml
hiveplane register examples/workloads/docs-agent.yaml
hiveplane register examples/workloads/incident-agent.yaml

# Phase 2: Certify all three
hiveplane certify repo-agent
hiveplane certify docs-agent
hiveplane certify incident-agent
hiveplane certs list
hiveplane certs show <cert-id>

# Verify uncertified is refused production
hiveplane register examples/workloads/uncertified-agent.yaml
hiveplane submit --agent uncertified-agent --context production  # → refused

# Phase 3: Submit tasks and observe lifecycle
hiveplane submit --agent repo-agent --task "summarize open PRs"
hiveplane runs list
hiveplane runs show <run-id>

# Phase 4: Governance
hiveplane submit --agent incident-agent --task "triage alert"   # triggers destructive tool → sandbox + approval
# Budget overrun seeded via test fixture

# Phase 5: Intervention
hiveplane runs pause <run-id>
docker compose restart                          # verify durable state
hiveplane runs resume <run-id>
hiveplane runs stop <run-id>

# Phase 6: Review
# Open UI: fleet view, certification dashboard, spend view, agent health
```

Full step-by-step instructions live in [field-test-plan.md](field-test-plan.md).

---

## Key Metrics

| Metric | Target |
|--------|--------|
| Agents registered | 3 |
| Agents certified for production | ≥ 1 |
| Uncertified agent refused production | pass |
| Attestation signed and verified | pass |
| Model-swap blocked | ≥ 1 seeded |
| Runs through full lifecycle | ≥ 3 |
| Budget overruns caught | ≥ 1 |
| Destructive run sandboxed + capped | pass |
| Tool-output shaping applied | ≥ 1 |
| Approvals exercised | ≥ 1 |
| Result fan-out delivered | ≥ 1 |
| Paused run survives restart | pass |
| Audit completeness | 100% |
| Median time to inspect + stop a bad run | < 2 min |
| `hiveplane init` scaffolds in | < 5 min |

---

## Notes

- Plans and reports live in `docs/field-test/v0.1.0/`.
- Benchmark corpora live under `examples/corpora/<workload>/v1/`.
- Execution artifacts and logs are written under the repo's data directory and are not committed.
- Update results in `FIELD_TEST_REPORT.md` after each run.
