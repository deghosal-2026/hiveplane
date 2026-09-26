# WBS v0.2.0 — Part 16: Corpus Tooling & API/SDK

**Milestones:** M55–M56 · **Part:** 16 of 19

## Goal

Make benchmarks easy to author and version, and expose the whole plane through a stable API v2 with a Python SDK, agent-as-service endpoints, rate limits, an events webhook, and plugin hooks.

## M55 — Corpus & Benchmark Tooling

**Objective:** Give operators a corpus authoring CLI with templates, corpus versioning/sharing, and benchmark profiles (fast for the dev loop, full for promotion) plus scheduled corpus expansion.

**Work items:**

- [ ] [#423](https://github.com/deghosal-2026/hiveplane/issues/423) — M55-01 — Corpus authoring CLI: scaffold a corpus, add/edit tasks, validate, and lint (`hiveplane corpus init|add|validate|publish`)
- [ ] [#424](https://github.com/deghosal-2026/hiveplane/issues/424) — M55-02 — Corpus templates for common workload types (repo agent, triage, generation, classification)
- [ ] [#425](https://github.com/deghosal-2026/hiveplane/issues/425) — M55-03 — Corpus versioning + sharing: versioned corpora, export/import (with M54 bundles)
- [ ] [#426](https://github.com/deghosal-2026/hiveplane/issues/426) — M55-04 — Benchmark profiles: fast (subset, dev loop) and full (promotion) with explicit profile in attestations
- [ ] [#427](https://github.com/deghosal-2026/hiveplane/issues/427) — M55-05 — Scheduled corpus expansion: periodically extend coverage from feedback candidates (M36) after review
- [ ] [#428](https://github.com/deghosal-2026/hiveplane/issues/428) — M55-06 — `hiveplane init` integration: seed a sample corpus (v0.1.0 behavior preserved/improved)
- [ ] [#429](https://github.com/deghosal-2026/hiveplane/issues/429) — M55-07 — Tests: scaffold validates; profiles run the right subset; versioning is immutable; expansion adds reviewed cases only

**Test ticket:** [#430](https://github.com/deghosal-2026/hiveplane/issues/430) — Test cases for Corpus & Benchmark Tooling

**Deliverables:**
- Corpus CLI + templates + profile support
- `docs/design/platform-api-design.md`, corpus authoring guide

**Acceptance criteria:**
- [ ] `hiveplane corpus init` scaffolds a valid corpus that certifies a demo workload
- [ ] A fast profile runs a deterministic subset; the full profile runs all tasks; the attestation records which profile
- [ ] Corpus versions are immutable and shareable via export/import
- [ ] Scheduled expansion only adds reviewed feedback candidates
- [ ] Malformed corpus edits are rejected with actionable errors

**Done when:** authoring and versioning benchmarks is easy, profiles fit both dev and promotion, and corpora are shareable.

**Dependencies:** M36 (feedback), M33 (diff), v0.1.0 corpus/runner.

**Notes / risks:** profile choice must be explicit in the attestation — certifying on a fast subset for production would be a loophole. Enforce a minimum profile for production promotion.

## M56 — REST API v2, Python SDK, Agent-as-Service, Rate Limits, Events Webhook & Plugin Hooks

**Objective:** Stabilize a public API v2 (OpenAPI, pagination), ship a Python SDK, serve per-workload agent-as-service endpoints through the gates, add per-tenant rate limits, a fleet-events webhook, and plugin hooks for community extension.

**Work items:**

- [ ] [#431](https://github.com/deghosal-2026/hiveplane/issues/431) — M56-01 — REST API v2: consistent versioning, OpenAPI spec, pagination everywhere, error model
- [ ] [#432](https://github.com/deghosal-2026/hiveplane/issues/432) — M56-02 — Python SDK: typed client covering runs, certs, triggers, pipelines, cost, approvals, health
- [ ] [#433](https://github.com/deghosal-2026/hiveplane/issues/433) — M56-03 — Agent-as-service endpoints: per-workload HTTP endpoint served through auth, budget, admission gates, and policy
- [ ] [#434](https://github.com/deghosal-2026/hiveplane/issues/434) — M56-04 — Per-tenant API rate limits: request quotas with 429s and `Retry-After`
- [ ] [#435](https://github.com/deghosal-2026/hiveplane/issues/435) — M56-05 — Fleet-events webhook: operators subscribe to run/approval/drift/trigger events
- [ ] [#436](https://github.com/deghosal-2026/hiveplane/issues/436) — M56-06 — Plugin hooks: custom trigger sources, fan-out channels, and policy checks via a documented interface
- [ ] [#437](https://github.com/deghosal-2026/hiveplane/issues/437) — M56-07 — Plugin safety: hooks run in a restricted context; failures cannot crash the plane
- [ ] [#438](https://github.com/deghosal-2026/hiveplane/issues/438) — M56-08 — Tests: API v2 contract tests; SDK round-trips a full run; service endpoint enforces gates; 429 on over-limit; a sample plugin hook fires

**Test ticket:** [#439](https://github.com/deghosal-2026/hiveplane/issues/439) — Test cases for REST API v2, Python SDK, Agent-as-Service, Rate Limits, Events Webhook & Plugin Hooks

**Deliverables:**
- API v2 + OpenAPI; Python SDK package
- Agent-as-service router; plugin interface + example plugin
- `docs/design/platform-api-design.md`, `docs/design/platform-api-design.md`, SDK docs

**Acceptance criteria:**
- [ ] API v2 is paginated and OpenAPI-documented; contract tests pass
- [ ] The SDK submits a run, waits, and retrieves the result end-to-end
- [ ] An agent-as-service endpoint enforces auth/budget/admission/policy
- [ ] Over-limit tenants receive 429 with `Retry-After`
- [ ] A subscribed events webhook receives run/approval/drift events
- [ ] A custom plugin hook fires without destabilizing the plane

**Done when:** the plane is programmable through a stable API/SDK, serves agents as endpoints, and is extensible by the community.

**Dependencies:** all prior parts; M40 (policy), M49 (budget), M32 (admission).

**Notes / risks:** API stability is a long-term commitment — freeze v2 at release and version any change. Plugins are arbitrary code: sandbox them and never let a hook block the critical path.

## Exit Gate (M55, M56)

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] All relevant docs updated (corpus guide, API reference, SDK docs, plugin guide)
- [ ] All M55–M56 issues done and closed
- [ ] Commit and push changes

## See Also

- [PRD 05 Features](../../prd/05-features.md) — Corpus & Benchmark Tooling, API/SDK & Extensibility themes
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillars N, O
- [v0.2.0 index](wbs-v0.2.0-index.md)
