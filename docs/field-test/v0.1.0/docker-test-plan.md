# HivePlane v0.1.0 — Docker Test Plan

> Status: plan (M23, #92). Defines image build, the layered test matrix, dummy data, the LLM
> strategy, and how results and screenshots are produced. Companion to the
> [field test plan](field-test-plan.md).

## Objective

Prove the certified control loop works in the shipped container topology — not just in-process
unit tests. Every layer of the stack (image → services → API → UI → control loop → governance)
is exercised against `docker compose`, with deterministic data and a hermetic CI mode.

## 1. Images

| Image | Source | Base | Contains |
|-------|--------|------|----------|
| `hiveplane/api` | `Dockerfile` | `python:3.12-slim` | `hiveplane` package, API + CLI, `examples/` entrypoints, `langgraph` extra |
| `hiveplane/ui` | same image, `hiveplane-ui` command | `python:3.12-slim` | operator UI |

Build requirements (see #112):

- The image must **copy `examples/`** so workload entrypoints load (`examples.repo_agent:run`).
- The image must install the **langgraph extra** so the docs-agent graph imports.
- `HIVEPLANE_EXECUTION__ENTRYPOINTS_ROOT` must point at the copied examples root.
- Images are tagged with the package version and `latest`; CI builds and tags by commit SHA.

Layered build (target):

1. base + system deps
2. Python deps (cacheable layer)
3. package + examples
4. runtime entrypoint

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
| `api` | 8000 | control plane |
| `ui` | 3001 | operator UI |
| `ollama` *(profile: local)* | 11434 | local LLM (OpenAI-compatible) |
| `webhook-sink` *(profile: test)* | 8081 | captures Slack/generic webhook deliveries |

Profiles: `ci` (fake provider), `local` (Ollama), `cloud` (OpenAI). See §6.

## 3. Layered Test Matrix

Tests live in `tests/docker/` and are organized by layer. Each layer gates the next.

| Layer | What is tested | How | Pass criteria |
|-------|----------------|-----|---------------|
| **L0 — Image build** | Image builds; every example entrypoint imports inside it | `docker build`; import each entrypoint in the image | Build succeeds; all entrypoints resolve |
| **L1 — Stack health** | All services start and become healthy | `docker compose up -d`; poll healthchecks and `/readyz` | All healthy within timeout; `/readyz` checks stores/migrations/adapter (#130) |
| **L2 — API contract** | Every REST endpoint behaves per schema | HTTP calls from the test runner | Status codes + response shapes match; error paths return specific errors |
| **L3 — UI** | Every screen renders and is operable | Playwright against the live stack | Screens load; key actions work; no console errors |
| **L4 — Control loop** | register → certify → run → intervene → deliver | CLI + API driven end-to-end | Full loop completes; evidence captured |
| **L5 — Governance** | budget, sandbox caps, approvals, shaping | Seeded governance scenarios | Each scenario blocks/caps/shapes as specified |
| **L6 — Durability** | pause → restart → resume; migrations; persistence | `docker compose restart` mid-run | Paused run resumes; data survives; attestations verify |
| **L7 — LLM matrix** | local, cloud (optional), fake providers | Same workload run per provider | Provider selected by config; identity/usage reported |

### L2 — API endpoints to cover

- Health: `GET /healthz`, `GET /readyz`
- Manifest: `GET /manifest/schema`
- Registry: `POST/GET/PUT/DELETE /workloads`, `/workloads/{name}/versions`, `/admission`, `/promote`, `/attestations`
- Runs: `POST /runs`, `GET /runs`, `GET /runs/{id}`, `POST /runs/{id}/pause|resume|stop|start`, `POST /runs/{id}/tool-calls`
- Policy: `POST /policy/evaluate`, `GET/POST /policy-packs`
- Approvals: `GET /approvals`, `GET /approvals/{id}`, `POST /approvals/{id}/approve|deny`
- Certifications: `POST /certifications`, `GET /certifications`, `/compare/{before}/{after}`, `/certifications/{id}`
- Spend: `GET /spend/*`
- Tools/triggers: `GET/POST /tools`, `GET /workloads/{name}/triggers`

Each endpoint: happy path + at least one error path (404/409/403/422 as applicable).

### L3 — UI screens to cover

- Fleet list
- Run detail (timeline, tool calls, model calls, usage)
- Approval queue (approve/deny)
- Certification dashboard
- Spend view
- Agent health (where present)

## 4. Dummy Data

Seed fixtures live in `deploy/testdata/` and are loaded by the test setup. All data is versioned
in-repo and deterministic.

| Fixture | Purpose |
|---------|---------|
| `workloads/repo-agent.yaml`, `docs-agent.yaml`, `incident-agent.yaml` | three real workloads |
| `workloads/uncertified-agent.yaml` | fourth, never certified (S2) |
| `workloads/model-swap-agent.yaml` | manifest identity ≠ runtime model (S3) |
| `workloads/regressed-agent.yaml` | seeded regression for the promotion gate (S4) |
| `corpora/<workload>/v1/corpus.yaml` | ≥ 5 deterministic tasks each (#98) |
| `tools/<tool_id>.json` | fixture tool responses for the tool executor (#116) |
| `governance/over-budget.json` | usage that exceeds per-run/per-day budget (S5) |
| `governance/destructive-call.json` | destructive tool call requiring approval (S6) |
| `governance/large-output.json` | tool output > `max_bytes` (S7) |
| `llm/replay-*.json` | recorded completions for the fake/replay provider |

Dummy data rules:

- No real secrets; no live network in CI.
- Fixtures are realistic enough that the agent produces meaningful output (#123).
- Each negative scenario maps to a release gate.

## 5. Local LLMs

**Yes — local LLMs are exercised, via an Ollama service in the `local` compose profile.**

- Provider `local` targets an OpenAI-compatible endpoint (`http://ollama:11434/v1`).
- Model identity uses the canonical `provider/family/version` form (e.g.
  `local/qwen2.5/7b`), priced at zero in the budget table.
- The fake/replay provider is used for CI (no network, no secrets, deterministic).
- Cloud (OpenAI) is optional and only run when `OPENAI_API_KEY` is present.

The model actually used is reported by the provider and checked against the certification binding
(T11). See [D17: LLM Provider Design](../../design/llm-provider-design.md).

## 6. LLM Configuration Matrix

| Profile | Provider | Endpoint | Secrets | Determinism | Used by |
|---------|----------|----------|---------|-------------|---------|
| `ci` | `fake` | in-process | none | fully deterministic | nightly + PR CI |
| `local` | `local` | `ollama:11434` | none | temperature 0 | local field test |
| `cloud` | `cloud` | `api.openai.com` | `OPENAI_API_KEY` | temperature 0 | optional cloud validation |

## 7. Execution

One command (see #94):

```
make docker-test            # or scripts/docker-test.sh
```

Steps: build images → `docker compose --profile <p> up -d` → wait for `/readyz` → seed fixtures
→ run L0–L7 → capture screenshots → write results → `docker compose down -v`.

CI: a nightly job runs the `ci` profile; a PR job runs L0–L2 only (fast). Exit codes are
CI-friendly; any layer failure fails the job (#59).

## 8. Screenshots

Captured deterministically by the Playwright layer into:

```
docs/field-test/v0.1.0/screenshots/<scenario-id>/<step>-<name>.png
```

- `<scenario-id>` matches S1–S9 and UI view names.
- Reruns overwrite the same paths (repeatable).
- Embedded in the user guide. See #95.

## 9. Results

`docs/field-test/v0.1.0/DOCKER_TEST_REPORT.md` (#96) records per-layer results, observations,
fixes, and takeaways, plus which LLM providers were exercised.

## 10. Out of Scope

- Container/cgroup sandbox isolation (process-level caps only; #110).
- Trigger ingestion (v0.2.0).
- MCP transport (fixture tool executor only; #116).
- Multi-tenant/multi-node.

## See Also

- [Field test plan](field-test-plan.md) (#97)
- [LLM provider design](../../design/llm-provider-design.md) (D17)
- [Benchmark execution design](../../design/benchmark-execution-design.md) (D19)
- [Durable resume design](../../design/durable-resume-design.md) (D18)
- [v0.1.0 index](../../wbs/v0.1.0/wbs-v0.1.0-index.md)
