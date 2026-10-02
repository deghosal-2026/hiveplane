# HivePlane v0.2.0 — Docker Test Plan

> Status: plan (M61, #484). Defines image build, the v0.2.0 service topology, the layered
> container test matrix (L0–L13), scenario tests, the LLM strategy, and how results and
> screenshots are produced. Companion to the [field test plan](field-test-plan.md).
> Precedent: [v0.1.0 docker test plan](../v0.1.0/docker-test-plan.md).

## Objective

Prove the **Complete Fleet OS** works in the shipped container topology — not just
in-process. Every layer (image → services → API v2 → UI v2 → control loop → immune system →
autonomy → fleet scale → tenancy/defense → cost/reporting → Helm) is exercised against
`docker compose`, with deterministic fixtures, a hermetic CI mode, and a **zero-skip**
policy for the real local model.

## 0. Scope

The container suite covers the **Tier 1 field-test workloads** plus the v0.2.0 fixture
workloads (multi-tenant, drifting, injection, worker, canary candidate). The wider
Tier 2/3 inventory in [field-test-plan.md](field-test-plan.md) is exercised through the
field test, not `docker compose`.

## 1. Images

One application image serves the API, the UI, the worker daemon, and the CLI (different
commands). A fixture MCP server and MinIO are additional images.

| Image | Source | Base | Contains |
|-------|--------|------|----------|
| `hiveplane/api` | `Dockerfile` | `python:3.12-slim` | `hiveplane` package, API + CLI, `examples/` entrypoints, `deploy/testdata/` fixtures, adapter extras |
| `hiveplane/ui` | same image, `hiveplane-ui` command | `python:3.12-slim` | operator UI v2 |
| `hiveplane/worker` | same image, `hiveplane worker` command | `python:3.12-slim` | distributed worker daemon (M46) |
| `minio/minio` | upstream | `minio` | artifact/S3 backend (M54) |
| `fixture-mcp` | `deploy/testdata/mcp/` | `python:3.12-slim` | MCP server for live transport tests (M44) |

Build requirements (unchanged from v0.1.0 plus v0.2.0):

- `COPY examples/` and `COPY field_test/` so Tier 1 entrypoints, shims, and corpora resolve.
- Install the adapter extras so langgraph / pydantic-ai graphs import.
- `COPY deploy/testdata/` so tool fixtures, replay data, and the MCP fixture resolve.
- `HIVEPLANE_EXECUTION__ENTRYPOINTS_ROOT=/app`,
  `HIVEPLANE_CERTIFICATION__CORPORA_DIR=/app/field_test`.

## 2. Service Topology

`docker compose --env-file .env.local --profile local --profile test up -d` starts:

| Service | Port | Role |
|---------|------|------|
| `postgres` | 5432 | system of record (runs, audit, certs, budget, registry, workers, replays) |
| `redis` | 6379 | queue / signaling |
| `otel-collector` | 4317/4318 | trace/metric/log ingestion |
| `tempo` | 3200 | trace storage |
| `prometheus` | 9090 | metrics |
| `grafana` | 3000 | dashboards |
| `api` | 8100 | control plane (v2 API) |
| `ui` | 3001 | operator UI v2 |
| `worker` (×2) | — | distributed workers (M46) for lease/preemption/reassign tests |
| `minio` | 9000/9001 | artifact/S3 backend (M54) |
| `mcp-fixture` | 8765 | MCP server for live-transport tests (M44) |
| `webhook-sink` *(profile: test)* | 8081 | captures 9-channel fan-out deliveries |

**No container provides a local LLM.** The `local` profile relies on **OMLX
(`mlx_lm.server`) on the host** at `127.0.0.1:8000/v1`; containers reach it via
`host.docker.internal:8000/v1`. Host port 8000 is reserved for OMLX.

Durable state volumes: `postgres-data`, `redis-data`, `checkpoint-data`
(`HIVEPLANE_EXECUTION__CHECKPOINT_PATH`), `minio-data`, and telemetry volumes.

Profiles: `ci` (fake provider), `local` (OMLX), `cloud` (OpenAI), `test` (webhook-sink +
fixtures). See §5–§6.

## 3. Layered Test Matrix

Tests live in `tests/docker/` (docker-marked, excluded from the default run:
`pytest tests/docker -m docker`). Each layer gates the next.

| Layer | What is tested | Test module | Pass criteria |
|-------|----------------|-------------|---------------|
| **L0 — Image build** | Image builds; every entrypoint imports; fixtures present | `test_container_fixtures.py` | Build succeeds; all entrypoints + fixtures resolve |
| **L1 — Stack health** | All services healthy; honest `/readyz`; MinIO/worker up | `test_stack_health.py` | All healthy in timeout; `/readyz` 200 |
| **L2 — API v2 contract** | Every REST endpoint per OpenAPI; pagination; errors | `test_api_contract.py`, `test_v02_api.py` | Status + shapes match; `X-Request-ID`, pagination, 429+`Retry-After` |
| **L3 — UI v2 (Playwright)** | Every screen renders/operates; live stream, diff, search, wizard, cost/ROI/health, queue, replay, incident | `test_ui.py`, `test_v02_ui.py` | Screens load; actions work; no console errors; screenshots captured |
| **L4 — Control loop & immune** | register → certify → run → intervene → deliver; promotion gate, regression diff, drift/quarantine/reinstate | `test_control_loop.py`, `test_v02_immune.py` | Full loop completes; scenarios S1–S5 pass; evidence captured |
| **L5 — Defense & governance** | budget, guards (context/velocity/retry/breaker), injection+taint, egress, shaping, policy what-if, kill switch | `test_governance.py`, `test_v02_defense.py` | Each scenario blocks/caps/shapes/denies as specified |
| **L6 — Autonomy** | triggers (webhook/GitHub/alert/cron/watch, ≥3 sources), dedup/cooldown, pipelines, canary/auto-promote, shadow, agent-as-tool, DLQ replay | `test_v02_autonomy.py` | Each path runs end-to-end; dedup/cooldown proven; DLQ replays once |
| **L7 — Fleet scale** | worker daemon on second host, lease expiry→reassign, preemption+attribution, leader election (no double-reconcile), chaos drills | `test_v02_fleet.py` | Lease reassigns; preemption attributed; standby doesn't act |
| **L8 — Secrets, RBAC & tenancy** | secret never in logs/traces/context; injection+rotation; viewer denied; per-tenant budget/policy/key isolation; worker token refusal | `test_v02_secrets_tenancy.py` | Absence tests pass; 401/403/429 as specified; no cross-tenant leak |
| **L9 — Cost, reporting & portability** | artifact stored+linked+retained; showback/CPCT; result cache hit + re-cert invalidation; replay/fork/diff; probes; transparency public verify; provenance mismatch | `test_v02_cost_reporting.py` | Each verified; replay side-effect-free |
| **L10 — Helm/k3d** | Chart lints/renders; deploys the full stack to k3d; multi-worker run | `test_v02_helm.py`, `deploy/k3d/up.sh` | Deploy succeeds; end-to-end run on cluster |
| **L11 — Load test** | ≥50 concurrent runs with no correctness failures | `test_v02_load.py` | ≥50 concurrent sustained; throughput/latency/errors/resources recorded |
| **L12 — Durability** | pause → restart → resume; startup recovery; checkpoints; migrations | `test_durability.py` | Paused run resumes; interrupted runs reconcile to `failed`; data survives |
| **L13 — LLM matrix** | local (OMLX) required; fake deterministic baseline | `test_llm_matrix.py` | Real completion + reported identity; unreachable ⇒ **fail, not skip** |

### L2 — API endpoints (v0.2.0 additions)

- Health/readiness: `GET /healthz`, `GET /readyz`, `GET /metrics` (plane self-monitoring)
- Registry/identity: `POST/GET/DELETE /workloads`, `POST /tools`, `GET /auth/whoami`,
  `GET/POST /keys`, `POST /auth/login`
- Runs/tools: `POST /runs`, `GET /runs`, `GET /runs/{id}`, `/events`, `/usage`, `/story`,
  `POST /runs/{id}/start|pause|resume|stop`, `POST /runs/{id}/tool-calls`
- Immune: `POST /certifications`, `GET /certifications`, `GET /certifications/compare/...`,
  `POST /promotions`, `/promotions/recertify`, `/drift/assess|probe`, `/quarantines`,
  `/quarantines/{id}/reinstate`, `GET /attestations/{id}/verify`
- Autonomy: `POST /triggers`, `POST /triggers/webhook/{id}`, `/triggers/github/{id}`,
  `/triggers/alertmanager/{id}`, `GET /triggers/dlq`, `POST /triggers/dlq/{id}/replay`,
  `POST/GET /pipelines`, `POST /pipelines/{id}/runs`, `POST /shadow`, `POST /canary`,
  `/canary/{id}/promote|abort`, `POST /experiments`
- Fleet/scale: `POST /workers/enroll|register`, `GET /workers`, `GET /queue`,
  `GET /cluster/leader`, `POST/GET /chaos/drills`, `POST /scheduler/...`
- Secrets/tenancy: `GET/POST /secrets`, `/keys`, `GET /audit/access`, `GET/POST /tenants`
- Cost/reporting: `GET /cost/showback`, `/cost/roi/fleet`, `/cost/forecast`,
  `POST /cost/cache/lookup|store`, `GET /reports`, `POST /retention/purge`,
  `GET /artifacts/{id}`, `POST /export|/import`
- Operator: `POST /ask`, `POST /fleet/pause|resume`, `GET /search`, `GET /health[/workloads]`,
  `POST /delivery/approvals/resolve`, `POST /runs/{id}/fork`, `POST /replay/{run_id}`,
  `POST /replay/ab`, `GET /services/{workload}` (agent-as-service)

Each external endpoint: happy path + at least one error path (404/409/403/422/429).

### L3 — UI v2 screens (Playwright)

Fleet, run detail + **live stream** (SSE), approval queue v2 (bulk/comment/delegate),
certification dashboard, **diff viewer** (version/regression/run-to-run/replay), **cost**,
**ROI**, **health/SLO**, **trigger log**, **queue visualizer**, **global search**,
**onboarding wizard**, **replay** page, incident banner. Screenshots →
`docs/field-test/v0.2.0/screenshots/<scenario-id>/`.

## 4. Dummy Data

Runtime fixtures live in `deploy/testdata/`; workloads/corpora in `examples/` and `field_test/`.

| Fixture | Location | Purpose |
|---------|----------|---------|
| Tier 1 workloads | `field_test/workloads/` | support-agent, eval-judge + negatives |
| v0.2.0 fixtures | `field_test/workloads/v02/` | `tenant-b-agent`, `drifting-agent`, `injection-agent`, `pipeline-*`, `canary-candidate` |
| corpora | `examples/corpora/` + `field_test/corpora/` | benchmarks |
| tool fixtures | `deploy/testdata/tools/` | fixture tool responses |
| MCP fixture server | `deploy/testdata/mcp/` | live-transport MCP (M44) |
| LLM replay | `deploy/testdata/llm/replay.json` | deterministic fake provider |
| governance | `deploy/testdata/governance/` | over-budget, destructive, oversized, injection, secret-ref |
| multi-tenant seed | `field_test/v0.2.0/seed/tenants.json` | tenants acme/default for isolation |

Rules: no real secrets; no live network in CI; fixtures realistic enough to produce
meaningful output; every negative scenario maps to a release gate.

## 5. Local LLMs

**Local LLMs are exercised via OMLX on the host** (`local` profile), and the federation/dummy
side uses the deterministic `fake` provider. Real inference is **mandatory** for the L7/LLM
layer.

- Provider `local` targets host OMLX at `http://host.docker.internal:8000/v1`.
- Model identity is canonical (`omlx/qwen3-4b-instruct-2507/4bit`), priced at zero.
- The fake/replay provider is the CI baseline (no network, deterministic).

### Zero-skip requirement

The docker suite runs against real local inference and must never silently pass without a
model.

- `scripts/docker-test-v02.sh` preflights the OpenAI-compatible endpoint and exits non-zero
  with a clear message if unreachable.
- `tests/docker/test_llm_matrix.py` fails (never skips) on an unreachable endpoint.
- `scripts/docker_report.py` exits non-zero on any failure/error **or any skip**.

## 6. LLM Configuration Matrix

| Profile | Provider | Endpoint | Secrets | Determinism | CI |
|---------|----------|----------|---------|-------------|----|
| `local` | `local` | OMLX `host.docker.internal:8000` | none | temperature 0 | **required — red if unreachable** |
| `ci` | `fake` | in-process replay | none | fully deterministic | optional snapshot baseline |
| `cloud` | `cloud` | `api.openai.com` | `OPENAI_API_KEY` | temperature 0 | separate opt-in |

## 7. Execution

```bash
scripts/docker-test-v02.sh            # preflight → build → up → wait /readyz → seed → L0–L13 → screenshots → report → down
```

CI wiring:

- **PR/push**: L0 image build only (fast, hermetic).
- **Nightly** (self-hosted macOS runner with OMLX): full L0–L13 + Playwright, zero-skip.

## 8. Screenshots

Captured deterministically by the Playwright layer into
`docs/field-test/v0.2.0/screenshots/<scenario-id>/<step>-<name>.png`; reruns overwrite the
same paths. Embedded in the user guide.

## 9. Results

Every run writes evidence to `field_test/v0.2.0/docker/<run-id>/` (build, compose, `/readyz`,
service + pytest logs, `junit.xml`, `environment.json`) and commits it.
`scripts/docker_report.py` renders `docs/field-test/v0.2.0/DOCKER_TEST_REPORT.md` and exits
non-zero on any failure or skip.

## 10. Out of Scope

- Container/cgroup sandbox isolation (process/rlimit caps only; #110).
- A containerized local LLM (OMLX runs on the host).
- Real cloud-provider validation (opt-in `cloud` profile).
- Cross-region federation (stretch; covered by unit/integration tests).

## See Also

- [Field test plan](field-test-plan.md)
- [Helm chart](../../../deploy/helm/hiveplane/README.md)
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — 34 release gates
- [v0.1.0 docker test plan](../v0.1.0/docker-test-plan.md)
