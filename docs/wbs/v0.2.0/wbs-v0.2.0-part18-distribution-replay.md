# WBS v0.2.0 — Part 18: Distribution & Replay

**Milestones:** M59–M60 · **Part:** 18 of 19

## Goal

Ship the plane to real clusters with a hardened supply chain, and give operators time-travel debugging: frame-by-frame replay, run forking, and A/B replay.

## M59 — Helm, k3d, Backup/Restore, Air-Gapped, Homebrew, Signed Releases, Demo Profile & Federation

**Objective:** Provide a Helm chart verified on a k3d/kind reference cluster, control-plane backup/restore, an air-gapped install bundle, Homebrew distribution, signed/SBOM'd/cosign releases with SLSA-style provenance, a demo profile, and (stretch) federation.

**Work items:**

- [ ] [#458](https://github.com/deghosal-2026/hiveplane/issues/458) — M59-01 — Helm chart for the full stack (API, UI, Postgres, Redis, telemetry) with sane defaults and values schema
- [ ] [#459](https://github.com/deghosal-2026/hiveplane/issues/459) — M59-02 — k3d/kind reference deployment, verified end-to-end in CI or a documented runbook
- [ ] [#460](https://github.com/deghosal-2026/hiveplane/issues/460) — M59-03 — Backup/restore: export/restore control-plane state (migrations, DR) with integrity checks
- [ ] [#461](https://github.com/deghosal-2026/hiveplane/issues/461) — M59-04 — Air-gapped install bundle: offline images + seed data for restricted networks
- [ ] [#462](https://github.com/deghosal-2026/hiveplane/issues/462) — M59-05 — Homebrew tap + PyPI distribution hardening
- [ ] [#463](https://github.com/deghosal-2026/hiveplane/issues/463) — M59-06 — Release supply chain: SBOM generation, cosign-signed images, SLSA-style provenance
- [ ] [#464](https://github.com/deghosal-2026/hiveplane/issues/464) — M59-07 — Demo profile: pre-baked datasets + seeded failure scenarios for screenshots/talks
- [ ] [#465](https://github.com/deghosal-2026/hiveplane/issues/465) — M59-08 — Federation (stretch): register remote planes, aggregate fleet view behind a feature flag
- [ ] [#466](https://github.com/deghosal-2026/hiveplane/issues/466) — M59-09 — Tests/docs: chart lints + templates render; k3d deploy smoke test; backup/restore round-trip; signature/provenance verification

**Test ticket:** [#467](https://github.com/deghosal-2026/hiveplane/issues/467) — Test cases for Helm, k3d, Backup/Restore, Air-Gapped, Homebrew, Signed Releases, Demo Profile & Federation

**Deliverables:**
- `deploy/helm/` chart + values schema; k3d runbook
- Backup/restore tooling; air-gapped bundle script
- Signed release pipeline + SBOM/provenance artifacts
- `docs/design/reporting-tenancy-distribution-design.md`, `docs/design/reporting-tenancy-distribution-design.md` (stretch)

**Acceptance criteria:**
- [ ] `helm install` deploys the full stack to a k3d cluster and the loop works end-to-end
- [ ] Backup then restore recovers control-plane state intact
- [ ] An air-gapped install completes with no internet access
- [ ] Released images are cosign-signed and verify; SBOM + provenance are published
- [ ] Homebrew install works (`brew install hiveplane`)
- [ ] The demo profile seeds a working, screenshot-ready fleet
- [ ] (Stretch) A remote plane registers and appears in the aggregate view

**Done when:** the plane installs to a real cluster, survives backup/restore, installs offline, and releases with a verifiable supply chain.

**Dependencies:** M46 (workers), M58 (tenancy), v0.1.0 Docker Compose.

**Notes / risks:** Helm values are a public contract — version the chart and document breaking changes. Signing/provenance must be reproducible in CI; do not sign ad hoc.

## M60 — State Diff/Replay, Run Forking & A/B Replay

**Objective:** Let operators replay a run frame-by-frame, fork a run (copy state → edit → re-run), diff runs against each other, and A/B replay two versions on the same input.

**Work items:**

- [ ] [#468](https://github.com/deghosal-2026/hiveplane/issues/468) — M60-01 — Frame-by-frame replay: reconstruct a run's execution frames from checkpoints/events
- [ ] [#469](https://github.com/deghosal-2026/hiveplane/issues/469) — M60-02 — Run-to-run diff: compare two runs (state, tool calls, model calls, cost, outcome)
- [ ] [#470](https://github.com/deghosal-2026/hiveplane/issues/470) — M60-03 — Run forking: copy a run's state, allow edits, and re-run from that point
- [ ] [#471](https://github.com/deghosal-2026/hiveplane/issues/471) — M60-04 — A/B replay: run two versions/configs on the same input and produce a side-by-side diff
- [ ] [#472](https://github.com/deghosal-2026/hiveplane/issues/472) — M60-05 — Replay safety: replays never deliver results or trigger side-effecting tools by default
- [ ] [#473](https://github.com/deghosal-2026/hiveplane/issues/473) — M60-06 — CLI (`hiveplane replay`) + UI diff viewer integration (M52)
- [ ] [#474](https://github.com/deghosal-2026/hiveplane/issues/474) — M60-07 — Tests: replay reproduces frames deterministically; fork with edited state re-runs; A/B diff is correct; replays are side-effect-free

**Test ticket:** [#475](https://github.com/deghosal-2026/hiveplane/issues/475) — Test cases for State Diff/Replay, Run Forking & A/B Replay

**Deliverables:**
- `hiveplane.replay` package (replay, fork, diff, A/B)
- `docs/design/operator-experience-design.md`

**Acceptance criteria:**
- [ ] A run is replayable frame-by-frame with a deterministic reconstruction
- [ ] A forked run with edited state re-runs from the fork point
- [ ] Run-to-run diff identifies meaningful differences
- [ ] A/B replay produces a side-by-side comparison on identical input
- [ ] Replays and forks do not deliver results or call destructive tools by default

**Done when:** operators can time-travel a run, fork it, and compare versions without side effects.

**Dependencies:** M33 (diff), M37 (shadow), v0.1.0 durable checkpoints.

**Notes / risks:** replay determinism depends on captured inputs — record all model/tool inputs. Forks must be clearly marked as non-production to avoid confusion.

## Exit Gate (M59, M60)

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] All relevant docs updated (Helm/k3d runbook, distribution, replay/fork, backup/restore)
- [ ] All M59–M60 issues done and closed
- [ ] Commit and push changes

## See Also

- [PRD 05 Features](../../prd/05-features.md) — Distribution & First Run, Run Lifecycle themes
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillars L, Q
- [v0.2.0 index](wbs-v0.2.0-index.md)
