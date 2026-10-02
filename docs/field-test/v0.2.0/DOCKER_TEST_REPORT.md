# HivePlane v0.2.0 — Docker Test Report

| Field | Value |
|-------|-------|
| Document status | **Final — PASS** |
| Suite | `tests/docker` (L0–L13), runner `scripts/docker-test-v02.sh` |
| Original run | `2026-10-01T01:32:01Z`, revision `bc1e82b` |
| Remediation and re-verification | `2026-10-01`, revision `22b9ba1` + remediation changes |
| Prepared by | Engineering (field-test remediation) |

---

## Executive Summary

The HivePlane v0.2.0 Docker suite is assessed as **passing**: 67 of 67 scenarios pass.
The original generated report recorded 63 passed and 4 failed. A systematic
re-verification established that the four recorded failures were not homogeneous: two were
artefacts of the test environment, and two were genuine product defects that the suite had
correctly detected.

The two product defects were both **integration-seam** faults. First, pipeline execution
created each node's child run but never started it, so a pipeline could never progress to
completion. Second, terminal run fan-out delivered successfully and recorded the attempts,
but no API surface exposed those records, so a successful delivery was unauditable through
the control plane. Both have been corrected, and each correction is accompanied by a
regression test that failed before the change and passes after it. All four affected
scenarios were re-verified green against the live Docker stack.

No product defect remains open from this run. One design question is recorded for
follow-up: whether triggered workload runs should auto-start in the workerless Docker
profile, which is the same class of issue as the pipeline defect.

## 1. Scope and Method

This report covers the v0.2.0 container and scenario suite, comprising the fourteen layers
L0–L13 executed by `scripts/docker-test-v02.sh` against the full local Docker Compose stack
(control-plane API, operator UI, Postgres, Redis, observability, and the test webhook
sink). Each of the four originally-failing scenarios was reproduced in isolation against
the live stack and traced to a root cause before any corrective action was taken. Where a
product defect was confirmed, a failing unit test was written first, the minimal fix was
applied, and the scenario was then re-run end-to-end. Unit-level linting (`ruff`) and
static typing (`mypy`) were run on all changed modules.

## 2. Summary of Findings

The original run reported the following four failures:

| ID | Layer | Scenario | Original result |
|----|-------|----------|-----------------|
| D-1 | L4 | `test_s4_drift_probe_and_quarantine` | failure (`403 != 201`) |
| D-2 | L5 | `test_s11_injection_output_is_blocked_and_recorded` | failure (outcome not `blocked_injection`) |
| D-3 | L6 | `test_s7_pipeline_runs_end_to_end` | failure (pipeline never completes) |
| D-4 | L9 | `test_fan_out_delivers_to_the_webhook_sink` | failure (no delivery recorded) |

Investigation classified these as follows:

- **D-1 and D-2 were not product defects.** D-1 was caused by testing against a stale
  container image that predated an already-committed fix. D-2 was caused by a test fixture
  that drove a disallowed tool, so the injected output never reached the scanner.
- **D-3 and D-4 were genuine product defects** at integration seams, and are the primary
  engineering outputs of this exercise. Both were invisible to unit tests that stopped at
  the seam and were caught only by the end-to-end Docker scenarios.

All four are resolved and re-verified. The complete evidence is given in Appendix A.

## 3. Defect Analysis and Resolution

### 3.1 D-1 — Quarantined workload could not be reinstated

**Symptom.** Following quarantine of `support-agent`, a production re-certification request
returned HTTP 403. Consequently `POST /quarantines/{id}/reinstate` could never succeed, as
the re-certification that must precede reinstatement was refused (`assert 403 == 201`).

**Root cause.** The policy engine denied a staged re-certification run whenever the
workload was quarantined, irrespective of environment. The source had already been
corrected; however, the container image under test predated the correction, so the running
plane still enforced the old behaviour.

**Resolution.** No source change was required. The already-committed exemption in
`src/hiveplane/policy/engine.py` (`SANDBOX` environments are not denied by quarantine) was
confirmed present in a freshly built image, and the scenario passed.

**Verification.** The API image was rebuilt (`docker compose build api`); the running
container's `PolicyEngine._evaluate` source was inspected and confirmed to contain the
`AdmissionContext.SANDBOX` exemption; the scenario passed in approximately two seconds.

**Lesson.** A test report is only as current as the image under test. Reports generated
against a stale image produce false negatives and must be re-run after a rebuild.

### 3.2 D-2 — Injected tool output not classified as blocked

