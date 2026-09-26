# WBS v0.2.0 — Part 4: Runtime Breadth & Promotion Gate

**Milestones:** M31–M32 · **Part:** 4 of 19

## Goal

Broaden the runtime surface (more frameworks, a converter for existing apps) and strengthen the thesis: a promotion gate that refuses to move a changed manifest to production until re-certification passes.

## M31 — Runtime Adapters v2 & `hiveplane wrap`

**Objective:** Add third and fourth runtime adapters (PydanticAI, plus OpenAI Agents SDK/CrewAI), harden the adapter contract with a conformance suite v2, and let operators convert an existing app into a HivePlane workload + adapter scaffold.

**Work items:**

- [x] [#208](https://github.com/deghosal-2026/hiveplane/issues/208) — M31-01 — Adapter contract v2 (lifecycle, streaming, tool calls, cancellation, usage reporting, model identity) — documented and versioned
- [x] [#209](https://github.com/deghosal-2026/hiveplane/issues/209) — M31-02 — PydanticAI adapter (certifiable via the benchmark, real entrypoint execution)
- [x] [#210](https://github.com/deghosal-2026/hiveplane/issues/210) — M31-03 — OpenAI Agents SDK adapter and/or CrewAI adapter (fourth runtime; at least one ships)
- [x] [#211](https://github.com/deghosal-2026/hiveplane/issues/211) — M31-04 — Conformance suite v2: every adapter must pass the same behavioral contract tests
- [x] [#212](https://github.com/deghosal-2026/hiveplane/issues/212) — M31-05 — `hiveplane wrap` — inspect a LangGraph/CrewAI/OpenAI-SDK app and generate a workload manifest + adapter scaffold
- [x] [#213](https://github.com/deghosal-2026/hiveplane/issues/213) — M31-06 — Wrap safety: never modifies the source app; generated scaffold is inert until registered and certified
- [x] [#214](https://github.com/deghosal-2026/hiveplane/issues/214) — M31-07 — Docs: "bring your own agent" guide for each supported framework
- [x] [#215](https://github.com/deghosal-2026/hiveplane/issues/215) — M31-08 — Tests: conformance for each adapter; wrap round-trip on sample apps

**Test ticket:** [x] [#216](https://github.com/deghosal-2026/hiveplane/issues/216) — Test cases for Runtime Adapters v2 & `hiveplane wrap`

**Deliverables:**
- Adapters for PydanticAI + (OpenAI Agents SDK or CrewAI) under `src/hiveplane/adapters/`
- Conformance suite v2 shared across all adapters
- `hiveplane wrap` CLI + `docs/design/runtime-adapter-v2-design.md`

**Acceptance criteria:**
- [x] Every shipped adapter passes conformance suite v2 in CI
- [x] A PydanticAI agent can be registered, certified, and run end-to-end
- [x] `hiveplane wrap` generates a manifest + scaffold for a sample app without modifying it
- [x] A wrapped app that is uncertified is refused production admission
- [x] Model identity is reported by the adapter and bound to the attestation (no self-report)

**Done when:** ≥3 runtimes (raw-worker, LangGraph, PydanticAI, + one more) certify through the same benchmark and contract, and `hiveplane wrap` onboards an existing app.

> **Status:** M31 complete. Contract v2 (`AdapterCapabilities`, `AdapterEvent`,
> `capabilities()`/`stream()`/`model_identity()`/`conformance_version()`),
> PydanticAI and OpenAI Agents adapters with governed inference, conformance
> suite v2, the import-boundary test, `hiveplane wrap`, `GET /adapters`, and the
> optional extras (`pydantic-ai`, `openai-agents`) landed. All four adapters
> (raw-worker, LangGraph, PydanticAI, OpenAI Agents) pass conformance v2. Issues
> #208–#216 closed; committed and pushed.

**Dependencies:** v0.1.0 adapter contract; M25.

**Notes / risks:** adapters must not leak framework types into core — enforce with import-boundary tests. Do not let `wrap` become a code rewriter; scaffold-only is the contract.

## M32 — Promotion Gate & Re-certification

**Objective:** Enforce staging→production promotion: any manifest, toolset, model-binding, or policy change invalidates the certification and blocks promotion until re-certification passes.

**Work items:**

- [x] [#217](https://github.com/deghosal-2026/hiveplane/issues/217) — M32-01 — Environment/context model (staging, production) with admission rules per context
- [x] [#218](https://github.com/deghosal-2026/hiveplane/issues/218) — M32-02 — Change detection: hash of manifest + toolset + model binding + policy version; certification is bound to that hash
- [x] [#219](https://github.com/deghosal-2026/hiveplane/issues/219) — M32-03 — Promotion workflow: request promotion → require valid unexpired certification for the target hash → admit or refuse with reason
- [x] [#220](https://github.com/deghosal-2026/hiveplane/issues/220) — M32-04 — Automatic invalidation: a changed artifact marks the workload `uncertified` for production (never silently passes)
- [x] [#221](https://github.com/deghosal-2026/hiveplane/issues/221) — M32-05 — Re-certification orchestration: run the benchmark for the new hash, produce a new attestation
- [x] [#222](https://github.com/deghosal-2026/hiveplane/issues/222) — M32-06 — Promotion gate API + CLI (`hiveplane promote`)
- [x] [#223](https://github.com/deghosal-2026/hiveplane/issues/223) — M32-07 — Audit: every promotion attempt, block, and certification linked in the run/attestation story
- [x] [#224](https://github.com/deghosal-2026/hiveplane/issues/224) — M32-08 — Tests: changed manifest blocks promotion; unchanged manifest promotes; toolset change blocks; model-binding change blocks

**Test ticket:** [x] [#225](https://github.com/deghosal-2026/hiveplane/issues/225) — Test cases for Promotion Gate & Re-certification

**Deliverables:**
- Promotion gate service integrated with registry + certification
- `docs/design/certification-v2-design.md`

**Acceptance criteria:**
- [x] Promoting an unchanged, certified workload to production succeeds
- [x] Promoting after any bound change is refused until re-certification passes
- [x] An uncertified workload cannot be promoted under any trigger trust level
- [x] The refusal reason names the exact changed binding
- [x] Promotion and block events are fully audited

**Done when:** production promotion is impossible without a valid certification for the exact current artifact, and every block is explainable.

> **Status:** M32 complete. Artifact binding (`hiveplane.certification.binding`),
> `Attestation.artifact_hash`/`binding`, the `PromotionGate` (with
> `recertify_and_promote`), automatic invalidation on artifact change,
> `PromotionStore` + migration `0010`, the promotions API, `hiveplane promote`,
> and promotion auditing landed. All tests pass with a database; coverage 95%
> total, ruff and mypy strict clean. Issues #217–#225 closed; committed and pushed.

**Dependencies:** M25; v0.1.0 certification pipeline; M31 (hash includes adapter/toolset).

**Notes / risks:** the hash definition is the crux — under-hashing lets a change slip through; over-hashing creates false re-certs. Document and test the hash inputs explicitly.

## Exit Gate (M31, M32)

- [x] All tests in the system pass: `pytest`
- [x] Code coverage total ≥ 95%
- [x] Ruff clean
- [x] Mypy strict clean
- [x] All relevant docs updated (adapter v2, promotion gate, bring-your-own-agent guide)
- [x] All M31–M32 issues done and closed
- [x] Commit and push changes

## See Also

- [PRD 05 Features](../../prd/05-features.md) — Certification Pipeline theme
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillars C, R
- [v0.2.0 index](wbs-v0.2.0-index.md)
