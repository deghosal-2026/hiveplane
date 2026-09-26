# WBS v0.2.0 — Part 5: Regression Diff, Drift & Quarantine

**Milestones:** M33–M34 · **Part:** 5 of 19

## Goal

Give the immune system its two most powerful behaviors: an explainable regression diff when re-certification fails, and a drift detector that automatically quarantines a decaying production agent and supports reinstatement.

## M33 — Regression Diff & Certification Compare

**Objective:** When re-certification fails, show exactly which corpus tasks regressed versus the previous certified baseline, with replayable traces for each regression, and expose comparison across any two certifications.

**Work items:**

- [ ] [#226](https://github.com/deghosal-2026/hiveplane/issues/226) — M33-01 — Task-level diff engine: pass→fail, fail→pass, metric deltas, latency deltas, cost deltas
- [ ] [#227](https://github.com/deghosal-2026/hiveplane/issues/227) — M33-02 — Baseline selection (last certified vs. specified attestation id) and deterministic comparison
- [ ] [#228](https://github.com/deghosal-2026/hiveplane/issues/228) — M33-03 — Replayable traces: each regressed task links to a replayable execution frame set
- [ ] [#229](https://github.com/deghosal-2026/hiveplane/issues/229) — M33-04 — Severity classification (critical vs. non-critical regressions) aligned with threshold rules
- [ ] [#230](https://github.com/deghosal-2026/hiveplane/issues/230) — M33-05 — Diff artifact generation (machine-readable + human report) attached to the certification record
- [ ] [#231](https://github.com/deghosal-2026/hiveplane/issues/231) — M33-06 — API + CLI: `hiveplane certs compare <v1> <v2>` with `--json` output
- [ ] [#232](https://github.com/deghosal-2026/hiveplane/issues/232) — M33-07 — Tests: seeded regressions are identified exactly; no false positives on unchanged corpora

**Test ticket:** [#233](https://github.com/deghosal-2026/hiveplane/issues/233) — Test cases for Regression Diff & Certification Compare

**Deliverables:**
- `hiveplane.certification.diff` extension
- Regression report format + `docs/design/certification-v2-design.md`

**Acceptance criteria:**
- [ ] A seeded regression is pinpointed to the exact task(s) with metric deltas
- [ ] Comparing identical certifications yields an empty diff
- [ ] Every regressed task exposes a replayable trace
- [ ] Critical regressions are flagged and feed the promotion gate's refusal reason
- [ ] `--json` output is stable and schema-validated

**Done when:** a failed re-certification produces a precise, replayable, human- and machine-readable regression diff.

**Dependencies:** M32; v0.1.0 certification store + corpus.

**Notes / risks:** metric drift vs. real regression — require deterministic checks and threshold tolerance to avoid noise. Keep diff output schema-stable for tooling.

## M34 — Drift Detector, Auto-Quarantine & Reinstatement

**Objective:** Periodically re-certify production agents, detect behavioral decay below threshold, auto-quarantine the agent (suspend + revoke admission + notify), and provide a safe reinstatement path. Add certification expiry/renewal windows.

**Work items:**

- [ ] [#234](https://github.com/deghosal-2026/hiveplane/issues/234) — M34-01 — Drift scheduler: periodic re-certification cadence per workload (configurable)
- [ ] [#235](https://github.com/deghosal-2026/hiveplane/issues/235) — M34-02 — Drift detection: compare current benchmark performance to the certified baseline; threshold + trend logic
- [ ] [#236](https://github.com/deghosal-2026/hiveplane/issues/236) — M34-03 — Auto-quarantine action: suspend admission, cancel/pause in-flight work per policy, mark `quarantined`
- [ ] [#237](https://github.com/deghosal-2026/hiveplane/issues/237) — M34-04 — Owner notification via fan-out channels (Slack/webhook) with the drift evidence and regression diff
- [ ] [#238](https://github.com/deghosal-2026/hiveplane/issues/238) — M34-05 — Quarantine history + reason persisted; visible on the certification dashboard
- [ ] [#239](https://github.com/deghosal-2026/hiveplane/issues/239) — M34-06 — Reinstatement flow: fix → re-certify → promote back → resume admission; all audited
- [ ] [#240](https://github.com/deghosal-2026/hiveplane/issues/240) — M34-07 — Certification expiry/renewal windows: certifications expire per policy; expired → not admissible
- [ ] [#241](https://github.com/deghosal-2026/hiveplane/issues/241) — M34-08 — False-positive controls: require N consecutive failures or a minimum delta before quarantining
- [ ] [#242](https://github.com/deghosal-2026/hiveplane/issues/242) — M34-09 — Tests: seeded drifting agent is quarantined; stable agent is not; reinstatement restores admission; expiry blocks admission

**Test ticket:** [#243](https://github.com/deghosal-2026/hiveplane/issues/243) — Test cases for Drift Detector, Auto-Quarantine & Reinstatement

**Deliverables:**
- `hiveplane.drift` package (scheduler, detector, quarantine, reinstatement)
- `docs/design/certification-v2-design.md`; dashboard additions

**Acceptance criteria:**
- [ ] A seeded drifting agent is auto-quarantined and its owner notified with evidence
- [ ] A stable agent is never falsely quarantined in the field test
- [ ] Quarantine revokes production admission immediately
- [ ] Reinstatement requires a fresh passing certification
- [ ] An expired certification blocks production admission

**Done when:** drift is detected, quarantined, explained, and reversible — with no false positives in the field test.

**Dependencies:** M33; M28 (notifications); v0.1.0 certification + registry.

**Notes / risks:** quarantine is disruptive — bias toward detection with evidence, and make reinstatement fast. Never quarantine on a single flaky run.

## Exit Gate (M33, M34)

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] All relevant docs updated (regression diff, drift detection, dashboard docs)
- [ ] All M33–M34 issues done and closed
- [ ] Commit and push changes

## See Also

- [PRD 05 Features](../../prd/05-features.md) — Certification Pipeline theme
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillars C, D
- [v0.2.0 index](wbs-v0.2.0-index.md)
