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

## Docker test suite (container layer — complete)

| Script | Purpose |
|--------|---------|
| `docker-test.sh` | One command to run the container-layer suite (L0–L7): reset volumes, build, bring the stack up, wait `/readyz`, seed tools, install Chromium, run `pytest tests/docker -m docker`, capture logs/junit, tear down. Fail-fast local-LLM preflight (no skips). Writes evidence to `field_test/v0.1.0/docker/` and status/heartbeat/pid/abort markers. |
| `docker_report.py` | Render `docs/field-test/v0.1.0/DOCKER_TEST_REPORT.md` from a run's `junit.xml` + `environment.json`; includes per-layer results and a persistent Issues/Learnings section. Exits non-zero on any failure/error/skip. |

## Real-agent field test (M23 P4)

| Script | Purpose |
|--------|---------|
| `field-test-setup.sh` | Field-test setup (#99): seed the MCP tool registry and register the three Tier 1 workloads plus the negative variants (uncertified, model-swap, regressed). Idempotent. Also invoked by `field-test.sh`. |
| `field-test.sh` | One command to run the **real-agent** field test: reset volumes, bring the stack up on the local model, run setup, then drive the three Tier 1 workloads through the certified control loop (S1–S9). Writes raw evidence to `field_test/v0.1.0/results/` and the report to `docs/field-test/v0.1.0/FIELD_TEST_REPORT.md`. `--keep` leaves the stack up; `--only S1,S2` runs a subset; `--no-build` skips the build. |
| `field_test_runner.py` | Scenario driver invoked by `field-test.sh` (also runnable directly with `--api-url`). Implements S1–S9: certification (staging→production), admission gates, model swap, regression, budget, destructive-tool approval, shaping, pause→restart→resume, fan-out. |
| `field_test_report.py` | Render the field-test report from `results/summary.json` (per-scenario results, evidence links, acceptance-criteria mapping). |

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
| `HIVEPLANE_MODEL__BASE_URL` | `http://127.0.0.1:8000/v1` | `docker-test.sh`, `field-test.sh` |
| `HIVEPLANE_MODEL__DEFAULT_MODEL` | from `.env.local` | preflight model resolution |
| `API_PORT` / `UI_PORT` | `8100` / `3001` | both suites |
| `POSTGRES_PORT`, `REDIS_PORT`, `GRAFANA_PORT`, `PROMETHEUS_PORT`, `TEMPO_PORT`, `OTEL_GRPC_PORT`, `OTEL_HTTP_PORT`, `WEBHOOK_SINK_PORT` | `55432`, `56379`, `33000`, `39090`, `33200`, `44317`, `44318`, `58081` | host port remaps |

The API is served on `http://localhost:8100`; host port `8000` is reserved for OMLX.

## Where results live

| Track | Raw evidence | Report |
|-------|--------------|--------|
| Docker (container layer) | `field_test/v0.1.0/docker/` | `docs/field-test/v0.1.0/DOCKER_TEST_REPORT.md` |
| Field test (real agents) | `field_test/v0.1.0/results/` | `docs/field-test/v0.1.0/FIELD_TEST_REPORT.md` |

See [docs/field-test/v0.1.0/README.md](../docs/field-test/v0.1.0/README.md) for the plans.