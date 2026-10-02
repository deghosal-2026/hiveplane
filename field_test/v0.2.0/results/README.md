# Field Test Results (v0.2.0)

Raw evidence for the **real-agent** v0.2.0 field test (M61). One subdirectory per scenario or
execution, committed as release-gate evidence. The narrative report is
[`docs/field-test/v0.2.0/FIELD_TEST_REPORT.md`](../../../docs/field-test/v0.2.0/FIELD_TEST_REPORT.md).

## Layout

```
field_test/v0.2.0/results/
├── README.md                       ← this file
├── S1-certification/<run-id>/      ← per-scenario evidence
├── S2-promotion-gate/
├── S3-regression-diff/
├── S4-drift-quarantine/
├── S5-reinstatement/
├── S6-triggers/
├── S7-pipelines/
├── S8-canary/
├── S9-shadow/
├── S10-agent-as-tool/
├── S11-injection/
├── S12-context-budget/
├── S13-spend-velocity/
├── S14-circuit-breaker/
├── S15-egress/
├── S16-secrets/
├── S17-rbac-tenancy/
├── S18-worker-lease/
├── S19-preemption/
├── S20-dlq-replay/
├── S21-showback/
├── S22-result-cache/
├── S23-gitops-reconcile/
├── S24-probes/
├── S25-verify-killswitch/
├── load/                           ← ≥50-concurrent-run load-test evidence
└── summary.json                    ← scenario → pass/fail + gate mapping + evidence pointers
```

## What each scenario directory contains

| Artifact | Contents |
|----------|----------|
| `commands.sh` | The exact CLI/API commands run (reproducible) |
| `output.log` | Raw command output / API responses |
| `environment.json` | Git SHA, model identity, stack profile, ports |
| `runs.json` | Run records + events captured for the scenario |
| `attestations.json` | Signed attestations (where applicable) |
| `notes.md` | Observations, pass/fail, links into `FIELD_TEST_REPORT.md` |

## Conventions

- The field test runs on the **local model `Qwen3-4B-Instruct-2507-4bit`**
  (canonical `omlx/qwen3-4b-instruct-2507/4bit`).
- Evidence is captured against the stack at `http://localhost:8100` (API) and
  `http://localhost:3001` (UI).
- Reruns overwrite the same scenario paths so the evidence is repeatable.
- Container/API/UI layer results live separately under
  [`field_test/v0.2.0/docker/`](../docker/) and
  `docs/field-test/v0.2.0/DOCKER_TEST_REPORT.md`.
- Do not commit secrets; no live external network in evidence.
- `summary.json` maps every scenario to the [34 release gates](../../../docs/prd/09-roadmap.md);
  the generated report (`scripts/field_test_report_v02.py`) reads it and writes
  `docs/field-test/v0.2.0/FIELD_TEST_REPORT.md`.
