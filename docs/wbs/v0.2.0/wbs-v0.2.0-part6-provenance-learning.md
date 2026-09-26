# WBS v0.2.0 — Part 6: Provenance & the Learning Loop

**Milestones:** M35–M36 · **Part:** 6 of 19

## Goal

Complete the trust story (certify code identity, not just behavior) and close the certification loop with production data — so the benchmark learns from real runs.

## M35 — Attestation Transparency Log, Public Verification & Workload Provenance

**Objective:** Make attestations independently verifiable and bind certification to signed agent code identity. Anyone can verify an attestation by ID; a swapped or tampered agent bundle fails admission.

**Work items:**

- [ ] [#244](https://github.com/deghosal-2026/hiveplane/issues/244) — M35-01 — Attestation transparency log: append-only, hash-chained history of every certification
- [ ] [#245](https://github.com/deghosal-2026/hiveplane/issues/245) — M35-02 — Public verification endpoint (`GET /attestations/{id}/verify`) — no auth; returns validity + chain proof
- [ ] [#246](https://github.com/deghosal-2026/hiveplane/issues/246) — M35-03 — Workload signing: sign an agent bundle (manifest + adapter/entrypoint digest) at registration
- [ ] [#247](https://github.com/deghosal-2026/hiveplane/issues/247) — M35-04 — Provenance verification at admission: verify bundle signature against the attested identity
- [ ] [#248](https://github.com/deghosal-2026/hiveplane/issues/248) — M35-05 — Provenance on export/import: bundles carry signatures; tampered imports are rejected
- [ ] [#249](https://github.com/deghosal-2026/hiveplane/issues/249) — M35-06 — Key management: persistent signing keys, rotation, and verification key distribution
- [ ] [#250](https://github.com/deghosal-2026/hiveplane/issues/250) — M35-07 — Fan-out results link to the public verification URL
- [ ] [#251](https://github.com/deghosal-2026/hiveplane/issues/251) — M35-08 — Tests: forged attestation rejected; tampered bundle fails admission; chain tamper detected; public verify works unauthenticated

**Test ticket:** [#252](https://github.com/deghosal-2026/hiveplane/issues/252) — Test cases for Attestation Transparency Log, Public Verification & Workload Provenance

**Deliverables:**
- `hiveplane.transparency` package (log, verification, signing, provenance)
- Public verify endpoint + `hiveplane verify` CLI
- `docs/design/certification-v2-design.md` and `docs/design/certification-v2-design.md`

**Acceptance criteria:**
- [ ] Any attestation verifies publicly by ID without authentication
- [ ] A modified agent bundle fails production admission on signature mismatch
- [ ] Tampering with the transparency log is detectable
- [ ] Exported/imported bundles verify their signatures; tampered imports are refused
- [ ] Signing keys persist across restarts and rotate without breaking old attestations

**Done when:** behavior certification and code identity are both enforced, and attestations are independently verifiable.

**Dependencies:** M32; v0.1.0 signed attestation + key persistence.

**Notes / risks:** signing/key rotation is security-critical; test restart and rotation paths. Public verification must not leak sensitive data — return only validity + public evidence.

## M36 — Production Feedback → Corpus & Online Eval Sampling

**Objective:** Let operators flag production runs (good/bad/failed-with-lesson) and turn flagged failures into corpus cases automatically, while sampling production runs for LLM-judge quality scoring that feeds health and drift between scheduled re-certifications.

**Work items:**

- [ ] [#253](https://github.com/deghosal-2026/hiveplane/issues/253) — M36-01 — Run feedback capture: operator marks a run good/bad/failed-with-lesson (UI/CLI/API), with notes
- [ ] [#254](https://github.com/deghosal-2026/hiveplane/issues/254) — M36-02 — Feedback → corpus pipeline: flagged failures are converted into candidate corpus cases (input + expected outcome) for review
- [ ] [#255](https://github.com/deghosal-2026/hiveplane/issues/255) — M36-03 — Corpus review/approval before inclusion; rejected candidates are archived with a reason
- [ ] [#256](https://github.com/deghosal-2026/hiveplane/issues/256) — M36-04 — Corpus versioning integration: accepted cases land in the next corpus version and are included in the next certification
- [ ] [#257](https://github.com/deghosal-2026/hiveplane/issues/257) — M36-05 — Online eval sampling: sample a configurable % of production runs for LLM-judge scoring against a rubric
- [ ] [#258](https://github.com/deghosal-2026/hiveplane/issues/258) — M36-06 — Judge results → health/drift signals (production quality scores as a first-class signal)
- [ ] [#259](https://github.com/deghosal-2026/hiveplane/issues/259) — M36-07 — Sampling guardrails: cost caps, PII-safe sampling, deterministic sample selection
- [ ] [#260](https://github.com/deghosal-2026/hiveplane/issues/260) — M36-08 — Tests: flagged failure becomes a corpus case; sampling scores runs; quality dip surfaces in health before re-cert

**Test ticket:** [#261](https://github.com/deghosal-2026/hiveplane/issues/261) — Test cases for Production Feedback → Corpus & Online Eval Sampling

**Deliverables:**
- Feedback store + corpus-candidate generator
- Online eval sampler + judge harness (local/cloud provider)
- `docs/design/learning-loop-design.md`

**Acceptance criteria:**
- [ ] An operator-flagged failed run becomes a corpus case included in the next certification (after review)
- [ ] Sampled production runs receive judge scores visible in the health model
- [ ] A seeded quality dip alerts before the scheduled re-certification catches it
- [ ] Sampling respects cost caps and never samples PII-marked runs
- [ ] Rejected candidates never enter the corpus

**Done when:** the certification benchmark demonstrably learns from production, and online eval gives real-world quality signal between re-certs.

**Dependencies:** M34 (drift/health), M33 (diff); corpus tooling (M55) for versioning.

**Notes / risks:** feedback→corpus is powerful and dangerous — a bad expected outcome poisons certification. Require human review of every candidate. Judge scoring must be rubric-versioned to avoid silent metric drift.

## Exit Gate (M35, M36)

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] All relevant docs updated (provenance, transparency log, learning loop, corpus guide)
- [ ] All M35–M36 issues done and closed
- [ ] Commit and push changes

## See Also

- [PRD 05 Features](../../prd/05-features.md) — Certification Pipeline theme
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillar C
- [v0.2.0 index](wbs-v0.2.0-index.md)