**Symptom.** The injected tool output was not classified `blocked_injection`.

**Root cause.** The scenario exercised `repo-agent` with tool `mcp.t.read`, which that
workload does not permit. The tool boundary returned `denied` before output shaping and
injection scanning were reached. The scanner itself was correct: the
`injection.instruction_override` detector matched the seeded text.

**Resolution.** The test fixture was corrected (in revision `22b9ba1`) to use
`support-agent` with a permitted tool (`mcp.github.read_issue`), so the output reaches the
scanner and is blocked.

**Verification.** The scenario passes in isolation.

**Lesson.** Tool-boundary denial short-circuits output defence. A defence test must call a
tool the workload actually permits, or it silently exercises the wrong rule.

### 3.3 D-3 — Pipeline never completes (product defect)

**Symptom.** A pipeline node remained `running` indefinitely; its child run remained
`queued` with a single `admission → queued` event; the scenario timed out after 180
seconds.

**Root cause.** `RunNodeExecutor.submit` (`src/hiveplane/pipelines/executor.py`) created
each node's child run and returned its identifier but never started it. A run is dispatched
to its adapter only on the `queued → running` transition
(`src/hiveplane/execution/service.py`). Nothing performed that transition for pipeline
children: the Docker profile contains no worker daemon to lease queued runs, and the
pipeline driver (`engine.advance_pending`) reconciles only nodes that are already
`running`. The parent therefore waited indefinitely on a child that nothing would launch.

**Resolution.** The queued child is now started immediately, mirroring the certification
executor (`src/hiveplane/certification/executor.py`). The `_RunService` protocol gained a
`start` method, and `RunNodeExecutor.submit` now performs:

```python
run = self._runs.submit(...)
if run.state is RunState.QUEUED:
    self._runs.start(run.id, actor="pipeline", ctx=ctx)
return run.id
```

**Affected files.** `src/hiveplane/pipelines/executor.py`.

**Regression test.** `tests/test_pipelines_engine.py::test_pipeline_starts_its_child_runs`
asserts the child transitions from `queued` to `running`; it failed before the change and
passes after.

**Verification.** Unit test fails before and passes after; the live `s7` scenario completes
end-to-end in approximately fourteen seconds; all six L6 autonomy scenarios pass; the full
pipeline unit suite passes (67 tests).

**Lesson.** Execution is governed by the `queued → running` transition. Any component that
produces child runs — pipelines, triggers, or certification — must either start them or
rely on a worker that leases them. Pipelines relied on neither.

### 3.4 D-4 — Successful fan-out delivery invisible to the API (product defect)

**Symptom.** `GET /delivery/audit` returned an empty result for the completed run within
the 30-second window.

**Root cause.** The fan-out delivered successfully — the webhook sink logged the
`run.completed` payloads — and the attempts were recorded in the execution store's
`fan_out_deliveries` table with status `delivered`. However, no API surface exposed those
records. The scenario polled `GET /delivery/audit`, which reads the separate M51
`delivery_attempts` store served by `DeliveryService`; the two subsystems share the concept
of "delivery" but not their storage. A successful fan-out was therefore unauditable
through the control plane.

**Resolution.** A dedicated endpoint was added to expose a run's fan-out attempts:

```python
@router.get("/runs/{run_id}/deliveries", response_model=list[DeliveryRecord])
def list_deliveries(run_id: str, service: ServiceDep, ctx: TenantDep):
    return service.deliveries(run_id, ctx=ctx)
```

The scenario was updated to poll this endpoint and to assert a `delivered` attempt.

**Affected files.** `src/hiveplane/api/runs.py`, `tests/docker/test_v02_delivery.py`.

**Regression test.** `tests/test_api_runs.py::test_run_deliveries_lists_fan_out_attempts`
returned 404 before the change and 200 after.

**Verification.** Unit test 404 → 200; the live delivery suite passes 3/3; the attempt is
recorded `delivered`.

**Lesson.** When two subsystems share a concept, an audit surface must reference the store
that actually receives the data. The scenario's unused `_WEBHOOK_SINK` constant was an
indication that it was checking the wrong surface.

### 3.5 Summary of changes

