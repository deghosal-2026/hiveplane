# HivePlane v0.2.0 — Field Test Plan

> Status: **plan** (M61-01, #476). Objective: exercise every pillar of the Complete Fleet OS
> against a live stack with **25 scenarios (S1–S25)** plus a **≥50-concurrent-run load test**,
> mapping **all 34 v0.2.0 release gates** ([PRD 09](../../prd/09-roadmap.md)) to at least one
> scenario. Preview: [docker-test-plan.md](docker-test-plan.md) validates the container/API/UI
> layers; this plan validates the real-agent certified lifecycle and the fleet-OS pillars.
> Precedent: [v0.1.0 field test plan](../v0.1.0/field-test-plan.md).

## Scope

**Required (release gate):** the Tier 1 workloads (`support-agent`, `eval-judge`) plus the
v0.2.0 fixture workloads, run through the certified control loop and every v0.2.0 pillar
(autonomy, immune system, progressive delivery, defense, health, MCP v2, secrets/RBAC,
delivery, cost/ROI, fleet control, tenancy, artifacts, API v2, reporting, operator surface),
covering S1–S25 and all 34 release gates. Same stack the [docker test plan](docker-test-plan.md)
validates at the container layer.

**Optional / deferred:** the Tier 2 platform-coverage inventory (113 agents / 12 frameworks)
demonstrates framework-agnostic certification; it is tracked separately and is not a v0.2.0
release gate.

## Prerequisites

- Docker + Docker Compose; local OMLX serving **`Qwen3-4B-Instruct-2507-4bit`** on host port 8000
  (canonical identity `omlx/qwen3-4b-instruct-2507/4bit`); Playwright chromium.
- All M25–M60 milestones complete (`feat-v0.2.0` + review fixes).
- `field_test/` corpora, shims, and v0.2.0 fixtures present; `HIVEPLANE_CERTIFICATION__CORPORA_DIR=field_test`.

## Objective

Prove the fleet **runs itself, defends itself, explains itself, and scales itself** with real
agents: register → certify → trigger/pipeline → run under budget/policy → defend → learn →
deliver → report → reconcile, and sustain ≥50 concurrent runs.

## Status & outstanding testing (2026-10-01)

**Docker container track (this plan's container/API/UI preview): green.** 67/67 pass in
[DOCKER_TEST_REPORT.md](DOCKER_TEST_REPORT.md); the four original failures were resolved and
re-verified (commit `c47d08d`). Two were environmental (S4 stale image; S11 test fixture)
and two were genuine product-seam defects now fixed (S7 pipeline child runs never started;
run fan-out deliveries recorded but unexposed). This validates S1–S25 at the container layer.

**Real-agent field-test track: not yet executed.** [FIELD_TEST_REPORT.md](FIELD_TEST_REPORT.md)
reports **0/25**, generated from an empty `field_test/v0.2.0/results/summary.json`. The
certified real-agent lifecycle (Phases 2–8 with Tier 1 `support-agent` / `eval-judge`) still
needs its end-to-end pass.

**Outstanding testing required:**

1. Execute the real-agent Tier 1 lifecycle and populate per-scenario evidence into
   `field_test/v0.2.0/results/`, so `summary.json` and `FIELD_TEST_REPORT.md` reflect the run.
2. Add the regression scenarios below (**S26**, **S27**) and the hardened **S7** to lock in
   the two product-seam fixes (they must fail if the seams regress).
3. **M61-08** — the k3d (Helm) multi-worker load test, still open and outside the Docker
   container track.

## Agent & Fixture Inventory

### Tier 1 — primary workloads

| Workload | Adapter | Stresses |
|----------|---------|----------|
| `support-agent` | raw-worker | destructive tool + approval/re-dispatch, action audit, certification |
| `eval-judge` | langgraph | interrupt → paused run, durable checkpoint resume, action audit |

### v0.2.0 fixtures

| Fixture | Purpose | Scenarios |
|---------|---------|-----------|
| `uncertified-agent` | refused production admission | S1 |
| `regressed-agent` | fails certification / promotion gate / regression diff | S2, S3 |
| `drifting-agent` | seeded drift → auto-quarantine → reinstate after re-cert | S4, S5 |
| `pipeline-*` (2–3 nodes) | multi-agent DAG with per-step gates | S7 |
| `canary-candidate` | 10% canary → auto-promote | S8 |
| `injection-agent` | injection-laden tool output / task input | S11 |
| `tenant-b-agent` | second-tenant isolation (budget/policy/key) | S6, S16, S17 |
| `secret-ref-agent` | references a secret; redaction absence tests | S16 |
| `worker-*` | distributed execution, lease expiry, preemption | S18, S19 |
| `provenance-agent` | tampered bundle fails admission | S25 |
| `probe-agent` | synthetic probe flags decay | S24 |

## Phases

**Evidence:** every scenario captures raw evidence (run ids, events, usage, attestations,
command output, logs) into **`field_test/v0.2.0/results/`**. The narrative and the merged
load-test results are written to `FIELD_TEST_REPORT.md`.

**API address:** `http://localhost:8100` (`HIVEPLANE_API_URL`); UI `http://localhost:3001`;
OMLX on host port 8000. Pass `--api-url http://localhost:8100` to CLI commands.

### Phase 1 — Baseline

- Stack starts with one command; `/readyz` honest; MinIO/worker/mcp fixture healthy.
- Tier 1 + v0.2.0 fixtures register; tools seeded (incl. MCP live transport); model identity resolves.

### Phase 2 — Immune system (S1–S5)

- S1 certify Tier 1 (staging→provisional→production→certified); signed attestations; uncertified refused.
- S2 promotion gate blocks a regression with a replayable diff.
- S3 regression diff classifies a seeded pass→fail change.
- S4 seeded drift auto-quarantines the agent and notifies.
- S5 reinstatement after re-certification.

### Phase 3 — Autonomy & orchestration (S6–S10)

- S6 triggers fire from ≥3 sources (webhook + GitHub + Alertmanager; cron included) with dedup/cooldown proven.
- S7 a 3-node pipeline runs end-to-end with per-step gates.
- S8 canary routes 10% to the candidate and auto-promotes on clean results.
- S9 a shadow run mirrors production without delivery and produces an outcome diff.
- S10 agent-as-tool nesting propagates budget/policy/certification to the nested run.

### Phase 4 — Defense & health (S11–S15)

- S11 seeded injection blocked (task input + tool output); repeated attempts quarantine.
- S12 context budget exceeded → run pauses cleanly with accounting shown.
- S13 spend-velocity breach → run pauses.
- S14 circuit breaker trips at the tool boundary and recovers.
- S15 egress to a disallowed host denied and audited.

### Phase 5 — Secrets, identity & tenancy (S16–S17)

- S16 a secret never appears in logs/traces/agent context (absence test); cross-tenant secret ref rejected.
- S17 RBAC viewer denied approve/promote/kill-switch/run-control; per-tenant budget/policy/key isolation; over-limit tenant gets 429.

### Phase 6 — Fleet scale (S18–S20)

- S18 a worker daemon executes a run on a second host; kill it → lease expiry reassigns the run (attempt+1, attributed).
- S19 an urgent run preempts a best-effort run at an idempotent checkpoint with attribution.
- S20 a dead trigger replays from the DLQ exactly once.

### Phase 7 — Cost, reporting & portability (S21–S25)

- S21 showback attributes cost by tenant → team → agent with cost-per-completed-task.
- S22 a cache hit reuses a result and shows savings; re-certification invalidates it.
- S23 Git deletes an agent → the plane deregisters it; a cert-threshold change fires re-cert (GitOps reconciliation).
- S24 a synthetic probe flags decay before the drift threshold trips.
- S25 an attestation verifies publicly by ID; the kill switch disables a tool fleet-wide; a provenance-signature mismatch fails admission; a worker without a signed token is refused.

### Phase 8 — Scale, HA & review

- Load test: ≥50 concurrent runs; throughput, latency, error rate, resource use recorded.
- k3d (Helm) multi-worker execution (M61-08).
- Second controller replica does not double-reconcile (leader election verified); chaos drills recover/halt.
- Operator review: fleet/health/SLO/drift dashboard, queue visualizer, cost/ROI, trigger log, replay, audit completeness; incident mode halts <5s.

## Scenario Map (S1–S25)

| # | Scenario | Phase | Gates |
|---|----------|-------|-------|
| S1 | Certification + signed attestation; uncertified refused production | 2 | 1, 25 |
| S2 | Promotion gate blocks a regression with a replayable diff | 2 | 2 |
| S3 | Regression diff classifies pass→fail | 2 | 2 |
| S4 | Drift → auto-quarantine + notify | 2 | 1 |
| S5 | Reinstatement after re-cert | 2 | 1 |
| S6 | Triggers from ≥3 sources with dedup/cooldown | 3 | 3 |
| S7 | 3-node pipeline end-to-end with per-step gates | 3 | 11 |
| S8 | Canary 10% → auto-promote | 3 | 12 |
| S9 | Shadow run (no delivery) + outcome diff | 3 | 12 |
| S10 | Agent-as-tool propagates budget/policy/cert | 3 | 31 |
| S11 | Injection blocked; repeated attempts quarantine | 4 | 4 |
| S12 | Context budget exceeded → clean pause + accounting | 4 | 20 |
| S13 | Spend-velocity breach → pause | 4 | 20 |
| S14 | Circuit breaker trips and recovers | 4 | 8, 14 |
| S15 | Egress denied and audited | 4 | 8 |
| S16 | Secret never leaks; cross-tenant ref rejected | 5 | 13 |
| S17 | RBAC viewer denial + per-tenant isolation + 429 | 5 | 6, 34 |
| S18 | Worker kill → lease reassign | 6 | 19, 33 |
| S19 | Urgent preempts best-effort, attributed | 6 | 18 |
| S20 | Dead trigger replays from DLQ once | 6 | 14 |
| S21 | Showback + cost-per-completed-task | 7 | 9 |
| S22 | Cache hit + savings; re-cert invalidates | 7 | 21 |
| S23 | GitOps: delete → deregister; threshold change → re-cert | 7 | 17 |
| S24 | Synthetic probe flags decay before drift trips | 7 | 22 |
| S25 | Public verify + kill switch + provenance + worker-token refusal | 7 | 25, 29, 30 |
| S26 | Run fan-out delivery is auditable and reaches the sink (regression) | 7 | 5 |
| S27 | Trigger-driven run executes to completion without an operator start (regression) | 3 | 3 |
| S28 | Defense ordering, both directions (denied tool vs allowed tool + injection) | 4 | 4, 8 |
| S29 | Run fan-out audit and M51 notification audit are distinct and each populated | 7 | 5, 15 |
| S30 | Run fan-out failure path records a delivery to the `on_failed` channel | 7 | 5 |
| S31 | Pipeline retry starts exactly one child per attempt (idempotency) | 3 | 11 |
| Load | ≥50 concurrent runs (compose + k3d) | 8 | 19, 33 |
| Review | Health/SLO/burn, incident halt <5s, audit, reports | 8 | 7, 8, 16, 23, 24, 26, 27, 28, 32 |

### Post-remediation scenarios & hardening (2026-10-01)

These additions lock in the two product-seam fixes from the Docker remediation and close the
coverage gaps they exposed.

- **S7 (hardened) — pipeline executes every node's child run.** Beyond "the pipeline reaches
  `completed`", assert that each node's child run transitions `queued → running` and reaches a
  terminal state, and that a node left `queued` is a failure. This is the regression signal
  for `RunNodeExecutor.submit` no longer starting its child (`executor.py`). Unit-level guard:
  `tests/test_pipelines_engine.py::test_pipeline_starts_its_child_runs`.
- **S26 — run fan-out delivery is auditable and reaches the sink (new).** For a completed run
  whose workload declares a destination, assert `GET /runs/{run_id}/deliveries` returns the
  configured destination with a terminal (`delivered`) status, and that the webhook sink
  captured the payload. This is the regression signal for the run fan-out audit now being
  exposed (`GET /runs/{run_id}/deliveries`). Unit-level guard:
  `tests/test_api_runs.py::test_run_deliveries_lists_fan_out_attempts`.
- **S27 — trigger-driven run executes to completion (new).** A webhook/cron trigger fires,
  submits a run, and that run reaches a terminal state **without an operator start**,
  exercising the same `queued → running` seam as S7. This is currently an open question: in
  the workerless Docker profile, trigger submissions are only admitted `queued` and nothing
  transitions them (the same class of defect as S7). The scenario must either (a) start the
  run explicitly, or (b) run a worker that leases queued runs. Decide and encode the intended
  behaviour before marking the trigger path green.
- **S28 — defense ordering, both directions (new).** A *disallowed* tool call returns
  `denied`; an *allowed* tool carrying injection text returns `blocked_injection`. This proves
  the output scanner is reached only after the tool boundary passes, and that a denied tool
  never reaches it (the false-negative that hid the real behaviour behind D-2).
- **S29 — delivery surfaces are disambiguated (new).** `GET /delivery/audit` (M51
  `delivery_attempts` via `DeliveryService`) and `GET /runs/{run_id}/deliveries` (execution
  `fan_out_deliveries` via `FanOutService`) each populate from their own path and do not
  cross-contaminate. Guards against re-conflating the two stores.
- **S30 — run fan-out failure path (new).** A run that terminates `failed` and declares
  `on_failed` destinations records a delivery attempt to the failure channel (the success path
  is covered by S26).
- **S31 — pipeline retry idempotency (new).** A retried pipeline node starts exactly one child
  per attempt; after a retry there are no duplicate live children and the parent progresses.
  Guards the interaction between retries and the new "start on submit" behaviour.

### Hardening rules (adopted 2026-10-01)

The first v0.2.0 draft passed scenarios on **permissive/surface assertions** ("any of
200/201/403/404/409", "pass on `[]`"), which manufacture false confidence. The following rules
are now mandatory for every scenario (see `FIELD_TEST_REPORT.md` §4, §10):

1. **One exact outcome per scenario.** Assert the exact expected status **and** resulting state;
   no "any of N statuses".
2. **Positive evidence required.** The scenario must *observe the behavior happen* — a paused run
   observed, a lease actually reassigned, a DLQ entry created then replayed, a regression seeded
   then caught. Empty/no-op results **fail**.
3. **Deep probe + logs are part of the scenario.** Every scenario writes `probe.json`/`probe.md`
   and a full HTTP trace; every run it touches carries usage (raw LLM prompt/response). The
   evidence contract is enforced by
   `tests/test_field_test_runner_v02.py::test_committed_scenario_evidence_satisfies_the_contract`.
4. **`surface` never counts toward a gate.** A scenario that only checks that an endpoint responds
   is labelled `surface` and cannot demonstrate a release gate until hardened (tracked per
   scenario as M61-29…M61-41).

**Profiles / environments.** The suite runs three passes: default (zero-cost local model), `--auth`
(bootstrap admin key, RBAC/tenancy, 429 leg of gate 34), and (planned) `--priced` (non-zero
`HIVEPLANE_BUDGET__PRICES`, zero-cost prefix dropped, for live budget/velocity enforcement). The
k3d/Helm multi-worker environment is exercised separately (M61-08 / M61-27).

**Scenario map.** The suite is **S1–S31 + H1–H5** (36 scenarios), not S1–S25; see the Scenario Map
above and `FIELD_TEST_REPORT.md` §2.

### Layer-level test additions (Docker container track)

These are layer checks rather than scenario narratives; they belong to both this plan and
[docker-test-plan.md](docker-test-plan.md).

| ID | Level | Test | Asserts |
|----|-------|------|---------|
| H1 | L0/L1 | Image matches source | The running API's build SHA/label equals `git rev-parse HEAD`; a stale image fails fast instead of producing phantom failures (the root of D-1). |
| H2 | L2/L7 | Execution-model contract | A freshly submitted run is `queued` and does **not** execute in the workerless profile unless explicitly started or leased — pins the intended semantics behind S27. |
| H3 | L11/L12 | `run_events` concurrent append | N concurrent appends to one run yield strictly unique, contiguous `sequence` values (guards the `FOR UPDATE` fix). |
| H4 | L12 | Startup recovery with orphaned run | API lifespan starts cleanly when a non-terminal run's workload has been deleted. |
| H5 | L4 | Quarantine → reinstate full cycle | Sandbox re-certification succeeds while quarantined and `POST /quarantines/{id}/reinstate` returns 200 (guards the `SANDBOX` exemption — the real content behind D-1). |

## Acceptance Criteria — all 34 release gates

| Gate | Description | Scenario(s) |
|------|-------------|-------------|
| 1 | Drifting agent auto-quarantined, notified, reinstated after re-cert | S4, S5 |
| 2 | Promotion gate blocks a regression with a replayable diff | S2, S3 |
| 3 | Triggers fire from ≥3 sources with dedup/cooldown | S6 |
| 4 | Seeded injection blocked; repeated attempts quarantine | S11 |
| 5 | Slack approvals + fan-out to ≥3 channels; mobile approvals | Delivery / Review |
| 6 | Per-tenant budget/policy/key isolation; viewer cannot approve | S17 |
| 7 | Helm chart deploys the full stack to k3d | Review / k3d |
| 8 | Health dashboard, burn-through throttle, breaker trips/recovers | S14, S15, Review |
| 9 | Showback by tenant → team → agent with cost-per-completed-task | S21 |
| 10 | Frame-by-frame replay + run diff; forked run re-runs edited state | S9, Review |
| 11 | Pipeline runs a multi-agent DAG end-to-end with per-step gates | S7 |
| 12 | Canary routes 10% and auto-promotes on clean results | S8, S9 |
| 13 | A secret never appears in logs/traces/agent context | S16 |
| 14 | Dead trigger replays from DLQ; breaker trips/recovers | S20, S14 |
| 15 | SDK + API v2 round-trip; a plugin hook fires | API v2 track / Review |
| 16 | Weekly digest auto-generates; artifact stored, linked, retained | Review |
| 17 | Git deletes an agent → deregister; threshold change → re-cert | S23 |
| 18 | Urgent run preempts best-effort with attribution | S19 |
| 19 | Worker on a second host; kill → lease expiry reassigns | S18, Load |
| 20 | Context budget exceeded → clean pause with accounting | S12, S13 |
| 21 | Cache hit reuses result + shows savings; re-cert invalidates | S22 |
| 22 | Synthetic probe flags decay before drift threshold | S24 |
| 23 | `ask` answers 5 live-state questions under budget + cert | Review |
| 24 | Incident mode halts fleet in <5s and broadcasts | Review |
| 25 | Attestation verifies publicly; kill switch disables a tool | S1, S25 |
| 26 | Signed image + SBOM; retention purge deletes tenant data | Review / release |
| 27 | Operator-flagged failed run becomes a corpus case | Review |
| 28 | Sampled production runs get judge scores; quality dip alerts | Review |
| 29 | Modified bundle fails admission on provenance mismatch | S25 |
| 30 | Worker without a signed token is refused | S25 |
| 31 | Agent-as-tool calls propagate budget/policy/certification | S10 |
| 32 | Second controller replica does not double-reconcile | Review |
| 33 | Chaos drills recover/halt (kill worker; revoke cert) | S18, Review |
| 34 | Per-workload service endpoint serves a run through all gates; 429s | S17 |

## LLM Configuration

| Environment | Provider | Endpoint | Determinism |
|-------------|----------|----------|-------------|
| **Local field test (current)** | `local` (OMLX) | `host.docker.internal:8000/v1` | temperature 0 |
| CI / nightly | `fake` (replay) | in-process | fully deterministic |
| Cloud (opt-in) | `cloud` | `api.openai.com` | temperature 0 |

**The v0.2.0 field test runs on the local model `Qwen3-4B-Instruct-2507-4bit`** (canonical
identity `omlx/qwen3-4b-instruct-2507/4bit`). `spec.model.identity` must match the provider;
model aliases map the served name to the canonical identity. Host port 8000 is reserved for OMLX.

## Runner & Scripts

There is a single real-agent runner for v0.2.0 (the v0.1.0 harness is retired):

| Script | Role |
|--------|------|
| `scripts/field-test-v02.sh` | Orchestrator: preflight the local LLM, reset volumes, bring the stack up, seed tools, run setup, invoke the runner, render the report. Flags: `--keep`, `--no-build`, `--auth`, `--only S1,S26`. |
| `scripts/field_test_runner_v02.py` | Scenario driver: implements S1–S25 (supported), S26–S31 (post-remediation regressions), and H1–H5 (harness checks). Writes one evidence directory per scenario plus `summary.json`. |
| `scripts/field_test_report_v02.py` | Renders `docs/field-test/v0.2.0/FIELD_TEST_REPORT.md` from `summary.json`. |
| `scripts/field_test_setup.sh` | Seeds the tool registry and registers the Tier 1 and negative workloads. |

The runner records a per-scenario `status` (`pass`/`fail`/`blocked`) and a `passed`
boolean, so the report script can render both the scenario table and the 34-gate mapping.
A scenario that needs a capability the profile lacks (e.g. S16/S17 without `--auth`,
H4 without a restart command) is recorded `blocked`, never silently skipped. H1 compares
the running image's `org.opencontainers.image.revision` label against `git rev-parse HEAD`
so a stale image fails fast (the root of the original S4 false negative).

## Load Test

- **Compose:** sustain ≥50 concurrent sandbox runs concurrently against `api`; record
  throughput (runs/s), p50/p95 latency, error rate, queue depth, and container
  CPU/memory. Zero correctness failures.
- **k3d (M61-08):** deploy the Helm chart and run the same load against the multi-worker
  cluster; verify lease balancing across workers.

## Repeatability Procedure

1. `scripts/docker-test-v02.sh` — resets volumes, brings the stack up on the local model,
   seeds fixtures, runs the container/API/UI layers (L0–L13) and the load test, writes
   evidence to `field_test/v0.2.0/docker/` and the report to
   `docs/field-test/v0.2.0/DOCKER_TEST_REPORT.md`.
2. `scripts/field-test-v02.sh` — the **real-agent** track: brings the stack up, seeds
   tools, runs `field_test_runner_v02.py` (S1–S31 + H1–H5), writes per-scenario evidence
   to `field_test/v0.2.0/results/` and the report to
   `docs/field-test/v0.2.0/FIELD_TEST_REPORT.md`. Use `--auth` for the S16/S17 pass.
3. `make test-e2e` — Playwright UI v2 (in-process); screenshots → `docs/field-test/v0.2.0/screenshots/`.
4. Prerequisite: OMLX serving `Qwen3-4B-Instruct-2507-4bit` on host port 8000 (fail-fast, no skips).
5. Subset: `--only S6,S7`; keep stack: `--keep`; reuse image: `--no-build`.

## Reporting

Raw evidence is written to **`field_test/v0.2.0/results/`** (per scenario) and committed. The
concise narrative, metrics, and learnings are recorded in
[FIELD_TEST_REPORT.md](FIELD_TEST_REPORT.md); the generated container report is
[DOCKER_TEST_REPORT.md](DOCKER_TEST_REPORT.md); the load numbers are in the
[`FIELD_TEST_REPORT.md`](FIELD_TEST_REPORT.md#load-test) `## Load Test` section. Each claim
links to its evidence.
