# WBS v0.2.0 — Part 6: Provenance & the Learning Loop

**Milestones:** M35–M36 · **Part:** 6 of 19

## Goal

Complete the trust story (certify code identity, not just behavior) and close the certification loop with production data — so the benchmark learns from real runs.

## M35 — Attestation Transparency Log, Public Verification & Workload Provenance

**Objective:** Make attestations independently verifiable and bind certification to signed agent code identity. Anyone can verify an attestation by ID; a swapped or tampered agent bundle fails admission.

**Work items:**

- [x] [#244](https://github.com/deghosal-2026/hiveplane/issues/244) — M35-01 — Attestation transparency log: append-only, hash-chained history of every certification
- [x] [#245](https://github.com/deghosal-2026/hiveplane/issues/245) — M35-02 — Public verification endpoint (`GET /attestations/{id}/verify`) — no auth; returns validity + chain proof
- [x] [#246](https://github.com/deghosal-2026/hiveplane/issues/246) — M35-03 — Workload signing: sign an agent bundle (manifest + adapter/entrypoint digest) at registration
- [x] [#247](https://github.com/deghosal-2026/hiveplane/issues/247) — M35-04 — Provenance verification at admission: verify bundle signature against the attested identity
- [x] [#248](https://github.com/deghosal-2026/hiveplane/issues/248) — M35-05 — Provenance on export/import: bundles carry signatures; tampered imports are rejected
- [x] [#249](https://github.com/deghosal-2026/hiveplane/issues/249) — M35-06 — Key management: persistent signing keys, rotation, and verification key distribution
- [x] [#250](https://github.com/deghosal-2026/hiveplane/issues/250) — M35-07 — Fan-out results link to the public verification URL
- [x] [#251](https://github.com/deghosal-2026/hiveplane/issues/251) — M35-08 — Tests: forged attestation rejected; tampered bundle fails admission; chain tamper detected; public verify works unauthenticated

**Test ticket:** [#252](https://github.com/deghosal-2026/hiveplane/issues/252) — Test cases for Attestation Transparency Log, Public Verification & Workload Provenance

**Deliverables:**
- `hiveplane.transparency` package (log, verification, signing, provenance)
- Public verify endpoint + `hiveplane verify` CLI
- `docs/design/certification-v2-design.md` and `docs/design/certification-v2-design.md`

**Acceptance criteria:**
- [x] Any attestation verifies publicly by ID without authentication
- [x] A modified agent bundle fails production admission on signature mismatch
- [x] Tampering with the transparency log is detectable
- [x] Exported/imported bundles verify their signatures; tampered imports are refused
- [x] Signing keys persist across restarts and rotate without breaking old attestations

**Done when:** behavior certification and code identity are both enforced, and attestations are independently verifiable.

> **Status:** M35 complete. `hiveplane.transparency` (hash-chained log, public verification, bundle provenance, key registry) landed with migrations `0012`/`0013`; every certification appends to the log; production admission now requires both a valid attestation and a verified bundle. API `GET /attestations/{id}/verify`, CLI `hiveplane verify`, and fan-out `verification_url` are live. Issues #244–#252 closed; all tests pass with a database, coverage >95%, ruff and mypy strict clean.

**Dependencies:** M32; v0.1.0 signed attestation + key persistence.

**Notes / risks:** signing/key rotation is security-critical; test restart and rotation paths. Public verification must not leak sensitive data — return only validity + public evidence.

## M36 — Production Feedback → Corpus & Online Eval Sampling

**Objective:** Let operators flag production runs (good/bad/failed-with-lesson) and turn flagged failures into corpus cases automatically, while sampling production runs for LLM-judge quality scoring that feeds health and drift between scheduled re-certifications.

**Work items:**

- [x] [#253](https://github.com/deghosal-2026/hiveplane/issues/253) — M36-01 — Run feedback capture: operator marks a run good/bad/failed-with-lesson (UI/CLI/API), with notes
- [x] [#254](https://github.com/deghosal-2026/hiveplane/issues/254) — M36-02 — Feedback → corpus pipeline: flagged failures are converted into candidate corpus cases (input + expected outcome) for review
- [x] [#255](https://github.com/deghosal-2026/hiveplane/issues/255) — M36-03 — Corpus review/approval before inclusion; rejected candidates are archived with a reason
- [x] [#256](https://github.com/deghosal-2026/hiveplane/issues/256) — M36-04 — Corpus versioning integration: accepted cases land in the next corpus version and are included in the next certification
- [x] [#257](https://github.com/deghosal-2026/hiveplane/issues/257) — M36-05 — Online eval sampling: sample a configurable % of production runs for LLM-judge scoring against a rubric
- [x] [#258](https://github.com/deghosal-2026/hiveplane/issues/258) — M36-06 — Judge results → health/drift signals (production quality scores as a first-class signal)
- [x] [#259](https://github.com/deghosal-2026/hiveplane/issues/259) — M36-07 — Sampling guardrails: cost caps, PII-safe sampling, deterministic sample selection
- [x] [#260](https://github.com/deghosal-2026/hiveplane/issues/260) — M36-08 — Tests: flagged failure becomes a corpus case; sampling scores runs; quality dip surfaces in health before re-cert

**Test ticket:** [#261](https://github.com/deghosal-2026/hiveplane/issues/261) — Test cases for Production Feedback → Corpus & Online Eval Sampling

**Deliverables:**
- Feedback store + corpus-candidate generator
- Online eval sampler + judge harness (local/cloud provider)
- `docs/design/learning-loop-design.md`

**Acceptance criteria:**
- [x] An operator-flagged failed run becomes a corpus case included in the next certification (after review)
- [x] Sampled production runs receive judge scores visible in the health model
- [x] A seeded quality dip alerts before the scheduled re-certification catches it
- [x] Sampling respects cost caps and never samples PII-marked runs
- [x] Rejected candidates never enter the corpus

**Done when:** the certification benchmark demonstrably learns from production, and online eval gives real-world quality signal between re-certs.

> **Status:** M36 complete. `hiveplane.learning` (run feedback, corpus candidates + mandatory review gate, corpus versioning, deterministic online-eval sampling, LLM judge, quality signal) landed with migrations `0014`–`0017`. API `POST /runs/{id}/feedback`, `GET /corpus/candidates`, approve/reject, `GET /eval/samples`, `GET /workloads/{id}/quality`; CLI `hiveplane feedback`, `hiveplane corpus ...`, `hiveplane eval ...`; UI feedback form. Issues #253–#261 closed; all tests pass with a database, coverage >95%, ruff and mypy strict clean.

**Dependencies:** M34 (drift/health), M33 (diff); corpus tooling (M55) for versioning.

**Notes / risks:** feedback→corpus is powerful and dangerous — a bad expected outcome poisons certification. Require human review of every candidate. Judge scoring must be rubric-versioned to avoid silent metric drift.

## Exit Gate (M35, M36)

- [x] All tests in the system pass: `pytest`
- [x] Code coverage total > 95%
- [x] Ruff clean
- [x] Mypy strict clean
- [x] All relevant docs updated (provenance, transparency log, learning loop, corpus guide)
- [x] All M35–M36 issues done and closed
- [x] Commit and push changes

## See Also

- [PRD 05 Features](../../prd/05-features.md) — Certification Pipeline theme
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillar C
- [v0.2.0 index](wbs-v0.2.0-index.md)
