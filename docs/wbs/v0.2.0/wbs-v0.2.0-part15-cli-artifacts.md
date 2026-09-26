# WBS v0.2.0 — Part 15: CLI, Copilot & Artifacts

**Milestones:** M53–M54 · **Part:** 15 of 19

## Goal

Give operators a powerful CLI (including a natural-language copilot that is itself a certified workload) and an emergency incident mode, plus a durable artifact store with retention and portable export/import bundles.

## M53 — CLI Depth, `ask` NL Copilot & Incident Mode

**Objective:** Extend the CLI (`health`, `cost`, `report`, `replay`, `ask`, `top`, `logs`, completions), ship `hiveplane ask` — an NL operator copilot that is itself a registered, certified, budgeted workload — and an incident mode that halts the fleet in under 5 seconds.

**Work items:**

- [ ] [#407](https://github.com/deghosal-2026/hiveplane/issues/407) — M53-01 — CLI: `hiveplane health|cost|report|replay|top|logs` + shell completions
- [ ] [#408](https://github.com/deghosal-2026/hiveplane/issues/408) — M53-02 — `hiveplane ask` — natural-language operator Q&A over live control-plane state ("why did run 42 fail?", "show team X spend last week", "who approved the destructive call?")
- [ ] [#409](https://github.com/deghosal-2026/hiveplane/issues/409) — M53-03 — `ask` as a first-class workload: registered, certified via benchmark, run under budget/policy, attributed (dogfooding the thesis)
- [ ] [#410](https://github.com/deghosal-2026/hiveplane/issues/410) — M53-04 — `ask` read-only by default: queries state, never mutates without an explicit confirmation path
- [ ] [#411](https://github.com/deghosal-2026/hiveplane/issues/411) — M53-05 — Incident mode: `hiveplane fleet pause` — global halt, drain triggers, broadcast to owners, big-red-button in UI; <5s fleet stop
- [ ] [#412](https://github.com/deghosal-2026/hiveplane/issues/412) — M53-06 — Incident mode recovery: resume with attribution and an incident record
- [ ] [#413](https://github.com/deghosal-2026/hiveplane/issues/413) — M53-07 — Tests: each CLI command; `ask` answers a fixed question set from live state; incident mode halts within 5s and broadcasts

**Test ticket:** [#414](https://github.com/deghosal-2026/hiveplane/issues/414) — Test cases for CLI Depth, `ask` NL Copilot & Incident Mode

**Deliverables:**
- Extended CLI + completions
- `hiveplane ask` copilot workload + certification corpus
- Incident mode control + `docs/design/ask-copilot-design.md`, `docs/design/incident-mode-design.md`

**Acceptance criteria:**
- [ ] `hiveplane ask` answers 5 live-state questions correctly, itself under budget + certification
- [ ] `ask` cannot mutate state without explicit confirmation
- [ ] Incident mode halts the fleet in <5s and broadcasts to owners
- [ ] Recovery resumes the fleet and records the incident
- [ ] `health`, `cost`, `report`, `replay`, `top`, `logs` all return real data

**Done when:** operators have a fast, safe CLI plus a copilot that proves the certification thesis by using it, and an emergency stop that works instantly.

**Dependencies:** M42 (health), M50 (cost), M40 (policy), v0.1.0 certification.

**Notes / risks:** `ask` must never become an unaudited backdoor to mutate state — read-only by default, every query attributed. Incident mode must bypass normal propagation to be truly instant.

## M54 — Artifact Store, Retention & Export/Import

**Objective:** Persist run artifacts (files/reports agents produce) with retention policies and links in fan-out/UI, and support portable export/import bundles for workloads, corpora, and policy packs.

**Work items:**

- [ ] [#415](https://github.com/deghosal-2026/hiveplane/issues/415) — M54-01 — Artifact store: local backend + S3/MinIO backend
- [ ] [#416](https://github.com/deghosal-2026/hiveplane/issues/416) — M54-02 — Artifact capture: link artifacts to runs/pipelines; store content-addressed with hashes
- [ ] [#417](https://github.com/deghosal-2026/hiveplane/issues/417) — M54-03 — Retention policies: per-tenant/per-artifact retention; scheduled purge
- [ ] [#418](https://github.com/deghosal-2026/hiveplane/issues/418) — M54-04 — Artifact links in fan-out payloads + UI run detail
- [ ] [#419](https://github.com/deghosal-2026/hiveplane/issues/419) — M54-05 — `hiveplane export/import` bundles: manifest + corpus + policy pack (+ provenance signatures from M35)
- [ ] [#420](https://github.com/deghosal-2026/hiveplane/issues/420) — M54-06 — Import safety: signature verification, conflict handling, dry-run
- [ ] [#421](https://github.com/deghosal-2026/hiveplane/issues/421) — M54-07 — Tests: artifact stored + retrieved + linked; retention purge removes expired; export/import round-trips; tampered import refused

**Test ticket:** [#422](https://github.com/deghosal-2026/hiveplane/issues/422) — Test cases for Artifact Store, Retention & Export/Import

**Deliverables:**
- `hiveplane.artifacts` package (local + S3/MinIO)
- Export/import CLI
- `docs/design/artifact-store-design.md`, `docs/design/export-import-design.md`

**Acceptance criteria:**
- [ ] An agent-produced artifact is stored, linked in fan-out/UI, and retrievable
- [ ] Retention purge deletes expired artifacts on schedule and leaves audit evidence
- [ ] An export bundle round-trips to another plane with provenance verified
- [ ] A tampered import is refused
- [ ] Artifact storage works against MinIO/S3 in the reference stack

**Done when:** agent outputs are durable, retained per policy, and portable between planes with provenance.

**Dependencies:** M35 (provenance), M25 (artifact model), v0.1.0 state store.

**Notes / risks:** artifact contents may contain sensitive data — retention and PII purge (M57) must coordinate. Import must never auto-execute imported workloads; register → certify → admit as usual.

## Exit Gate (M53, M54)

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] All relevant docs updated (CLI reference, ask copilot, incident mode, artifacts, export/import)
- [ ] All M53–M54 issues done and closed
- [ ] Commit and push changes

## See Also

- [PRD 05 Features](../../prd/05-features.md) — Operator Surface, Artifacts & Portability themes
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillars M, Q
- [v0.2.0 index](wbs-v0.2.0-index.md)
