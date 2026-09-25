# HivePlane v0.1.0 — Field Test Report

> Generated 2026-09-25T00:54:05.506190+00:00 by `scripts/field-test.sh`.
> **Overall: FAIL** — 0 passed, 1 failed, 0 blocked.

## Environment

| Item | Value |
|------|-------|
| HivePlane version | v0.1.0 |
| Model identity | `omlx/qwen3-4b-instruct-2507/4bit` |
| Generated at | 2026-09-25T00:54:05.449279+00:00 |
| Results directory | `/Users/deghosal/desktop/code/github/hiveplane/field_test/v0.1.0/results` |

This is the **real-agent** field test (three Tier 1 workloads). Container/API/UI layers are
covered by [DOCKER_TEST_REPORT.md](DOCKER_TEST_REPORT.md).

## Scenario Results

| Scenario | Name | Status | Detail | Evidence |
|----------|------|--------|--------|----------|
| S1 | certify-tier1 | ❌ fail | support-agent production status=quarantined | `field_test/v0.1.0/results/S1-certify-tier1` |

## Acceptance Criteria

| # | Criterion | Result |
|---|-----------|--------|
| A1 | Three real agents registered | ❌ fail |
| A2 | At least one agent certified for production via benchmark | ❌ fail |
| A3 | Uncertified agent refused production admission | — — |
| A4 | Seeded manifest change blocked by re-certification (regression) | — — |
| A5 | Attestation signed and verified on read | ❌ fail |
| A6 | Model-swap blocked (certified on A, running on B) | — — |
| A7 | Agents through full lifecycle | ❌ fail |
| A8 | Budget enforcement blocks an over-budget run | — — |
| A9 | Execution isolation caps a destructive run | — — |
| A10 | Tool-output shaping truncates a large payload | — — |
| A11 | Guarded tool call requires approval | — — |
| A12 | Paused run survives control-plane restart | — — |
| A13 | Operators can inspect and stop any run from one surface | — — |
| A14 | Result fan-out delivered to configured destinations | — — |
| A15 | Audit trail complete for every run in the field test | — — |
| A16 | Median time to inspect and stop a bad run | — — |
| A17 | Docker Compose stack starts with one command | — — |
| A18 | hiveplane init scaffolds a working project in < 5 minutes | — — |
| A19 | Certification dashboard renders fleet cert status | — — |
| A20 | Spend view shows cost showback by team and agent | — — |

## Learnings

_To be completed._

## Known Issues

- Scenarios marked ⏸️ blocked need a fixture or provider that the local $0 model cannot
  exercise (see each scenario's `notes.md` under `field_test/v0.1.0/results/`).
