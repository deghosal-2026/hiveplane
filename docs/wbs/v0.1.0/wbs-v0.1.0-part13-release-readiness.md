# WBS v0.1.0 — Part 13: Release Readiness

**Milestone:** M24 · **Issues:** #60-#61, #145-#148 · **Target tag:** `v0.1.0` (alpha)

## Goal

Ship v0.1.0 with everything true: security-clean, all tests green, docs accurate, the
package on PyPI, the image published, and the GitHub release tagged.

> **Execution order:** M24.1 → M24.2 → M24.3 → M24.4 → M24.5 → M24.6.
> No item closes until the aggregate pre-release gate passes.

| Item | Issue | Title | Area | Status |
|------|-------|-------|------|--------|
| M24.1 | [#145](https://github.com/deghosal-2026/hiveplane/issues/145) | Security pass — git history audit + trufflehog secret scan | security | ⬜ |
| M24.2 | [#60](https://github.com/deghosal-2026/hiveplane/issues/60) | Final release gate — tests, CI, coverage, docs accuracy | testing/docs | ⬜ |
| M24.3 | [#146](https://github.com/deghosal-2026/hiveplane/issues/146) | Documentation refresh — CHANGELOG, release notes, README | docs | ⬜ |
| M24.4 | [#147](https://github.com/deghosal-2026/hiveplane/issues/147) | Publish to PyPI | release | ⬜ |
| M24.5 | [#61](https://github.com/deghosal-2026/hiveplane/issues/61) | Distribution — Docker image publish + demo verification | release | ⬜ |
| M24.6 | [#148](https://github.com/deghosal-2026/hiveplane/issues/148) | Tag and publish the GitHub release `v0.1.0` | release | ⬜ |

---

### M24.1 — Security pass — git history audit + trufflehog secret scan (#145)

**Problem:** Before tagging, the release must pass a secret/security scan. Any leaked
secret, vulnerable dependency, or security regression must be caught before the tag.

**Scope:** `trufflehog` over the full history; working-tree audit for keys/tokens/`.env`/
`*.pem`; `.gitignore`/`.dockerignore` verification; `pip-audit`; SECURITY.md review;
OpenSSF scorecard (if applicable).

**Checklist**
- [ ] `trufflehog git file://.` clean (or documented false positives)
- [ ] `pip-audit` clean on production dependencies (no unmitigated high/critical)
- [ ] `.gitignore`/`.dockerignore` verified; no key material tracked
- [ ] `SECURITY.md` current (threat model, disclosure policy)
- [ ] Scan results recorded in the release notes

### M24.2 — Final release gate — tests, CI, coverage, docs accuracy (#60)

**Problem:** Everything merged in M1-M24 must pass; docs must match behavior.

**Scope:** full deterministic suite, Docker integration on the rebuilt image, CI hermetic
pass (py3.12 + 3.13), coverage measurement, docs accuracy pass (README, USER_GUIDE,
ADAPTERS, observability, WBS).

**Checklist**
- [ ] `pytest`: 100% pass, zero unexplained skips
- [ ] Docker test track green against the rebuilt `v0.1.0` image
- [ ] CI hermetic pass on GitHub Actions (py3.12 + 3.13)
- [ ] Coverage recorded — **current 93%, target >95%**: raise or restate the gate
- [ ] Docs match behavior (no stale-doc contradictions)

### M24.3 — Documentation refresh — CHANGELOG, release notes, README (#146)

**Problem:** All user-facing docs must be current and pinned to v0.1.0.

**Scope / version-bump locations:** `pyproject.toml` (0.1.0), `src/hiveplane/__init__.py`
(`__version__`), `Dockerfile` pin, `README.md`, `CHANGELOG.md`, `docs/release/v0.1.0/`.

**Checklist**
- [ ] `CHANGELOG.md` created (Keep a Changelog) with a v0.1.0 entry
- [ ] `docs/release/v0.1.0/release-notes.md` — what's new, field-test results, known issues
- [ ] `README.md` — status (alpha), `pip install hiveplane`, quickstart, links
- [ ] `SECURITY.md`, `CONTRIBUTING.md` published
- [ ] Version consistent at all locations; no stale agent names/versions
- [ ] `docs/README.md` links the release artifacts

### M24.4 — Publish to PyPI (#147)

**Problem:** `pip install hiveplane` must work.

**Checklist**
- [ ] `python -m build` produces clean sdist + wheel
- [ ] Metadata complete (license, classifiers, readme, URLs, python-requires)
- [ ] Clean-venv install smoke test (`hiveplane --help`, `hiveplane init`)
- [ ] TestPyPI verified, then PyPI published (Trusted Publishing / token — never committed)
- [ ] `pip install hiveplane==0.1.0` works from PyPI
- [ ] Optional: `release.yml` workflow (build + publish on tag)

### M24.5 — Distribution — Docker image publish + demo verification (#61)

**Problem:** HivePlane ships as a Docker Compose stack; the release image must be published
and the one-command demo verified.

**Checklist**
- [ ] `Dockerfile` pinned to `v0.1.0`; image builds
- [ ] Image published to a registry (GHCR) and tagged `0.1.0` + `latest`
- [ ] Docker Compose one-command start verified on macOS, Linux, CI
- [ ] `hiveplane init` scaffolds a working project in < 5 minutes
- [ ] Seeded demo (`scripts/demo.sh`) exercises register → certify → run → intervene → deliver
- [ ] `scripts/field-test.sh` brings a stack to a certified run

### M24.6 — Release tags and milestone closure (#148)

**Prerequisites (all green):** M24.1 security · M24.2 tests · M24.3 docs · M24.4 PyPI ·
M24.5 distribution.

**Checklist**
- [ ] Annotated tag `v0.1.0` created and pushed
- [ ] GitHub release published with release notes, artifacts, and field-test report link
- [ ] Marked **pre-release** (alpha)
- [ ] All v0.1.0 milestones closed; no open v0.1.0 issues
- [ ] Deferred items remain open with documented rationale

---

## Deferred to v0.2.0 (not v0.1.0 blockers)

Distribution conveniences and platform breadth — tracked, not blocking the core release:

- Homebrew formula, standalone binary (PyInstaller), GitHub Action, shields.io badge
- Official benchmark leaderboard; pack certification baseline
- A21 Tier 2 platform coverage (>1 certified agent per framework)

## Exit Gate (M24)

Standard gate:
- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95% (current 93% — raise or restate)
- [ ] Ruff clean · Mypy strict clean
- [ ] Update all relevant docs affected by this milestone
- [ ] Verify all issues in this milestone are done
- [ ] Close all completed issues
- [ ] Commit and push changes

Pre-release aggregate gate (all must be green before M24.6 tags):
- [ ] Security: trufflehog clean, dependency audit clean
- [ ] Tests: full suite + Docker + CI green; coverage target resolved
- [ ] Docs: complete and consistent (no stale-doc contradictions)
- [ ] PyPI: v0.1.0 published with release-notes link
- [ ] Distribution: image published; demo verified

## Dependencies

- Part 12 (field test) — complete: S1-S10 all pass, A1-A20 all pass.
- See also: [Field test report](../../field-test/v0.1.0/FIELD_TEST_REPORT.md),
  [release notes](../../release/v0.1.0/release-notes.md).

## See Also

- [v0.1.0 index](wbs-v0.1.0-index.md)
- [Field test plan](../../field-test/v0.1.0/field-test-plan.md)
