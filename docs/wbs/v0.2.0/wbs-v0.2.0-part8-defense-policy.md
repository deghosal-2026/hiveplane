# WBS v0.2.0 — Part 8: Defense & Policy

**Milestones:** M39–M40 · **Part:** 8 of 19

## Goal

Make the fleet defend itself. Scan inputs and tool outputs for injection, constrain egress, evaluate policy with full context (environment, data sensitivity, blast radius, time), support team policy packs, and give operators an instant tool kill switch.

## M39 — Injection Defense & Egress Allow-Lists

**Objective:** Block prompt-injection and adversarial input at the boundary with deterministic scanning, taint marks, and provenance tags, and constrain per-workload outbound network access.

**Work items:**

- [x] [#279](https://github.com/deghosal-2026/hiveplane/issues/279) — M39-01 — Input/tool-output scanner: deterministic detectors for injection patterns, instruction smuggling, and data-exfiltration attempts
- [x] [#280](https://github.com/deghosal-2026/hiveplane/issues/280) — M39-02 — Taint marks + provenance tags: track untrusted content through the run so downstream tools can be gated
- [x] [#281](https://github.com/deghosal-2026/hiveplane/issues/281) — M39-03 — Blocking action: quarantine/refuse the offending tool output or input with a recorded reason
- [x] [#282](https://github.com/deghosal-2026/hiveplane/issues/282) — M39-04 — Repeated-attempt escalation: repeated injection attempts feed auto-quarantine (shared with M34)
- [x] [#283](https://github.com/deghosal-2026/hiveplane/issues/283) — M39-05 — Egress allow-lists: per-workload outbound domain/port policy enforced by the sandbox
- [x] [#284](https://github.com/deghosal-2026/hiveplane/issues/284) — M39-06 — Egress denial is audited and surfaced in the run story
- [x] [#285](https://github.com/deghosal-2026/hiveplane/issues/285) — M39-07 — Detector configurability + false-positive controls; detectors are versioned
- [x] [#286](https://github.com/deghosal-2026/hiveplane/issues/286) — M39-08 — Tests: seeded injection via tool output is blocked; taint propagates; egress to a non-allowed host is denied

**Test ticket:** [#287](https://github.com/deghosal-2026/hiveplane/issues/287) — Test cases for Injection Defense & Egress Allow-Lists

**Deliverables:**
- `hiveplane.defense` package (scanner, taint, egress, escalation, security events, guard)
- Public `GET /security/events` endpoint; migration `0014_security_events` (follows M35's `0012`/`0013`)
- `docs/design/defense-policy-v2-design.md` and egress policy reference (this guide + `USER_GUIDE.md`)

**Acceptance criteria:**
- [x] A seeded injection via tool output is blocked with a reason
- [x] Taint marks propagate so an untrusted value cannot silently reach a destructive tool
- [x] Repeated injection attempts escalate to quarantine
- [x] A run attempting egress to a non-allowed domain is denied and audited
- [x] Detectors are versioned and configurable per policy pack

**Done when:** injection is blocked deterministically, and network egress is constrained and auditable.

> **Status (M39):** complete. Implemented at the sandbox-channel/tool-call boundary;
> production netns/CNI egress enforcement is the documented follow-on layer that
> mirrors the same allow-list.

**Dependencies:** v0.1.0 sandbox + tool-output shaping; M34 (quarantine).

**Notes / risks:** false positives can break legitimate agents — make detectors tunable and always explain a block. Egress policy must default-deny with an explicit allow-list.

## M40 — Context-Aware Policy, What-If, Policy Packs, Time Windows & Kill Switch

**Objective:** Evaluate policy with real context (staging/prod, data sensitivity, blast radius, time-of-day), let operators dry-run decisions, distribute versioned team policy packs, and disable a bad tool instantly fleet-wide.

**Work items:**

- [ ] [#288](https://github.com/deghosal-2026/hiveplane/issues/288) — M40-01 — Context-aware policy engine: decision inputs include environment, data sensitivity (public/PII), blast-radius score, budget state, and time
- [ ] [#289](https://github.com/deghosal-2026/hiveplane/issues/289) — M40-02 — Decision records: every decision carries a reason and the originating rule id
- [ ] [#290](https://github.com/deghosal-2026/hiveplane/issues/290) — M40-03 — Policy what-if / dry-run: evaluate a proposed decision without executing it
- [ ] [#291](https://github.com/deghosal-2026/hiveplane/issues/291) — M40-04 — Team policy packs: versioned, inheritable bundles with linting and distribution
- [ ] [#292](https://github.com/deghosal-2026/hiveplane/issues/292) — M40-05 — Time-window policies: business-hours-only destructive actions, blackout calendars
- [ ] [#293](https://github.com/deghosal-2026/hiveplane/issues/293) — M40-06 — Tool kill switch: instant fleet-wide disable of any tool, with audit and re-enable
- [ ] [#294](https://github.com/deghosal-2026/hiveplane/issues/294) — M40-07 — Policy pack CLI (`hiveplane policies lint|publish|apply`) + API
- [ ] [#295](https://github.com/deghosal-2026/hiveplane/issues/295) — M40-08 — Tests: staging vs. prod decision differs; what-if matches real decision; pack inheritance/lint; kill switch blocks a tool instantly

**Test ticket:** [#296](https://github.com/deghosal-2026/hiveplane/issues/296) — Test cases for Context-Aware Policy, What-If, Policy Packs, Time Windows & Kill Switch

**Deliverables:**
- Extended policy engine + decision records
- Policy pack registry; kill-switch control
- `docs/design/defense-policy-v2-design.md` and `docs/design/defense-policy-v2-design.md`

**Acceptance criteria:**
- [ ] The same tool call is allowed in staging and denied in production per policy
- [ ] Every decision includes a reason + originating rule id
- [ ] What-if output matches the real decision for the same context
- [ ] A team policy pack inherits, lints, and applies across a team's workloads
- [ ] A time-window rule blocks a destructive action outside business hours
- [ ] The kill switch disables a tool fleet-wide instantly and is audited

**Done when:** policy is context-aware, explainable, distributable, and instantly overridable in an emergency.

**Dependencies:** M25 (policy pack model); v0.1.0 policy engine.

**Notes / risks:** the kill switch must be near-instant and fail-safe — design it to bypass normal config propagation. What-if must share the exact code path as real evaluation to avoid divergence.

## Exit Gate (M39, M40)

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] All relevant docs updated (injection defense, context policy, policy packs, egress)
- [ ] All M39–M40 issues done and closed
- [ ] Commit and push changes

## See Also

- [PRD 05 Features](../../prd/05-features.md) — Policy & Governance, Safe Execution themes
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillar E
- [v0.2.0 index](wbs-v0.2.0-index.md)
