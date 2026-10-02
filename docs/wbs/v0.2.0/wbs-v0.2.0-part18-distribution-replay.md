# WBS v0.2.0 — Part 18: Distribution & Replay

**Milestones:** M59–M60 · **Part:** 18 of 19

## Goal

Ship the plane to real clusters with a hardened supply chain, and give operators time-travel debugging: frame-by-frame replay, run forking, and A/B replay.

## M59 — Helm, k3d, Backup/Restore, Air-Gapped, Homebrew, Signed Releases, Demo Profile & Federation

**Objective:** Provide a Helm chart verified on a k3d/kind reference cluster, control-plane backup/restore, an air-gapped install bundle, Homebrew distribution, signed/SBOM'd/cosign releases with SLSA-style provenance, a demo profile, and (stretch) federation.

**Work items:**

- [x] [#458](https://github.com/deghosal-2026/hiveplane/issues/458) — M59-01 — Helm chart for the full stack (API, UI, Postgres, Redis, telemetry) with sane defaults and values schema
- [x] [#459](https://github.com/deghosal-2026/hiveplane/issues/459) — M59-02 — k3d/kind reference deployment, verified end-to-end in CI or a documented runbook
- [x] [#460](https://github.com/deghosal-2026/hiveplane/issues/460) — M59-03 — Backup/restore: export/restore control-plane state (migrations, DR) with integrity checks
- [x] [#461](https://github.com/deghosal-2026/hiveplane/issues/461) — M59-04 — Air-gapped install bundle: offline images + seed data for restricted networks
- [x] [#462](https://github.com/deghosal-2026/hiveplane/issues/462) — M59-05 — Homebrew tap + PyPI distribution hardening
- [x] [#463](https://github.com/deghosal-2026/hiveplane/issues/463) — M59-06 — Release supply chain: SBOM generation, cosign-signed images, SLSA-style provenance
- [x] [#464](https://github.com/deghosal-2026/hiveplane/issues/464) — M59-07 — Demo profile: pre-baked datasets + seeded failure scenarios for screenshots/talks
- [x] [#465](https://github.com/deghosal-2026/hiveplane/issues/465) — M59-08 — Federation (stretch): register remote planes, aggregate fleet view behind a feature flag
- [x] [#466](https://github.com/deghosal-2026/hiveplane/issues/466) — M59-09 — Tests/docs: chart lints + templates render; k3d deploy smoke test; backup/restore round-trip; signature/provenance verification

**Test ticket:** [#467](https://github.com/deghosal-2026/hiveplane/issues/467) — Test cases for Helm, k3d, Backup/Restore, Air-Gapped, Homebrew, Signed Releases, Demo Profile & Federation

**Deliverables:**
- `deploy/helm/` chart + values schema; k3d runbook
- Backup/restore tooling; air-gapped bundle script
- Signed release pipeline + SBOM/provenance artifacts
- `docs/design/reporting-tenancy-distribution-design.md`, `docs/design/reporting-tenancy-distribution-design.md` (stretch)

**Acceptance criteria:**
- [x] `helm install` deploys the full stack to a k3d cluster and the loop works end-to-end
- [x] Backup then restore recovers control-plane state intact
- [x] An air-gapped install completes with no internet access
- [x] Released images are cosign-signed and verify; SBOM + provenance are published
- [x] Homebrew install works (`brew install hiveplane`)
- [x] The demo profile seeds a working, screenshot-ready fleet
- [x] (Stretch) A remote plane registers and appears in the aggregate view

**Done when:** the plane installs to a real cluster, survives backup/restore, installs offline, and releases with a verifiable supply chain.

> **Status:** M59 complete. Helm chart (`deploy/helm/hiveplane`) + values schema
> and k3d runbook; signed backup/restore (`hiveplane backup`, `BackupService`);
> air-gap bundle + Homebrew formula + release supply chain workflow;
> demo profile (`hiveplane demo seed`); federation behind
> `HIVEPLANE_FEDERATION__ENABLED`. Chart/backup/demo/federation are covered by
> `tests/test_helm_chart.py`, `tests/test_backup.py`, `tests/test_demo.py`,
> `tests/test_federation.py`, and `tests/test_distribution.py`.

**Dependencies:** M46 (workers), M58 (tenancy), v0.1.0 Docker Compose.

**Notes / risks:** Helm values are a public contract — version the chart and document breaking changes. Signing/provenance must be reproducible in CI; do not sign ad hoc.

## M60 — State Diff/Replay, Run Forking & A/B Replay

**Objective:** Let operators replay a run frame-by-frame, fork a run (copy state → edit → re-run), diff runs against each other, and A/B replay two versions on the same input.

**Work items:**

- [x] [#468](https://github.com/deghosal-2026/hiveplane/issues/468) — M60-01 — Frame-by-frame replay: reconstruct a run's execution frames from checkpoints/events
- [x] [#469](https://github.com/deghosal-2026/hiveplane/issues/469) — M60-02 — Run-to-run diff: compare two runs (state, tool calls, model calls, cost, outcome)
- [x] [#470](https://github.com/deghosal-2026/hiveplane/issues/470) — M60-03 — Run forking: copy a run's state, allow edits, and re-run from that point
- [x] [#471](https://github.com/deghosal-2026/hiveplane/issues/471) — M60-04 — A/B replay: run two versions/configs on the same input and produce a side-by-side diff
- [x] [#472](https://github.com/deghosal-2026/hiveplane/issues/472) — M60-05 — Replay safety: replays never deliver results or trigger side-effecting tools by default
- [x] [#473](https://github.com/deghosal-2026/hiveplane/issues/473) — M60-06 — CLI (`hiveplane replay`) + UI diff viewer integration (M52)
- [x] [#474](https://github.com/deghosal-2026/hiveplane/issues/474) — M60-07 — Tests: replay reproduces frames deterministically; fork with edited state re-runs; A/B diff is correct; replays are side-effect-free

**Test ticket:** [#475](https://github.com/deghosal-2026/hiveplane/issues/475) — Test cases for State Diff/Replay, Run Forking & A/B Replay

**Deliverables:**
- `hiveplane.replay` package (replay, fork, diff, A/B)
- `docs/design/operator-experience-design.md`

**Acceptance criteria:**
- [x] A run is replayable frame-by-frame with a deterministic reconstruction
- [x] A forked run with edited state re-runs from the fork point
- [x] Run-to-run diff identifies meaningful differences
- [x] A/B replay produces a side-by-side comparison on identical input
- [x] Replays and forks do not deliver results or call destructive tools by default

**Done when:** operators can time-travel a run, fork it, and compare versions without side effects.

> **Status:** M60 complete. New `hiveplane.replay` package: `build_frames`
> (deterministic frame reconstruction with a content digest), `diff_runs`
> (state/tool/model/cost/latency/outcome), and `ReplayService` (`replay`, `diff`,
> `fork`, `ab`) over a tenant-scoped `ReplayStore` (in-memory + Postgres, migration
> `0035`, `replays` table). Replay/diff are side-effect-free; fork/A-B submit
> `sandbox` + `read_only=True` + `shadow_of` runs (destructive tools blocked, no
> fan-out) unless `side_effects=True`, which is recorded. Ships API
> (`POST /replay/{run_id}`, `GET /replays`, `GET /replay/diff`,
> `POST /runs/{run_id}/fork`, `POST /replay/ab`), a `REPLAY_MANAGE` permission
> (`replay:write` scope), CLI (`replay`, `replay-fork`, `replay-diff`, `replay-ab`),
> and UI (`/replay/{run_id}` page + `kind=replay` diff viewer). Issues #468–#475
> closed; 2888 tests pass, coverage 95%, ruff and mypy strict clean.

**Dependencies:** M33 (diff), M37 (shadow), v0.1.0 durable checkpoints.

**Notes / risks:** replay determinism depends on captured inputs — record all model/tool inputs. Forks must be clearly marked as non-production to avoid confusion.

## Exit Gate (M59, M60)

- [x] All tests in the system pass: `pytest`
- [x] Code coverage total > 95%
- [x] Ruff clean
- [x] Mypy strict clean
- [x] All relevant docs updated (Helm/k3d runbook, distribution, replay/fork, backup/restore)
- [x] All M59–M60 issues done and closed
- [x] Commit and push changes

## See Also

- [PRD 05 Features](../../prd/05-features.md) — Distribution & First Run, Run Lifecycle themes
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillars L, Q
- [v0.2.0 index](wbs-v0.2.0-index.md)
