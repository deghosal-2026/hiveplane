# HivePlane Scripts

Operator and release-evidence scripts for HivePlane. Run them from the repo
root (each resolves its own paths). Most require a Docker daemon; the field and
Docker suites also require **real local inference** (OMLX on host port 8000) and
fail rather than skip when it is unavailable.

## Day-to-day

| Script | Purpose |
|--------|---------|
| `dev-up.sh` | Bring up the local reference stack with `docker compose up -d` (creates `.env` from `.env.example`). Use `--no-build` to skip the image build. |
| `seed-tools.sh` | Seed the MCP tool registry with every tool referenced by the workload manifests (`hiveplane tools seed`). Idempotent. |

## Docker test suite (container layer)

| Script | Purpose |
|--------|---------|
| `docker-test.sh` | v0.1.0 container-layer suite (L0–L7): reset volumes, build, bring the stack up, wait `/readyz`, seed tools, install Chromium, run `pytest tests/docker -m docker`, capture logs/junit, tear down. Fail-fast local-LLM preflight (no skips). Writes evidence to `field_test/v0.1.0/docker/`. |
| `docker-test-v02.sh` | **v0.2.0 container + scenario suite (M61, L0–L13 + S1–S25 + load test):** same flow, runs the v0.2.0 docker modules and Playwright UI v2, writes evidence to `field_test/v0.2.0/docker/` and the report to `docs/field-test/v0.2.0/DOCKER_TEST_REPORT.md`. Supports `--no-build`, `--keep`, `--only S6,S7`. |
| `docker_report.py` | Render a Docker test report from a run's `junit.xml` + `environment.json`; per-layer (L0–L13) and per-test results, Issues/Learnings, log index. `--version-dir` selects the report version (default `v0.1.0`). Exits non-zero on any failure/error/**skip**. |

## Real-agent field test

| Script | Purpose |
|--------|---------|
| `field-test-setup.sh` | Field-test setup (#99): seed the MCP tool registry and register the Tier 1 workloads plus negative variants (uncertified, model-swap, regressed). Idempotent. Also invoked by `field-test-v02.sh`. |
| `field-test-v02.sh` | **v0.2.0 real-agent field test runner (M61):** reset volumes, bring the stack up on the local model, seed tools, run setup, drive the Tier 1 workloads and every supported scenario (S1–S25) plus the post-remediation regressions (S26–S31) and harness checks (H1–H5). Writes evidence to `field_test/v0.2.0/results/` and the generated report to `results/report.generated.md`. Supports `--keep`, `--no-build`, `--auth`, `--priced`, `--env-file FILE`, `--only S1,S26`. Use `--env-file .env.ci` with `HIVEPLANE_MODEL__PROVIDER=fake` for the hermetic CI replay sweep (`.github/workflows/field-test-replay.yml`). |
| `field_test_runner_v02.py` | v0.2.0 scenario driver invoked by `field-test-v02.sh` (also runnable directly with `--api-url`/`--results-dir`/`--only`). Implements S1–S31 and H1–H5. |
| `field_test_report_v02.py` | Render the **generated** report (`field_test/v0.2.0/results/report.generated.md`) from `summary.json`: S1–S31 + H1–H5 table, per-scenario detail, 34-gate coverage naming asserting scenarios, and an evidence index. Never writes the curated `FIELD_TEST_REPORT.md`. |
| `docker-test-v02.sh` | The container-layer track (`tests/docker/test_v02_*.py`, L0–L13) that complements the real-agent runner; evidence lands in `field_test/v0.2.0/{docker,results}/`. |

## Seeded demo

| Script | Purpose |
|--------|---------|
| `demo.sh` | Deterministic first-run walkthrough on the **fake provider** (`--env-file .env.ci`): register → certify → submit → observe → intervene → deliver, with a readable narrative. No secrets, no network; ideal for a user walkthrough. `--keep` leaves the stack up. |

## Fixtures & replay

| Script | Purpose |
|--------|---------|
| `generate_replay.py` | Regenerate `deploy/testdata/llm/replay.json` by driving every example agent through its corpus tasks; the committed file must be byte-identical to the generator output (`tests/test_replay_generation.py` enforces this). Re-run after any agent prompt/contract change. |

## Common environment variables

| Variable | Default | Used by |
|----------|---------|---------|
| `HIVEPLANE_MODEL__BASE_URL` | `http://127.0.0.1:8000/v1` | `docker-test.sh`, `field-test-v02.sh` |
| `HIVEPLANE_MODEL__DEFAULT_MODEL` | from `.env.local` | preflight model resolution |
| `API_PORT` / `UI_PORT` | `8100` / `3001` | both suites |
| `POSTGRES_PORT`, `REDIS_PORT`, `GRAFANA_PORT`, `PROMETHEUS_PORT`, `TEMPO_PORT`, `OTEL_GRPC_PORT`, `OTEL_HTTP_PORT`, `WEBHOOK_SINK_PORT` | `55432`, `56379`, `33000`, `39090`, `33200`, `44317`, `44318`, `58081` | host port remaps |

The API is served on `http://localhost:8100`; host port `8000` is reserved for OMLX.

## Where results live

| Track | Raw evidence | Report |
|-------|--------------|--------|
| Docker (v0.1.0) | `field_test/v0.1.0/docker/` | `docs/field-test/v0.1.0/DOCKER_TEST_REPORT.md` |
| Field test (v0.1.0) | `field_test/v0.1.0/results/` | `docs/field-test/v0.1.0/FIELD_TEST_REPORT.md` |
| Docker (v0.2.0) | `field_test/v0.2.0/docker/` | `docs/field-test/v0.2.0/DOCKER_TEST_REPORT.md` |
| Field test (v0.2.0) | `field_test/v0.2.0/results/` | `docs/field-test/v0.2.0/FIELD_TEST_REPORT.md` (incl. the merged load test) |

See [docs/field-test/v0.2.0/README.md](../docs/field-test/v0.2.0/README.md) for the v0.2.0 plans and
[docs/field-test/v0.1.0/README.md](../docs/field-test/v0.1.0/README.md) for the v0.1.0 plans.