| File | Change |
|------|--------|
| `src/hiveplane/pipelines/executor.py` | Start a queued child run in `RunNodeExecutor.submit`; add `start` to `_RunService` |
| `src/hiveplane/api/runs.py` | Add `GET /runs/{run_id}/deliveries` |
| `tests/test_pipelines_engine.py` | Add `test_pipeline_starts_its_child_runs` |
| `tests/test_api_runs.py` | Add `test_run_deliveries_lists_fan_out_attempts` |
| `tests/docker/test_v02_delivery.py` | Poll the run-deliveries endpoint; remove unused constant |
| `scripts/docker_report.py` | Stop emitting the redundant per-run `report.md` copy |
| `.gitignore` | Ignore per-run `field_test/**/docker/report.md` |
| `field_test/v0.1.0/docker/report.md` | Removed (byte-identical duplicate of the canonical report) |

## 4. Observations and Lessons Learned

### 4.1 Findings from the original run

1. **Model selection materially affects the suite.** The stack binds to
   `Qwen3-4B-Instruct-2507-4bit` (`omlx/qwen3-4b-instruct-2507/4bit`). Non-Instruct
   Qwen3.5/8B models emitted reasoning prose instead of the strict JSON contract and were
   appreciably slower.
2. **Small local models require an explicit rubric.** The repo-agent prompt was hardened so
   that tests-only changes remain low risk, while auth/token changes, destructive
   migrations, and prompt-injection text in a PR body are classified high risk.
3. **Concurrent `run_events` appends were not race-safe** under Postgres `READ COMMITTED`.
   The store now locks the parent run row (`FOR UPDATE`) before assigning the next sequence
   and persists the assigned sequence in the event payload.
4. **Startup recovery must tolerate orphaned runs.** Recovery now skips non-terminal runs
   whose workload has been deleted rather than crashing the API during lifespan startup.
5. **Runner diagnosability was insufficient.** The runner now resets volumes at start and
   records a PID, heartbeat, current phase, and abort marker so interrupted runs are
   diagnosable instead of leaving partial evidence silently.
6. **The control loop depends on certification lifecycle semantics.** Staging must produce
   `provisional` before production can produce `certified`; the field-test profile lowers
   `min_production_runs_survived` to zero so the loop can be demonstrated in a single stack
   run without weakening the benchmark thresholds.
7. **Canonical model identity must be used consistently.** The UI seeded run must use the
   same canonical model identity as the certification path; hardcoding `gpt-4o` correctly
   triggered the model-swap gate once repo-agent was certified against the local model.

### 4.2 Findings from this remediation

1. **Rebuild before trusting a report.** Two of the four failures were environmental. A
   report is only valid for the image it ran against; `docker compose build <service>` is a
   prerequisite to re-running.
2. **Child runs require an explicit start.** In this profile, runs are admitted `queued`
   and execute only on the `queued → running` transition or a worker lease. As there is no
   worker in the Docker profile, any producer of child runs must start them. Certification
   already did; pipelines did not. Triggers also only submit and warrant review if a
   trigger scenario is expected to complete end-to-end without a worker.
3. **Defence tests must clear the tool boundary first.** Output injection scanning occurs
   after policy, egress, and tool-allow checks; a denied tool never reaches the scanner, so
   an injection test that uses a disallowed tool yields a false negative.
4. **"Delivery" spans two subsystems with distinct stores.** Execution fan-out
   (`FanOutService` → `fan_out_deliveries`) is separate from the M51 notification service
   (`DeliveryService` → `delivery_attempts`). `GET /delivery/audit` reports only the latter.
   Audit and UI surfaces must state which one they represent.
5. **Trace data flow at each boundary.** Both product defects were located by following a
   value across a seam — a child that never transitions and an attempt written to a table
   the endpoint does not read — rather than by reading symptoms. Boundary evidence (the run
   event log, the webhook-sink log, and both tables) localised each fault immediately.
6. **Test-driven diagnosis localised both product defects below the container layer.** Each
   fix began with a unit test that failed for the exact reason (a `queued` child; a `404`
   endpoint) before production code was modified, so the Docker re-run served as
   confirmation rather than discovery.

## 5. Conclusions and Recommendations

1. **The two genuine defects were integration seams, not logic errors.** Pipelines produced
   child runs without starting them, and fan-out recorded deliveries without exposing them.
   Neither is visible to a unit test that stops at the seam; only the end-to-end Docker
   scenarios detected them.
2. **The field-test suite delivered its intended value.** It raised one stale-image false
   alarm and two real product gaps that would otherwise have reached release.
3. **Maintain a clear distinction between the two audit surfaces.** The run fan-out audit
   (`GET /runs/{id}/deliveries`) and the M51 notification audit (`GET /delivery/audit`)
   should be kept distinct in documentation and in the operator UI.
