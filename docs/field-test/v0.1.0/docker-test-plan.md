# HivePlane v0.1.0 — Docker Test Plan

> Status: plan (M23, #92); updated 2026-09-23 to match the implemented stack (readiness
> #130, recovery #111, durable checkpoint #122, fixtures in-image #143). Defines image
> build, the layered test matrix, dummy data, the LLM strategy, and how results and
> screenshots are produced. Companion to the [field test plan](field-test-plan.md).

## Objective

Prove the certified control loop works in the shipped container topology — not just in-process
unit tests. Every layer of the stack (image → services → API → UI → control loop → governance →
durability) is exercised against `docker compose`, with deterministic data and a hermetic CI mode.

## 0. Scope

The container suite covers the **three primary field-test workloads**: `repo-agent` (raw-worker),
`docs-agent` (langgraph), `incident-agent` (raw-worker). The wider 113-agent / 12-platform inventory
in [field-test-plan.md](field-test-plan.md) is **not** run through `docker compose` in v0.1.0; it
belongs to the broader certification story tracked separately.

## 1. Images

A single image serves both the API and the UI (the UI is the same image run with the
`hiveplane-ui` command):

| Image | Source | Base | Contains |
|-------|--------|------|----------|
| `hiveplane/api` | `Dockerfile` | `python:3.12-slim` | `hiveplane` package, API + CLI, `examples/` entrypoints, `deploy/testdata/` fixtures, `langgraph` extra |
| `hiveplane/ui` | same image, `hiveplane-ui` command | `python:3.12-slim` | operator UI |

Build requirements:

- `COPY examples/` so workload entrypoints load (`examples.repo_agent:run`,
  `examples.docs_agent:graph`, `examples.incident_agent:run`).
- Install the **langgraph extra** so the docs-agent graph imports.
- `COPY deploy/testdata/` so tool fixtures and replay data resolve in-container (#143). The default
  fixture root is `deploy/testdata/tools` (`FixtureToolExecutor`) and replay is
  `deploy/testdata/llm/replay.json` (`.env.ci` `HIVEPLANE_MODEL__REPLAY_FILE`).
- `HIVEPLANE_EXECUTION__ENTRYPOINTS_ROOT=/app` must point at the copied examples root.

Target (owned by #94): images are tagged with the package version and `latest`; CI builds and tags
by commit SHA.

## 2. Service Topology

`docker compose up -d` starts:

| Service | Port | Role |
|---------|------|------|
| `postgres` | 5432 | system of record (runs, audit, certs, budget, registry) |
| `redis` | 6379 | queue / signaling |
| `otel-collector` | 4317/4318 | trace/metric/log ingestion |
| `tempo` | 3200 | trace storage |
| `prometheus` | 9090 | metrics |
| `grafana` | 3000 | dashboards |
| `api` | 8100 | control plane (host port 8000 is reserved for OMLX) |
| `ui` | 3001 | operator UI |
| `webhook-sink` *(profile: test)* | 8081 | captures Slack/generic webhook deliveries |

**No container provides a local LLM.** The `local` profile relies on **OMLX (`mlx_lm.server`)
running on the host** at `127.0.0.1:8000/v1`; containers reach it via
`host.docker.internal:8000/v1`. There is no `ollama` service (host port 8000 is reserved and no
HivePlane service may bind it).

Durable state:

- `postgres-data` volume — system of record. Migrations run on API startup.
- `checkpoint-data` volume mounted at `/app/.hiveplane/checkpoints`, with
  `HIVEPLANE_EXECUTION__CHECKPOINT_PATH=/app/.hiveplane/checkpoints/graph.json` — LangGraph graph
  checkpoints, so a paused docs-agent run survives container recreate (#122).

Profiles: `ci` (fake provider), `local` (OMLX on the host), `cloud` (OpenAI), `test`
(webhook-sink). See §5-§6.

## 3. Layered Test Matrix

Tests live in `tests/docker/` and are organized by layer. Each layer gates the next; the suite is
marked `docker` and excluded from the default `pytest` run (`pytest tests/docker -m docker`).

| Layer | What is tested | How | Pass criteria |
|-------|----------------|-----|---------------|
| **L0 — Image build** | Image builds; every example entrypoint imports inside it; runtime fixtures are present | `docker build`; import each entrypoint + `langgraph` in the image; stat fixture files | Build succeeds; all entrypoints resolve; `deploy/testdata/{tools,llm}` present |
| **L1 — Stack health** | All services start and become healthy | `docker compose up -d`; poll healthchecks and `/readyz` | All healthy within timeout; `/readyz` returns 200 when the adapter is attached and 503 with a reason when disabled (#130) |
| **L2 — API contract** | Every REST endpoint behaves per schema | HTTP calls from the test runner | Status codes + response shapes match; error paths return specific errors |
| **L3 — UI** | Every screen renders and is operable | Playwright (`tests/e2e`) against the live stack | Screens load; key actions work; no console errors |
| **L4 — Control loop** | register → certify → run → intervene → deliver | CLI + API driven end-to-end | Full loop completes; evidence captured |
| **L5 — Governance** | budget, sandbox caps, approvals, shaping | Seeded governance scenarios | Each scenario blocks/caps/shapes as specified |
| **L6 — Durability** | pause → restart → resume; startup recovery; migrations; persistence; checkpoints | `docker compose restart` mid-run | Paused run resumes from its graph node; interrupted running runs reconcile to `failed` with a `recovery` event (#111); data + attestations survive (#122) |
| **L7 — LLM matrix** | local (OMLX) provider is **required**; fake is the deterministic baseline | Same workload run with the provider selected by profile | Provider selected by config; identity/usage reported; **the run FAILS when OMLX is unreachable — zero skips** (see §5) |

### L2 — API endpoints to cover

- Health: `GET /healthz`, `GET /readyz`
- Manifest: `GET /manifest/schema`
- Registry: `POST /workloads`, `GET /workloads`, `GET/PUT/DELETE /workloads/{name}`,
  `GET /workloads/{name}/versions`, `GET /workloads/{name}/versions/diff`,
  `GET /workloads/{name}/admission`, `POST /workloads/{name}/promote`,
  `GET /workloads/{name}/attestations`, `GET /attestations/{id}`
- Runs: `POST /runs`, `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/events`,
  `GET /runs/{id}/usage`, `GET /runs/{id}/story`,
  `POST /runs/{id}/start|pause|resume|stop`, `POST /runs/{id}/tool-calls`
- Policy: `POST /policy/evaluate`, `GET/POST /policy-packs`
- Approvals: `GET /approvals`, `GET /approvals/{id}`, `POST /approvals/{id}/approve|deny`
- Certifications: `POST /certifications`, `GET /certifications`,
  `GET /certifications/{id}`, `GET /certifications/compare/{before_id}/{after_id}`
- Spend: `GET /spend`
- Tools/triggers: `GET/POST /tools`, `GET /workloads/{name}/triggers`,
  `POST/DELETE /workloads/{name}/triggers/...`
- Internal sandbox channel (token-guarded; exercised by L4/L5, not by external clients):
  `POST /internal/sandbox/{run_id}/tool-call|model|usage|result|failure`,
  `GET /internal/sandbox/{run_id}/control`

Each external endpoint: happy path + at least one error path (404/409/403/422 as applicable).

### L3 — UI screens to cover

- Fleet list
- Run detail (timeline: admission, state, tool calls, model calls with prompt/response, usage, deliveries, approvals; #124)
- Approval queue (approve/deny)
- Certification dashboard
- Spend view
- Agent health (deferred; not present in v0.1.0)

## 4. Dummy Data

Runtime fixtures live in `deploy/testdata/`; workloads and corpora live in `examples/`:

| Fixture | Location | Purpose |
|---------|----------|---------|
| `repo-agent.yaml`, `docs-agent.yaml`, `incident-agent.yaml` | `examples/workloads/` | three primary workloads |
| `repo-agent` corpus | `examples/corpora/repo-agent/v2/corpus.yaml` | ≥ 5 deterministic tasks |
| `docs-agent` corpus | `examples/corpora/docs-agent/v2/corpus.yaml` | ≥ 5 deterministic tasks |
| `incident-agent` corpus | `examples/corpora/incident-agent/v1/corpus.yaml` | ≥ 5 deterministic tasks |
| `tools/<tool_id>.json` | `deploy/testdata/tools/` | fixture tool responses (#116) |
| `llm/replay.json` | `deploy/testdata/llm/` | recorded completions for the fake/replay provider (#137) |

To be added by #93 (mapped to release scenarios):

| Fixture | Purpose |
|---------|---------|
| `examples/workloads/uncertified-agent.yaml` | fourth workload, never certified (S2) |
| `examples/workloads/model-swap-agent.yaml` | manifest identity ≠ runtime model (S3) |
| `examples/workloads/regressed-agent.yaml` | seeded regression for the promotion gate (S4) |
| `deploy/testdata/governance/over-budget.json` | usage exceeding per-run/per-day budget (S5) |
| `deploy/testdata/governance/destructive-call.json` | destructive tool call requiring approval (S6) |
| `deploy/testdata/governance/large-output.json` | tool output > `max_bytes` (S7) |

Dummy-data rules:

- No real secrets; no live network in CI.
- Fixtures are realistic enough that the agent produces meaningful output (#123).
- Each negative scenario maps to a release gate.

## 5. Local LLMs

**Yes — local LLMs are exercised, via OMLX (`mlx_lm.server`) running on the host**, but only in the
`local` profile and only when OMLX is actually reachable. This is intentional: a local model cannot
be shipped as a hermetic compose service.

- Provider `local` targets the host OMLX at `http://host.docker.internal:8000/v1`
  (native control-plane runs use `127.0.0.1:8000/v1`).
- **Host port 8000 is reserved for OMLX** — no HivePlane service binds it (API maps to 8100).
- Model identity uses the canonical `provider/family/version` form (e.g.
  `omlx/qwen2.5-7b-instruct/4bit`), priced at zero in the budget table.
- The fake/replay provider is used for CI (no network, no secrets, deterministic).

### Zero-skip requirement (L7)

The docker suite runs against **real local inference** and must never silently pass without a model.
There are **no skips**: if the local LLM is unavailable the run fails.

- The runner (`scripts/docker-test.sh`) performs a **preflight** that calls the local
  OpenAI-compatible endpoint (`HIVEPLANE_MODEL__BASE_URL`, default
  `http://127.0.0.1:8000/v1`) and exits non-zero with a clear message if it is unreachable.
- The L7 test (`tests/docker/test_llm_matrix.py`) asserts the endpoint is reachable and that a real
  completion returns a server-reported model identity; an unreachable endpoint is a **failure**,
  not a skip.
- The `local` provider is the provider under test. CI must provision OMLX (or an equivalent
  OpenAI-compatible local endpoint) or the docker job is red.

The model actually used is reported by the provider and checked against the certification binding
(T11). See [D17: LLM Provider Design](../../design/llm-provider-design.md).

## 6. LLM Configuration Matrix

| Profile | Provider | Endpoint | Secrets | Determinism | CI | Used by |
|---------|----------|----------|---------|-------------|----|---------|
| `local` | `local` | OMLX `host.docker.internal:8000` | none | temperature 0 | **required — no skip; red if unreachable** | docker test suite, local field test |
| `ci` | `fake` | in-process replay | none | fully deterministic | optional snapshot baseline | unit/PR fast path |
| `cloud` | `cloud` | `api.openai.com` | `OPENAI_API_KEY` | temperature 0 | separate opt-in invocation, not part of the required run | optional cloud validation |

The **required** docker run uses the `local` profile; the suite fails if the local LLM is not
available. The `ci` (fake) profile is a deterministic baseline only and never substitutes for the
real-model L7 assertions.

## 7. Execution

One command:

```
make docker-test            # wraps scripts/docker-test.sh
```

Steps:

1. **Preflight (fail-fast):** verify the local LLM endpoint answers; abort non-zero if not.
2. Build images.
3. `docker compose --env-file .env.local --profile local --profile test up -d`.
4. Wait for `/readyz`.
5. Seed fixtures.
6. Run L0–L7 with `pytest tests/docker -m docker` (**no skips**).
7. Capture screenshots; write `DOCKER_TEST_REPORT.md`.
8. `docker compose down -v`.

CI: a nightly job runs the full `local`-profile docker suite; a PR job runs L0–L2 only (fast).
Exit codes are CI-friendly; any layer failure — including an unreachable local LLM — fails the job
(#59). Neither job is wired yet; the only workflow currently is `.github/workflows/ci.yml`
(lint/type/test + operator UI e2e).

## 8. Screenshots

Captured deterministically by the Playwright layer into:

```
docs/field-test/v0.1.0/screenshots/<scenario-id>/<step>-<name>.png
```

- `<scenario-id>` matches S1–S9 and UI view names.
- Reruns overwrite the same paths (repeatable).
- Embedded in the user guide. See #95. (Directory not created yet.)

## 9. Results

Every run writes its evidence to `field_test/v0.1.0/docker/<run-id>/` (build,
compose, `/readyz`, service, and pytest logs; `junit.xml`; `environment.json`)
and **commits it**. `scripts/docker_report.py` renders the detailed
`docs/field-test/v0.1.0/DOCKER_TEST_REPORT.md` from that JUnit run — per-layer
and per-test results, failures, environment, and a log index — and exits
non-zero on any failure/error or **any skip** (zero-skip policy, #96).

## 10. Out of Scope

- Container/cgroup sandbox isolation (process-level caps only; #110).
- Trigger ingestion (v0.2.0).
- MCP transport (fixture tool executor only; #116).
- A containerized local LLM (OMLX runs on the host; the `local` profile is not hermetic).
- Multi-tenant/multi-node.

## See Also

- [Field test plan](field-test-plan.md) (#97)
- [LLM provider design](../../design/llm-provider-design.md) (D17)
- [Benchmark execution design](../../design/benchmark-execution-design.md) (D19)
- [Durable resume design](../../design/durable-resume-design.md) (D18)
- [v0.1.0 index](../../wbs/v0.1.0/wbs-v0.1.0-index.md)
