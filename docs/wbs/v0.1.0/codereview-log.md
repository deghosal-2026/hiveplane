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
