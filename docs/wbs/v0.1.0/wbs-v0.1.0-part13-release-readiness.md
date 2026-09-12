# WBS v0.1.0 — Part 13: Release Readiness

**Milestone:** M24 · **Issues:** #60-#61

## Goal

Ship v0.1.0 with docs accurate, demo working, distribution verified, and the release published.

## M24 — Release Readiness

**Issues:** [#60](https://github.com/deghosal-2026/hiveplane/issues/60) · [#61](https://github.com/deghosal-2026/hiveplane/issues/61)

- [ ] [#60](https://github.com/deghosal-2026/hiveplane/issues/60) — Final release gate and docs accuracy pass
- [ ] [#61](https://github.com/deghosal-2026/hiveplane/issues/61) — Distribution, demo, and release

### Checklist

- [ ] Final release gate from the [index](wbs-v0.1.0-index.md) passed with evidence
- [ ] README, USER_GUIDE, ADAPTERS, observability docs match behavior
- [ ] Docker Compose one-command start verified on macOS, Linux, CI
- [ ] `hiveplane init` scaffolds a working project in under 5 minutes
- [ ] Seeded demo exercises register → certify → trigger → run → intervene → deliver
- [ ] `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md` published
- [ ] Release notes published (`docs/release/v0.1.0/release-notes.md`)
- [ ] Git tag `v0.1.0` and GitHub release created

**Done when:** a new user reaches a certified agent in under 5 minutes and v0.1.0 is published.

## Dependencies

- Part 12 (field test)

## Exit Gate (M24)

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] Update all relevant docs affected by this milestone
- [ ] Verify all issues in this milestone are done
- [ ] Close all completed issues
- [ ] Commit and push changes

## See Also

- [Release notes](../../release/v0.1.0/release-notes.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)
