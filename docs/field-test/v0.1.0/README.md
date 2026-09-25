# HivePlane v0.1.0 — Field Test Directory

This directory contains the field test plan, execution artifacts, and reports for M23. It is organized for **repeatability** — anyone can run the field test again by following this directory.

---

## Directory Structure

```
docs/field-test/v0.1.0/
├── README.md                 ← This file — directory index
├── field-test-plan.md        ← Master field test plan (6 phases, 20 acceptance criteria, S1-S9)
├── docker-test-plan.md       ← Container-layer plan (image build, layered tests, dummy data, LLM matrix)
├── FIELD_TEST_REPORT.md      ← Results (published after M23)
├── DOCKER_TEST_REPORT.md     ← Docker-layer results (published after M23)
└── screenshots/              ← Scenario screenshots for the user guide (regenerable)
    └── <scenario-id>/<step>-<name>.png
```

---

## What Each File Contains

| File | Content | Status |
|------|---------|--------|
| `field-test-plan.md` | 6-phase plan: baseline, certification, lifecycle, governance, intervention & durability, review; S1-S9 scenarios; LLM matrix; A1-A20 acceptance criteria | ✅ Plan |
| `docker-test-plan.md` | Image build, service topology, layered test matrix (L0-L7), dummy data, LLM profiles, screenshots, results | ✅ Executed (25/25 green) |
| `FIELD_TEST_REPORT.md` | Execution results: certification, sandbox, shaping, fan-out, observability metrics, learnings | ⬜ Pending (P4) |
| `DOCKER_TEST_REPORT.md` | Docker-layer results: per-layer pass/fail, observations, fixes, takeaways | ✅ Published |
| `screenshots/` | Deterministic scenario screenshots embedded in the user guide | ✅ Captured + embedded |

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
# The field test runs the real agents on REAL local inference (OMLX on the host, port 8000).
pip install -e ".[dev]"
# Bring up the stack on the local model (API on :8100):
docker compose --env-file .env.local --profile local --profile test up -d
```

The real agents, LLM seam, tool execution, adapter-backed certification, persistence, sandbox
caps, and durable resume are all landed (M23 P0–P3), so this plan can execute. See
[docker-test-plan.md](docker-test-plan.md) for the container-layer matrix (already complete) and
[field-test-plan.md](field-test-plan.md) for the LLM profile matrix.

### Procedure

```bash
# Phase 1: Register the three field-test workloads
hiveplane register examples/workloads/repo-agent.yaml
hiveplane register examples/workloads/docs-agent.yaml
hiveplane register examples/workloads/incident-agent.yaml

# Phase 2: Certify all three (staging -> provisional, production -> certified)
hiveplane certify repo-agent --context staging
hiveplane certify repo-agent --context production
hiveplane certify docs-agent --context staging
hiveplane certify docs-agent --context production
hiveplane certify incident-agent --context staging
hiveplane certify incident-agent --context production
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
- Container-layer validation lives in [docker-test-plan.md](docker-test-plan.md).
- Benchmark corpora live under `examples/corpora/<workload>/v1/`.
- Scenario screenshots live under `screenshots/<scenario-id>/` and are regenerated by the
  Playwright layer so reruns are repeatable.
- Docker run evidence (logs, `junit.xml`, environment, per-run report) is written under
  `field_test/v0.1.0/docker/` and committed; the consolidated report is
  `DOCKER_TEST_REPORT.md` in this directory.
- **Field test results (real agents) are written under `field_test/v0.1.0/results/`** and committed;
  the narrative report is `FIELD_TEST_REPORT.md` in this directory.
- Other execution artifacts and logs are written under the repo's data directory and are not
  committed.
- Update results in `FIELD_TEST_REPORT.md` (and `DOCKER_TEST_REPORT.md`) after each run.