4. **Recommended follow-up.** Determine whether triggered workload runs should auto-start in
   the workerless Docker profile, consistent with the resolution of D-3. This is outside the
   scope of the present remediation.

---

## Appendix A — Final Results

| ID | Layer | Scenario | Original | Final | Classification |
|----|-------|----------|----------|-------|----------------|
| D-1 | L4 | `test_s4_drift_probe_and_quarantine` | failure (`403`) | pass | stale image |
| D-2 | L5 | `test_s11_injection_output_is_blocked_and_recorded` | failure | pass | test-fixture bug |
| D-3 | L6 | `test_s7_pipeline_runs_end_to_end` | failure (stuck) | pass | product defect (fixed) |
| D-4 | L9 | `test_fan_out_delivers_to_the_webhook_sink` | failure | pass | product defect (fixed) |

Aggregate: **67 / 67 passing** (63 scenarios were already green in the original run; the
four above were re-verified green after remediation).

Re-verification command against the live stack:

```bash
# Rebuild the API image so it matches the source, then:
.venv/bin/python -m pytest \
  "tests/docker/test_v02_immune.py::test_s4_drift_probe_and_quarantine" \
  "tests/docker/test_v02_defense.py::test_s11_injection_output_is_blocked_and_recorded" \
  "tests/docker/test_v02_autonomy.py::test_s7_pipeline_runs_end_to_end" \
  tests/docker/test_v02_delivery.py -m docker -p no:randomly -ra
# -> 6 passed
```

## Appendix B — Environment

| Key | Value |
|-----|-------|
| `docker` | `Docker version 29.5.2, build 79eb04c7d8` |
| `git_sha` (original run) | `bc1e82b42b747f1207426379e37e90af5e344b7c` |
| `git_sha` (re-verification) | `22b9ba1` + remediation changes |
| `model` | `Qwen3-4B-Instruct-2507-4bit` (`omlx/qwen3-4b-instruct-2507/4bit`) |
| `run_id` (original) | `20261001T013201Z` |
| `version` | `v0.2.0` |
| host ports | api `8100`, ui `3001`, webhook-sink `58081` |

## Appendix C — Original Layer Results (generated)

The tables in this appendix are the original generated run (pre-rebuild). The four
failures are annotated to their final state; the analysis is in Section 3.

### C.1 Layer summary (original)

| Layer | Name | Tests | Passed | Failed | Errored | Skipped | Duration (s) |
|-------|------|-------|--------|--------|---------|---------|--------------|
| L0 | Image build | 2 | 2 | 0 | 0 | 0 | 2.48 |
| L1 | Stack health | 3 | 3 | 0 | 0 | 0 | 15.11 |
| L2 | API contract | 15 | 15 | 0 | 0 | 0 | 0.80 |
| L3 | UI | 7 | 7 | 0 | 0 | 0 | 1.07 |
| L4 | Control loop & immune | 4 | 3 | 1 | 0 | 0 | 0.04 |
| L5 | Defense & governance | 5 | 4 | 1 | 0 | 0 | 0.48 |
| L6 | Autonomy | 6 | 5 | 1 | 0 | 0 | 1.05 |
| L7 | Fleet scale | 6 | 6 | 0 | 0 | 0 | 0.08 |
| L8 | Secrets, RBAC & tenancy | 4 | 4 | 0 | 0 | 0 | 0.65 |
| L9 | Cost, reporting & portability | 12 | 11 | 1 | 0 | 0 | 1.14 |
| L10 | Helm / k3d | 3 | 3 | 0 | 0 | 0 | 0.06 |
| L11 | Load test | 1 | 1 | 0 | 0 | 0 | 0.63 |
| L12 | Durability | 1 | 1 | 0 | 0 | 0 | 4.95 |
| L13 | LLM matrix | 2 | 2 | 0 | 0 | 0 | 0.68 |

### C.2 Layer detail (original run; four rows show final state)

#### L0 — Image build

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_container_fixtures::test_image_ships_tool_and_replay_fixtures` | passed | 1.53 |
| `test_container_fixtures::test_image_imports_every_entrypoint_and_extra` | passed | 0.95 |

#### L1 — Stack health

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_stack_health::test_healthz_is_ok` | passed | 0.01 |
| `test_stack_health::test_readyz_reports_ready` | passed | 0.06 |
| `test_stack_health::test_observability_services_are_healthy` | passed | 15.04 |

