# WBS v0.2.0 — Part 15: CLI, Copilot & Artifacts

**Milestones:** M53–M54 · **Part:** 15 of 19

## Goal

Give operators a powerful CLI (including a natural-language copilot that is itself a certified workload) and an emergency incident mode, plus a durable artifact store with retention and portable export/import bundles.

## M53 — CLI Depth, `ask` NL Copilot & Incident Mode

**Objective:** Extend the CLI (`health`, `cost`, `report`, `replay`, `ask`, `top`, `logs`, completions), ship `hiveplane ask` — an NL operator copilot that is itself a registered, certified, budgeted workload — and an incident mode that halts the fleet in under 5 seconds.

**Work items:**

- [x] [#407](https://github.com/deghosal-2026/hiveplane/issues/407) — M53-01 — CLI: `hiveplane health|cost|report|replay|top|logs` + shell completions
- [x] [#408](https://github.com/deghosal-2026/hiveplane/issues/408) — M53-02 — `hiveplane ask` — natural-language operator Q&A over live control-plane state ("why did run 42 fail?", "show team X spend last week", "who approved the destructive call?")
- [x] [#409](https://github.com/deghosal-2026/hiveplane/issues/409) — M53-03 — `ask` as a first-class workload: registered, certified via benchmark, run under budget/policy, attributed (dogfooding the thesis)
- [x] [#410](https://github.com/deghosal-2026/hiveplane/issues/410) — M53-04 — `ask` read-only by default: queries state, never mutates without an explicit confirmation path
- [x] [#411](https://github.com/deghosal-2026/hiveplane/issues/411) — M53-05 — Incident mode: `hiveplane fleet pause` — global halt, drain triggers, broadcast to owners, big-red-button in UI; <5s fleet stop
- [x] [#412](https://github.com/deghosal-2026/hiveplane/issues/412) — M53-06 — Incident mode recovery: resume with attribution and an incident record
- [x] [#413](https://github.com/deghosal-2026/hiveplane/issues/413) — M53-07 — Tests: each CLI command; `ask` answers a fixed question set from live state; incident mode halts within 5s and broadcasts

**Test ticket:** [x] [#414](https://github.com/deghosal-2026/hiveplane/issues/414) — Test cases for CLI Depth, `ask` NL Copilot & Incident Mode

**Deliverables:**
- Extended CLI + completions
- `hiveplane ask` copilot workload + certification corpus
- Incident mode control + `docs/design/operator-experience-design.md`, `docs/design/operator-experience-design.md`

**Acceptance criteria:**
- [x] `hiveplane ask` answers 5 live-state questions correctly, itself under budget + certification
- [x] `ask` cannot mutate state without explicit confirmation
- [x] Incident mode halts the fleet in <5s and broadcasts to owners
- [x] Recovery resumes the fleet and records the incident
- [x] `health`, `cost`, `report`, `replay`, `top`, `logs` all return real data

**Done when:** operators have a fast, safe CLI plus a copilot that proves the certification thesis by using it, and an emergency stop that works instantly.

> **Status:** M53 implemented. New `hiveplane.incident` package (durable `incidents`
> table, migration `0028`; `IncidentService` with pause/resume, fleet/tenant/workload
> scope, trigger drain, owner broadcast, fail-closed halt; `IncidentHaltGate` wired into
> `AdmissionPipeline`; `POST /fleet/pause|resume`, `GET /fleet/state`; `hiveplane fleet
> pause|resume|status`; UI big-red-button + incident banner). New `hiveplane.ask` package
> (`AskService` read-only NL Q&A with deterministic intent routing and per-query audit;
> `ServiceReaders` over live control-plane services, tenant-scoped; `POST /ask`;
> `hiveplane ask`; `ask` shipped as a first-class workload manifest (+ `run`
> entrypoint) with a fixed 5-question corpus). CLI depth:
> top-level `report`, `top`, `logs`, `replay` commands (thin API clients; run-level
> fork/replay is M60); shell completions exposed by Typer.
>
> **M53 exit gate — green.** With Postgres: `pytest` → 2305 passed, 2 skipped,
> 42 deselected (e2e/docker); coverage total 95%; `ruff check` clean;
> `mypy src/ tests/` → no issues in 632 files. Issues #407–#414 done and closed;
> docs (WBS, `USER_GUIDE.md`, D36, `CHANGELOG.md`) updated; changes committed and
> pushed.

**Dependencies:** M42 (health), M50 (cost), M40 (policy), v0.1.0 certification.

**Notes / risks:** `ask` must never become an unaudited backdoor to mutate state — read-only by default, every query attributed. Incident mode must bypass normal propagation to be truly instant.

## M54 — Artifact Store, Retention & Export/Import

**Objective:** Persist run artifacts (files/reports agents produce) with retention policies and links in fan-out/UI, and support portable export/import bundles for workloads, corpora, and policy packs.

**Work items:**

- [x] [#415](https://github.com/deghosal-2026/hiveplane/issues/415) — M54-01 — Artifact store: local backend + S3/MinIO backend
- [x] [#416](https://github.com/deghosal-2026/hiveplane/issues/416) — M54-02 — Artifact capture: link artifacts to runs/pipelines; store content-addressed with hashes
- [x] [#417](https://github.com/deghosal-2026/hiveplane/issues/417) — M54-03 — Retention policies: per-tenant/per-artifact retention; scheduled purge
- [x] [#418](https://github.com/deghosal-2026/hiveplane/issues/418) — M54-04 — Artifact links in fan-out payloads + UI run detail
- [x] [#419](https://github.com/deghosal-2026/hiveplane/issues/419) — M54-05 — `hiveplane export/import` bundles: manifest + corpus + policy pack (+ provenance signatures from M35)
- [x] [#420](https://github.com/deghosal-2026/hiveplane/issues/420) — M54-06 — Import safety: signature verification, conflict handling, dry-run
- [x] [#421](https://github.com/deghosal-2026/hiveplane/issues/421) — M54-07 — Tests: artifact stored + retrieved + linked; retention purge removes expired; export/import round-trips; tampered import refused

**Test ticket:** [x] [#422](https://github.com/deghosal-2026/hiveplane/issues/422) — Test cases for Artifact Store, Retention & Export/Import

**Deliverables:**
- `hiveplane.artifacts` package (local + S3/MinIO)
- Export/import CLI
- `docs/design/operator-experience-design.md`, `docs/design/operator-experience-design.md`

**Acceptance criteria:**
- [x] An agent-produced artifact is stored, linked in fan-out/UI, and retrievable
- [x] Retention purge deletes expired artifacts on schedule and leaves audit evidence
- [x] An export bundle round-trips to another plane with provenance verified
- [x] A tampered import is refused
- [x] Artifact storage works against MinIO/S3 in the reference stack

**Done when:** agent outputs are durable, retained per policy, and portable between planes with provenance.

> **Status:** M54 implemented. New `hiveplane.artifacts` package: `LocalBlobBackend`
> and an injectable `S3BlobBackend` (boto3/MinIO, path-style) selected by
> `ArtifactSettings`; a metadata `ArtifactStore` (in-memory + Postgres over the
> existing `artifacts`/`retention_policies` tables — no new migration);
> `ArtifactService.capture` stores content-addressed bytes linked to a run;
> `RetentionService` applies per-tenant policies and purges expired artifacts with
> audit evidence (legal holds skipped). Portable `export_fleet_bundle` /
> `import_fleet_bundle` carry manifest + corpus + policy pack signed with Ed25519;
> import verifies signature and digest, plans create/update (never auto-executes),
> and supports dry-run. API `/artifacts`, `/artifacts/{id}[/content]`,
> `/retention/policies`, `/retention/purge`, `/export`, `/import`; CLI `artifacts
> list|show|content|put|purge`, `export`, `import`. Artifact links are added to the
> fan-out payload and the UI run detail. With Postgres: 2354 passed, 2 skipped;
> coverage 95%; ruff and mypy strict clean.

**Dependencies:** M35 (provenance), M25 (artifact model), v0.1.0 state store.

**Notes / risks:** artifact contents may contain sensitive data — retention and PII purge (M57) must coordinate. Import must never auto-execute imported workloads; register → certify → admit as usual.

## Exit Gate (M53, M54)

- [x] All tests in the system pass: `pytest`
- [x] Code coverage total > 95%
- [x] Ruff clean
- [x] Mypy strict clean
- [x] All relevant docs updated (CLI reference, ask copilot, incident mode, artifacts, export/import)
- [x] All M53–M54 issues done and closed
- [x] Commit and push changes

> **Exit gate green.** With Postgres: `pytest` → 2354 passed, 2 skipped, 42
> deselected (e2e/docker); coverage total 95%; `ruff check` clean;
> `mypy src/ tests/` → no issues in 646 files. Issues #407–#422 done and closed.

## See Also

- [PRD 05 Features](../../prd/05-features.md) — Operator Surface, Artifacts & Portability themes
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillars M, Q
- [v0.2.0 index](wbs-v0.2.0-index.md)
