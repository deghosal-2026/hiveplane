# HivePlane v0.1.0 — Field Test Directory

This directory contains the field test plan, execution artifacts, and reports for M18. It is organized for **repeatability** — anyone can run the field test again by following this directory.

---

## Directory Structure

```
docs/field-test/v0.1.0/
├── README.md                 ← This file — directory index
├── field-test-plan.md        ← Master field test plan
└── FIELD_TEST_REPORT.md      ← Results (published after M18)
```

---

## What Each File Contains

| File | Content | Status |
|------|---------|--------|
| `field-test-plan.md` | Scenario, three workloads, acceptance criteria, run procedure | ✅ Plan |
| `FIELD_TEST_REPORT.md` | Execution results, metrics, learnings | ⬜ Pending |

---

## Field Test Goal

Run **three real agents** through the same control-plane lifecycle and prove the core loop:

1. register → submit → queued → running → completed
2. budget enforcement blocks a seeded over-budget run
3. a guarded tool call escalates for approval
4. operator pause / resume / stop works
5. a paused run survives a control-plane restart
6. audit trail is complete for every run

---

## How To Run Field Tests

### Prerequisites

```bash
docker compose up -d
pip install -e ".[dev]"
```

### Procedure

```bash
# Register the three field-test workloads
hiveplane register examples/workloads/repo-agent.yaml
hiveplane register examples/workloads/docs-agent.yaml
hiveplane register examples/workloads/incident-agent.yaml

# Submit tasks and observe lifecycle
hiveplane submit --agent repo-agent --task "summarize open PRs"
hiveplane runs list

# Exercise intervention
hiveplane runs pause <run-id>
hiveplane runs resume <run-id>
hiveplane runs stop <run-id>
```

Full step-by-step instructions live in [field-test-plan.md](field-test-plan.md).

---

## Key Metrics

| Metric | Target |
|--------|--------|
| Agents onboarded | 3 |
| Runs through full lifecycle | ≥ 3 |
| Budget overruns caught | ≥ 1 |
| Approvals exercised | ≥ 1 |
| Audit completeness | 100% |
| Median time to inspect + stop a bad run | < 2 min |

---

## Notes

- Plans and reports live in `docs/field-test/v0.1.0/`.
- Execution artifacts and logs are written under the repo's data directory and are not committed.
- Update results in `FIELD_TEST_REPORT.md` after each run.