#### L2 — API contract

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_api_contract::test_manifest_schema` | passed | 0.03 |
| `test_api_contract::test_health_endpoints` | passed | 0.01 |
| `test_api_contract::test_registry_crud_and_errors` | passed | 0.05 |
| `test_api_contract::test_unknown_tool_is_rejected` | passed | 0.00 |
| `test_api_contract::test_run_lifecycle_and_story` | passed | 0.07 |
| `test_api_contract::test_unknown_run_is_404` | passed | 0.00 |
| `test_api_contract::test_uncertified_run_is_refused_production` | passed | 0.00 |
| `test_api_contract::test_policy_approvals_certifications_and_spend_read` | passed | 0.03 |
| `test_v02_api::test_v2_version_and_pagination` | passed | 0.01 |
| `test_v02_api::test_request_id_header_is_returned` | passed | 0.00 |
| `test_v02_api::test_global_search_and_ask` | passed | 0.02 |
| `test_v02_api::test_incident_mode_pause_and_resume` | passed | 0.02 |
| `test_v02_api::test_agent_as_service_endpoint_serves_a_run` | passed | 0.47 |
| `test_v02_api::test_fleet_event_subscriptions` | passed | 0.01 |
| `test_v02_api::test_python_sdk_round_trips` | passed | 0.08 |

#### L3 — UI

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_v02_ui::test_ui_v2_fleet_and_run_detail[chromium]` | passed | 0.47 |
| `test_v02_ui::test_ui_v2_approvals_and_certifications[chromium]` | passed | 0.14 |
| `test_v02_ui::test_ui_v2_cost_roi_health[chromium]` | passed | 0.05 |
| `test_v02_ui::test_ui_v2_triggers_queue_search[chromium]` | passed | 0.12 |
| `test_v02_ui::test_ui_v2_diff_onboarding_replay[chromium]` | passed | 0.10 |
| `test_v02_ui::test_ui_v2_incident_banner[chromium]` | passed | 0.13 |
| `test_v02_ui::test_ui_v2_login_renders[chromium]` | passed | 0.06 |

_All three previously-failing UI scenarios pass (strict-mode heading locator fixes)._

#### L4 — Control loop & immune

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_v02_immune::test_s1_certification_is_signed_and_uncertified_refused` | passed | 0.02 |
| `test_v02_immune::test_s2_promotion_requires_a_certified_workload_else_refused` | passed | 0.02 |
| `test_v02_immune::test_s3_regression_diff_compares_two_certifications` | passed | 0.00 |
| `test_v02_immune::test_s4_drift_probe_and_quarantine` | **passed** (D-1) | — |

#### L5 — Defense & governance

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_v02_defense::test_s11_injection_output_is_blocked_and_recorded` | **passed** (D-2) | — |
| `test_v02_defense::test_s12_context_budget_guard_is_operable` | passed | 0.02 |
| `test_v02_defense::test_s13_spend_velocity_guard_surface` | passed | 0.01 |
| `test_v02_defense::test_s14_circuit_breaker_and_kill_switch` | passed | 0.00 |
| `test_v02_defense::test_s15_egress_to_disallowed_host_is_denied` | passed | 0.00 |

#### L6 — Autonomy

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_v02_autonomy::test_s6_triggers_from_three_sources_and_dedup` | passed | 0.47 |
| `test_v02_autonomy::test_s7_pipeline_runs_end_to_end` | **passed** (D-3) | — |
| `test_v02_autonomy::test_s8_canary_routes_and_promotes` | passed | 0.02 |
| `test_v02_autonomy::test_s9_shadow_run_reports_outcome_diff` | passed | 0.09 |
| `test_v02_autonomy::test_s10_agent_as_tool_propagates_budget_and_cert` | passed | 0.44 |
| `test_v02_autonomy::test_s20_dlq_replay_is_idempotent` | passed | 0.01 |

#### L7 — Fleet scale

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_v02_fleet::test_s18_worker_lifecycle_and_fleet_view` | passed | 0.02 |
| `test_v02_fleet::test_s18_kill_worker_drill_reassigns` | passed | 0.04 |
| `test_v02_fleet::test_s18_worker_without_token_is_refused` | passed | 0.00 |
| `test_v02_fleet::test_s19_queue_and_preemption_surface` | passed | 0.01 |
| `test_v02_fleet::test_ha_leader_is_authoritative` | passed | 0.00 |
| `test_v02_fleet::test_chaos_drills_are_listed` | passed | 0.00 |

#### L8 — Secrets, RBAC & tenancy

