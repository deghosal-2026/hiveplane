# HivePlane v0.1.0 — Docker Test Report

> Generated 2026-09-24T23:56:15.148607+00:00 by `scripts/docker-test.sh`.
> **Overall: PASS** — 25 passed, 0 failed, 0 errored, 0 skipped (of 25).

## Issues / Learnings

- LLM selection matters: the docker stack now uses `Qwen3-4B-Instruct-2507-4bit` via canonical identity `omlx/qwen3-4b-instruct-2507/4bit` because the non-Instruct Qwen3.5/8B models emitted reasoning prose instead of the strict JSON contract and were materially slower.
- Small local models need an explicit rubric: the repo-agent prompt was hardened so tests-only changes stay low risk, auth/token changes stay high risk, destructive migrations stay high risk, and prompt-injection text in a PR body is classified as high risk.
- Concurrent `run_events` appends were not race-safe under Postgres READ COMMITTED. The store now locks the parent run row (`FOR UPDATE`) before assigning the next sequence and persists the assigned sequence in the event payload.
- Startup recovery must tolerate orphaned runs. Recovery now skips non-terminal runs whose workload has been deleted instead of crashing the API during lifespan startup.
- The docker runner now resets volumes at start, records a PID, heartbeat, current phase, and abort marker so interrupted runs are diagnosable instead of silently leaving partial evidence.
- The control-loop docker test depends on certification lifecycle semantics: staging must produce `provisional` before production can produce `certified`, and the field-test profile lowers `min_production_runs_survived` to `0` so the loop can be demonstrated in one stack run without weakening the benchmark thresholds themselves.
- The UI seeded run must use the same canonical model identity as the certification path; hardcoding `gpt-4o` correctly triggered the model-swap gate once repo-agent was certified against the local model.

## Environment

| Key | Value |
|-----|-------|
| `api_url` | `http://localhost:8100` |
| `docker_client` | `Docker version 29.5.2, build 79eb04c7d8` |
| `docker_compose` | `5.5.1` |
| `git_branch` | `main` |
| `git_dirty` | `True` |
| `git_sha` | `fdb0d46dab906636087b345579a0925ee8426931` |
| `llm_base_url` | `http://127.0.0.1:8000/v1` |
| `llm_model` | `omlx/qwen3-4b-instruct-2507/4bit` |
| `llm_served_model` | `Qwen3-4B-Instruct-2507-4bit` |
| `log_dir` | `/Users/deghosal/Desktop/code/github/hiveplane/field_test/v0.1.0/docker` |
| `platform` | `macOS-26.6.2-arm64-arm-64bit` |
| `profile` | `local+test` |
| `provider` | `local` |
| `python` | `3.12.13` |
| `run_id` | `20260924T235452Z` |
| `timestamp_utc` | `2026-09-24T23:54:53.131884+00:00` |

## Layer summary

| Layer | Name | Tests | Passed | Failed | Errored | Skipped | Duration (s) |
|-------|------|-------|--------|--------|---------|---------|--------------|
| L0 | Image build | 2 | 2 | 0 | 0 | 0 | 2.18 |
| L1 | Stack health | 3 | 3 | 0 | 0 | 0 | 15.06 |
| L2 | API contract | 8 | 8 | 0 | 0 | 0 | 0.13 |
| L3 | UI | 5 | 5 | 0 | 0 | 0 | 0.66 |
| L4 | Control loop | 1 | 1 | 0 | 0 | 0 | 32.71 |
| L5 | Governance | 3 | 3 | 0 | 0 | 0 | 0.05 |
| L6 | Durability | 1 | 1 | 0 | 0 | 0 | 4.87 |
| L7 | LLM matrix | 2 | 2 | 0 | 0 | 0 | 0.59 |

## Layer detail

### L0 — Image build

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_container_fixtures::test_image_ships_tool_and_replay_fixtures` | passed | 1.37 |
| `test_container_fixtures::test_image_imports_every_entrypoint_and_extra` | passed | 0.81 |

### L1 — Stack health

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_stack_health::test_healthz_is_ok` | passed | 0.01 |
| `test_stack_health::test_readyz_reports_ready` | passed | 0.01 |
| `test_stack_health::test_observability_services_are_healthy` | passed | 15.04 |

### L2 — API contract

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_api_contract::test_manifest_schema` | passed | 0.03 |
| `test_api_contract::test_health_endpoints` | passed | 0.01 |
| `test_api_contract::test_registry_crud_and_errors` | passed | 0.04 |
| `test_api_contract::test_unknown_tool_is_rejected` | passed | 0.00 |
| `test_api_contract::test_run_lifecycle_and_story` | passed | 0.04 |
| `test_api_contract::test_unknown_run_is_404` | passed | 0.00 |
| `test_api_contract::test_uncertified_run_is_refused_production` | passed | 0.00 |
| `test_api_contract::test_policy_approvals_certifications_and_spend_read` | passed | 0.01 |

### L3 — UI

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_ui::test_fleet_screen[chromium]` | passed | 0.32 |
| `test_ui::test_run_detail_screen[chromium]` | passed | 0.12 |
| `test_ui::test_approvals_screen[chromium]` | passed | 0.07 |
| `test_ui::test_certifications_screen[chromium]` | passed | 0.07 |
| `test_ui::test_spend_screen[chromium]` | passed | 0.09 |

### L4 — Control loop

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_control_loop::test_register_certify_run_and_deliver` | passed | 32.71 |

### L5 — Governance

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_governance::test_uncertified_destructive_workload_refused_production` | passed | 0.02 |
| `test_governance::test_uncertified_destructive_workload_admitted_to_sandbox` | passed | 0.02 |
| `test_governance::test_spend_surface_reports_budgets` | passed | 0.01 |

### L6 — Durability

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_durability::test_paused_run_survives_restart_and_resumes` | passed | 4.87 |

### L7 — LLM matrix

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_llm_matrix::test_local_llm_returns_a_completion` | passed | 0.25 |
| `test_llm_matrix::test_local_llm_reports_usage` | passed | 0.34 |


## Logs

| Artifact | Bytes |
|----------|-------|
| `README.md` | 1393 |
| `compose-ps.txt` | 2281 |
| `compose-reset.log` | 2143 |
| `compose-up.log` | 5632 |
| `compose.log` | 239100 |
| `environment.json` | 677 |
| `heartbeat.txt` | 32 |
| `junit.xml` | 2965 |
| `playwright-install.log` | 0 |
| `preflight.log` | 0 |
| `pytest.log` | 1157 |
| `report.md` | 8912 |
| `run.log` | 1217 |
| `seed-tools.log` | 589 |
| `status.txt` | 60 |
| `teardown.log` | 2126 |
| `wait-ready.log` | 0 |
