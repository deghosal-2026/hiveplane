# Field Test Results (v0.1.0)

Raw evidence for the **real-agent** field test (M23, P4). One subdirectory per
scenario or execution, committed as release-gate evidence. The narrative report
is [`docs/field-test/v0.1.0/FIELD_TEST_REPORT.md`](../../../docs/field-test/v0.1.0/FIELD_TEST_REPORT.md).

## Layout

```
field_test/v0.1.0/results/
├── README.md                     ← this file
├── S1-certify-three/<run-id>/    ← per-scenario evidence
├── S2-uncertified-refused/
├── S3-model-swap/
├── S4-regression-blocked/
├── S5-over-budget/
├── S6-destructive-tool/
├── S7-large-output/
├── S8-pause-restart-resume/
├── S9-fan-out/
└── summary.json                  ← scenario → pass/fail + evidence pointers
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
  (canonical `omlx/qwen3-4b-instruct-2507/4bit`); a cloud model is added later.
- Evidence is captured against the stack at `http://localhost:8100` (API) and
  `http://localhost:3001` (UI).
- Reruns overwrite the same scenario paths so the evidence is repeatable.
- Container/API/UI layer results live separately under
  [`field_test/v0.1.0/docker/`](../docker/) and `docs/field-test/v0.1.0/DOCKER_TEST_REPORT.md`
  (already complete).
- Do not commit secrets; no live external network in evidence.