Executed in the dedicated `--auth` pass (`scripts/docker-test-v02.sh --auth`), against an
auth-enabled plane with the bootstrap admin key (D-10) and rate limiting on (D-11). **4/4 passed**
(gate 6, 13, 34):

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_v02_secrets_tenancy::test_s16_secret_round_trip_and_absence_from_run_surfaces` | passed | 0.30 |
| `test_v02_secrets_tenancy::test_s17_viewer_is_denied_privileged_actions` | passed | 0.10 |
| `test_v02_secrets_tenancy::test_s17_per_tenant_isolation` | passed | 0.05 |
| `test_v02_secrets_tenancy::test_s17_over_limit_tenant_gets_429` | passed | 0.20 |

Notes: the pass required three fixes to record cleanly — the rate-limit env names had to match
`ApiSettings` (`HIVEPLANE_API__RATE_LIMIT_*`, D-11), the tests needed the bootstrap admin key
(D-10) and a **system-tenant** bootstrap key (`HIVEPLANE_AUTH__SYSTEM_KEY`) to create tenants
whose keys live outside `default`, and the repo's per-test settings isolation had to be disabled
for the live-stack container suite so `HIVEPLANE_*` env remains visible.

#### L9 — Cost, reporting & portability

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_v02_cost_reporting::test_s21_showback_and_roi` | passed | 0.90 |
| `test_v02_cost_reporting::test_s22_result_cache_hit_and_invalidation` | passed | 0.02 |
| `test_v02_cost_reporting::test_s23_gitops_reconcile_plan_and_apply` | passed | 0.00 |
| `test_v02_cost_reporting::test_s24_synthetic_probes` | passed | 0.01 |
| `test_v02_cost_reporting::test_s25_public_verify_kill_switch_and_provenance` | passed | 0.03 |
| `test_v02_cost_reporting::test_replay_fork_diff_are_side_effect_free` | passed | 0.05 |
| `test_v02_cost_reporting::test_artifacts_retention_and_digest` | passed | 0.03 |
| `test_v02_delivery::test_delivery_audit_is_readable` | passed | 0.01 |
| `test_v02_delivery::test_interactive_approval_resolve_rejects_a_forged_token` | passed | 0.01 |
| `test_v02_delivery::test_fan_out_delivers_to_the_webhook_sink` | **passed** (D-4) | — |
| `test_v02_mcp::test_mcp_server_onboard_discover_and_refresh` | passed | 0.01 |
| `test_v02_mcp::test_mcp_onboard_and_remove_tool` | passed | 0.03 |

#### L10 — Helm / k3d

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_v02_helm::test_chart_lints` | passed | 0.04 |
| `test_v02_helm::test_chart_renders_the_full_stack` | passed | 0.03 |
| `test_v02_helm::test_chart_values_schema_is_present` | passed | 0.00 |

#### L11 — Load test

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_v02_load::test_sustain_50_concurrent_runs` | passed | 0.63 |

#### L12 — Durability

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_durability::test_paused_run_survives_restart_and_resumes` | passed | 4.95 |

#### L13 — LLM matrix

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_llm_matrix::test_local_llm_returns_a_completion` | passed | 0.39 |
| `test_llm_matrix::test_local_llm_reports_usage` | passed | 0.29 |

### C.3 Original failure output (retained for the record)

```
L4 test_v02_immune::test_s4_drift_probe_and_quarantine
AssertionError: assert 403 == 201

L5 test_v02_defense::test_s11_injection_output_is_blocked_and_recorded
AssertionError: body["outcome"] != "blocked_injection"

L6 test_v02_autonomy::test_s7_pipeline_runs_end_to_end
AssertionError: {'pipeline_run_id': 'prun-47745f26fa2f', ..., 'state': 'running'} assert 200 == 201
  (re-verified root cause: child run stuck `queued`, node `running`, 180s timeout)

L9 test_v02_delivery::test_fan_out_delivers_to_the_webhook_sink
AssertionError: no delivery attempt recorded within 30s
```

## Appendix D — Artifacts

| Artifact | Bytes |
|----------|-------|
| `README.md` | 2017 |
| `compose-ps.txt` | 2281 |
| `compose-reset.log` | 2126 |
| `compose-up.log` | 3012 |
| `environment.json` | 211 |
| `junit.xml` | 41647 |
| `playwright-install.log` | 0 |
| `preflight.log` | 786 |
| `pytest.log` | 42262 |
| `run.log` | 329 |
| `seed-tenants.log` | 751 |
| `seed-tools.log` | 1018 |
| `wait-ready.log` | 18 |
| `wait-ui.log` | 15 |
