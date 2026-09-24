# HivePlane v0.1.0 — Docker Test Report

> Generated 2026-09-24T02:08:47.333449+00:00 by `scripts/docker-test.sh`.
> **Overall: FAIL** — 23 passed, 1 failed, 1 errored, 0 skipped (of 25).

## Environment

| Key | Value |
|-----|-------|
| `api_url` | `http://localhost:8100` |
| `docker_client` | `Docker version 29.5.2, build 79eb04c7d8` |
| `docker_compose` | `5.5.1` |
| `git_branch` | `main` |
| `git_dirty` | `True` |
| `git_sha` | `c6629a7b4bba5e5f8498bad94cbcd6087e082134` |
| `llm_base_url` | `http://127.0.0.1:8000/v1` |
| `llm_model` | `Llama-3.2-3B-Instruct-4bit` |
| `log_dir` | `/Users/deghosal/desktop/code/github/hiveplane/field_test/v0.1.0/docker` |
| `platform` | `macOS-26.6.2-arm64-arm-64bit` |
| `profile` | `local+test` |
| `provider` | `local` |
| `python` | `3.12.13` |
| `run_id` | `20260924T020320Z` |
| `timestamp_utc` | `2026-09-24T02:03:23.058536+00:00` |

## Layer summary

| Layer | Name | Tests | Passed | Failed | Errored | Skipped | Duration (s) |
|-------|------|-------|--------|--------|---------|---------|--------------|
| L0 | Image build | 2 | 2 | 0 | 0 | 0 | 2.45 |
| L1 | Stack health | 3 | 3 | 0 | 0 | 0 | 15.05 |
| L2 | API contract | 8 | 8 | 0 | 0 | 0 | 0.13 |
| L3 | UI | 5 | 4 | 0 | 1 | 0 | 0.80 |
| L4 | Control loop | 1 | 0 | 1 | 0 | 0 | 231.85 |
| L5 | Governance | 3 | 3 | 0 | 0 | 0 | 0.05 |
| L6 | Durability | 1 | 1 | 0 | 0 | 0 | 29.22 |
| L7 | LLM matrix | 2 | 2 | 0 | 0 | 0 | 0.59 |

## Layer detail

### L0 — Image build

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_container_fixtures::test_image_ships_tool_and_replay_fixtures` | passed | 1.53 |
| `test_container_fixtures::test_image_imports_every_entrypoint_and_extra` | passed | 0.92 |

### L1 — Stack health

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_stack_health::test_healthz_is_ok` | passed | 0.01 |
| `test_stack_health::test_readyz_reports_ready` | passed | 0.01 |
| `test_stack_health::test_observability_services_are_healthy` | passed | 15.04 |

### L2 — API contract

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_api_contract::test_manifest_schema` | passed | 0.02 |
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
| `test_ui::test_fleet_screen[chromium]` | passed | 0.55 |
| `test_ui::test_run_detail_screen[chromium]` | error | 0.02 |
| `test_ui::test_approvals_screen[chromium]` | passed | 0.08 |
| `test_ui::test_certifications_screen[chromium]` | passed | 0.07 |
| `test_ui::test_spend_screen[chromium]` | passed | 0.08 |

<details><summary>test_ui::test_run_detail_screen[chromium] — error</summary>

```
failed on setup with "AssertionError: {'detail': "attestation 'att-b7d392e5cb9d' failed signature verification"} assert 422 == 201"
```

</details>

### L4 — Control loop

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_control_loop::test_register_certify_run_and_deliver` | failure | 231.85 |

<details><summary>test_control_loop::test_register_certify_run_and_deliver — failure</summary>

```
AssertionError: {'record_id': 'att-b7d392e5cb9d', 'certification': {'certification_id': 'cert-d269c655c2d1', 'workload_id': 'repo-agen...run_id': 'br-fc1c031ba0da', 'workload_id': 'repo-agent', 'manifest_version': 1, 'corpus_id': 'repo-agent-corpus', ...}} assert 'uncertified' == 'certified' - certified + uncertified ? ++
```

</details>

### L5 — Governance

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_governance::test_uncertified_destructive_workload_refused_production` | passed | 0.02 |
| `test_governance::test_uncertified_destructive_workload_admitted_to_sandbox` | passed | 0.02 |
| `test_governance::test_spend_surface_reports_budgets` | passed | 0.01 |

### L6 — Durability

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_durability::test_paused_run_survives_restart_and_resumes` | passed | 29.22 |

### L7 — LLM matrix

| Test | Status | Duration (s) |
|------|--------|--------------|
| `test_llm_matrix::test_local_llm_returns_a_completion` | passed | 0.31 |
| `test_llm_matrix::test_local_llm_reports_usage` | passed | 0.28 |


## Logs

| Artifact | Bytes |
|----------|-------|
| `README.md` | 1393 |
| `compose-ps.txt` | 2201 |
| `compose-up.log` | 30706 |
| `compose.log` | 56627 |
| `environment.json` | 618 |
| `junit.xml` | 5658 |
| `playwright-install.log` | 0 |
| `preflight.log` | 0 |
| `pytest.log` | 3822 |
| `run.log` | 498 |
| `seed-tools.log` | 590 |
| `teardown.log` | 2126 |
| `wait-ready.log` | 0 |
