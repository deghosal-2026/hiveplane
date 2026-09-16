# v0.1.0 Code Review Log

**Date:** 2026-09-15
**Scope:** Parts 1–7 (M1–M15, issues #2–#41) — the work closed to date. Code audited against
`docs/prd/` (features, security baseline), `docs/design/` (per-part designs), the WBS exit
criteria, and closed GitHub issues.
**Outcome:** 20 findings opened as `[0.1.0 CodeReview]` issues (#62–#81): 2 high, 12 medium, 6 low.

## Overall assessment

- **Certification (Part 3, M5–M7):** structurally sound (deterministic runner, signed
  attestations, verification on read, model binding), but has real security/logic gaps: the
  default API path rubber-stamps certification (#63), the corpus reference is path-traversable
  (#62), the survival gate is client-declared (#64), quarantine is unrecoverable (#68), and
  record history is overwritten (#69).
- **Registry (Part 2):** solid. Admission enforcement, append-only versions, and tamper checks
  behave per design.
- **Run lifecycle (Part 4):** state machine and intervention are correct, but nothing in the
  shipped app drives `queued → running` (#72), and `record_usage` is unsafe after terminal
  states (#74).
- **Policy/Budget/Sandbox/Shaping (Parts 5–7):** the engines are well-built libraries, but the
  tool-call boundary that would put them on a live path does not exist yet — policy/shaping/
  egress enforcement is inert until Part 8 (M16–M17) lands (#73). Budget trusts caller cost
  when no model identity is present (#65). Sandbox CPU caps are mis-mapped and egress is
  bookkeeping-only (#66, #67).
- **Test/quality gates:** 431 tests, 97% coverage, ruff and mypy strict clean. Test coverage is
  strong at the unit level; the gaps are end-to-end (no adapter-driven run exercises the
  policy/shaping/egress/budget-during-execution paths).

## Findings

| # | Issue | Severity | Area |
|---|-------|----------|------|
| [#62](https://github.com/deghosal-2026/hiveplane/issues/62) | `POST /certifications` corpus reference is path-traversable and allows cross-workload corpus reuse | high | certification / security |
| [#63](https://github.com/deghosal-2026/hiveplane/issues/63) | Default app coordinator uses `ReferenceExecutor`, so the shipped API rubber-stamps certification | high | certification / security |
| [#64](https://github.com/deghosal-2026/hiveplane/issues/64) | Production "runs survived" requirement is client-declared, defeating the survival gate | medium | certification / security |
| [#65](https://github.com/deghosal-2026/hiveplane/issues/65) | Client-reported cost is trusted when no model identity is present (budget bypass) | medium | budget / security |
| [#66](https://github.com/deghosal-2026/hiveplane/issues/66) | `ProcessSandboxManager` maps `cpu_cores` onto RLIMIT_CPU seconds and swallows rlimit failures | medium | sandbox / security |
| [#67](https://github.com/deghosal-2026/hiveplane/issues/67) | `EgressGuard` is not wired into any execution path — "restricted egress" is bookkeeping only | medium | sandbox / security |
| [#68](https://github.com/deghosal-2026/hiveplane/issues/68) | `QUARANTINED` is terminal — `RECOVER` is never emitted; a quarantined workload can never be re-certified | medium | certification / logic |
| [#69](https://github.com/deghosal-2026/hiveplane/issues/69) | Certification record store overwrites previous records on re-certification (audit trail lost) | medium | certification / logic |
| [#70](https://github.com/deghosal-2026/hiveplane/issues/70) | Per-workload manifest thresholds are ignored by the engine | medium | certification / logic |
| [#71](https://github.com/deghosal-2026/hiveplane/issues/71) | Corpus task `timeout_seconds` and `allow_network` are declared but never enforced | medium | certification / logic |
| [#72](https://github.com/deghosal-2026/hiveplane/issues/72) | No path drives `QUEUED → RUNNING` in the shipped app; submitted runs are stuck (cannot start or stop) | medium | run lifecycle / integration |
| [#73](https://github.com/deghosal-2026/hiveplane/issues/73) | Tool-level policy, approvals, shaping, and egress are not on any live execution path | medium | policy/shaping / integration |
| [#74](https://github.com/deghosal-2026/hiveplane/issues/74) | `RunService.record_usage` is not safe after terminal states and persists usage before pricing | medium | run lifecycle / budget |
| [#75](https://github.com/deghosal-2026/hiveplane/issues/75) | Model identity has no canonical convention, causing false model-swap refusals | low | certification / logic |
| [#76](https://github.com/deghosal-2026/hiveplane/issues/76) | Regression diff ignores tasks added/removed between corpus versions | low | certification / logic |
| [#77](https://github.com/deghosal-2026/hiveplane/issues/77) | Attestation chain (`previous_attestation_id`) is never set on the live path | low | certification / logic |
| [#78](https://github.com/deghosal-2026/hiveplane/issues/78) | Policy engine deviates from design: blast-radius high denies even with certification; restricted read-only doesn't escalate | low | policy / design |
| [#79](https://github.com/deghosal-2026/hiveplane/issues/79) | Approval approve/deny is not transactional: decision persists before the run transition | low | policy / logic |
| [#80](https://github.com/deghosal-2026/hiveplane/issues/80) | `JsonFileRunStore` writes are non-atomic, and the default app uses `InMemoryRunStore` | low | run lifecycle / reliability |
| [#81](https://github.com/deghosal-2026/hiveplane/issues/81) | `benchmark_version` bound in attestations is a static constant, not a version of the benchmark logic | low | certification |

## PRD feature coverage (v0.1.0 scope)

| PRD feature (v0.1.0) | Status | Notes |
|----------------------|--------|-------|
| Registry, manifest validation, versioning, `--dry-run` | ✅ Shipped | M3–M4; no findings |
| Benchmark runner + certification engine + signed attestation | ⚠️ Shipped with gaps | #62, #63, #68, #69, #70, #71 |
| Certification status enforced at admission | ✅ Shipped | T8 met; model binding in place (#75 convention gap) |
| Task submission, state machine, pause/resume/cancel, durable state | ⚠️ Partial | State machine correct; #72 (no start path), #74, #80 (durability not the app default) |
| Budget enforcement per run/day | ⚠️ Shipped with gap | #65 (unpriced spend hole), #74 (late usage 500) |
| Deny-by-default policy + approvals + audit | ⚠️ Library only | #73 — not on a live execution path until M16–M17 |
| Execution isolation + resource/output caps | ⚠️ Shipped with gaps | #66 (CPU cap semantics, silent failures), #67 (egress not enforced) |
| Tool-output shaping | ⚠️ Library only | #73 — `ShapingPipeline`/`InjectionScanner` not wired into app |
| Raw-worker + LangGraph adapters, conformance suite | ❌ Not started | Part 8 (M16–M17) — blocks #72, #73 and real benchmark execution |
| OTel traces/metrics/logs, fleet metrics | ❌ Not started | Part 10 (M19–M20) |
| CLI + minimal UI | ⚠️ Partial | `register/validate/certify/certs` exist; `submit/runs/approvals/triggers/tools` + UI + `init` pending (M21–M22) |
| `hiveplane init` + seeded demo + Docker Compose | ⚠️ Partial | Compose skeleton (M1); `init` pending (M21); demo corpus seeded |
| Trigger rules / scheduled modes | ❌ Out of v0.1.0 per PRD | v0.2.0 (models + registry storage exist) |
| Drift detector + auto-quarantine | ❌ Out of v0.1.0 per PRD | v0.2.0; note #68 (quarantine is unrecoverable) must be fixed before drift lands |
| Promotion gate + regression diff | ✅ Core shipped | `registry.promote` + `compare` endpoint; #76 (diff union gap) |

## Suggested fix order

1. **Before more Part 3/8 work:** #63 (reference executor default), #62 (corpus path
   traversal), #64 (survival gate), #68 (recovery path), #69 (record history) — these change
   the certification contract that Part 8 (adapters) will build against.
2. **With Part 8 (M16–M17):** #72 (run start path), #73 (tool-call boundary), #66/#67
   (sandbox caps/egress enforcement), #71 (task timeout/network in runner).
3. **Quick wins, any time:** #74, #65, #75, #76, #77, #78, #79, #80, #81.

## Resolution

All 20 findings are fixed on `main`. Summary of the fixes and the tests that cover them:

| # | Fix | Key tests |
|---|-----|-----------|
| #62 | `CertificationCoordinator._resolve_corpus_path` resolves and confines corpus references to the corpora root | `test_certify_rejects_corpus_path_traversal`, `test_certify_allows_in_root_corpus_override` |
| #63 | Default app uses `UnconfiguredTaskExecutor`; `ReferenceExecutor` requires `HIVEPLANE_CERTIFICATION__EXECUTOR=reference`; `503` otherwise | `test_default_app_refuses_certification_without_executor`, `test_reference_executor_opt_in_allows_certification` |
| #64 | `production_runs_survived` tracked server-side on `WorkloadRecord`; incremented on completed production runs; removed from the API | `test_production_certification_requires_server_side_survived_runs`, `test_increment_production_runs_and_reset_on_transition` |
| #65 | `BudgetService.record_usage` raises `MissingModelIdentityError` instead of trusting caller cost | `test_record_usage_without_model_identity_is_rejected` |
| #66 | CPU cap bounds by wall clock (not cores); `inspect_caps` records `caps_applied`/`cap_errors` | `test_cpu_cap_uses_wall_clock_not_core_count`, `test_process_sandbox_records_applied_caps`, `test_cap_errors_are_recorded` |
| #67 | `EgressGuard` enforced at the tool boundary for any call carrying a `host` | `test_disallowed_egress_is_denied`, `test_allowed_egress_and_shaping` |
| #68 | Engine emits `RECOVER` for a passing re-certification of a quarantined workload | `test_quarantined_passing_recert_recovers_to_provisional`, `test_quarantined_recovers_on_passing_recert` |
| #69 | Records keyed by unique `record_id` (attestation id); append-only history | `test_records_are_append_only` |
| #70 | `workload_policy` honors the manifest's thresholds; service passes an effective policy to the engine | `test_workload_policy_overrides_fleet_thresholds`, `test_manifest_staging_threshold_overrides_fleet_default` |
| #71 | Runner enforces per-task `timeout_seconds` and `allow_network` (`network_used`) | `test_task_timeout_is_enforced`, `test_network_use_is_blocked_when_not_allowed`, `test_network_use_allowed_when_task_permits` |
| #72 | `POST /runs/{id}/start` (QUEUED→RUNNING); QUEUED→CANCELLED permitted | `test_start_queued_run`, `test_stop_queued_run` |
| #73 | `ToolGateway` (+ `POST /runs/{id}/tool-calls`) is the live policy/shaping boundary and records `policy_decision` events | `tests/test_tool_gateway.py`, `test_tool_call_endpoint_denies_unlisted_tool` |
| #74 | `record_usage` no-ops after terminal states; prices before persisting | `test_late_usage_after_failure_is_ignored`, `test_usage_cost_is_priced_server_side` |
| #75 | `canonical_model_identity` / `validate_model_identity`; canonical form enforced on submission | `test_run_submission_rejects_non_canonical_model_identity` |
| #76 | Diff reports `added`/`removed`; removing a passing task blocks promotion | `test_regression_diff_blocks_on_removed_passing_task`, `test_regression_diff_surfaces_added_task` |
| #77 | Coordinator links `previous_attestation_id` from the workload's latest attestation | `test_attestations_form_a_chain` |
| #78 | Restricted read-only escalates; high blast allows a certified production tool | `test_restricted_read_only_escalates`, `test_high_blast_radius_allowed_with_production_certification` |
| #79 | Approve/deny reject when the run is not paused, keeping records consistent | `tests/test_approval_api.py` |
| #80 | Atomic JSON writes (temp + replace); durable JSON store by default with config | `test_json_store_write_is_atomic`, `test_default_run_store_is_durable_json` |
| #81 | `BENCHMARK_VERSION` lives with the runner and is the attestation default | covered by certification/service suites |

Verification: `pytest` 474 passed, coverage 97%, `ruff check` clean, `mypy` strict clean.
