# WBS v0.1.0 — Part 1: Foundation & Data Models

**Milestones:** M1-M2 · **Issues:** #2-#10

## Goal

Stand up the HivePlane package skeleton, developer tooling, the local stack, and every domain model the rest of the system depends on — including certification, triggers, and tools.

## M1 — Project Skeleton

**Issues:** [#2](https://github.com/deghosal-2026/hiveplane/issues/2) · [#3](https://github.com/deghosal-2026/hiveplane/issues/3) · [#4](https://github.com/deghosal-2026/hiveplane/issues/4) · [#5](https://github.com/deghosal-2026/hiveplane/issues/5)

- [x] [#2](https://github.com/deghosal-2026/hiveplane/issues/2) — Scaffold package layout, `pyproject.toml`, and dev tooling
- [x] [#3](https://github.com/deghosal-2026/hiveplane/issues/3) — CI workflow (lint, type, test, coverage gate)
- [x] [#4](https://github.com/deghosal-2026/hiveplane/issues/4) — Docker Compose reference stack skeleton
- [x] [#5](https://github.com/deghosal-2026/hiveplane/issues/5) — Configuration module with environment overrides

**Done when:** `pip install -e ".[dev]"` works, CI is green, `docker compose up -d` is healthy, and config loads from env with clear errors.

**Status:** Complete (commit `8ecab27`). `pyproject.toml` + package layout, CI workflow, Docker Compose stack (7 services healthy), and typed `hiveplane.config`. Tests pass, coverage 100%, ruff clean, mypy strict clean. Milestone M1 closed; issues #2-#5 closed.

## M2 — Domain Models

**Issues:** [#6](https://github.com/deghosal-2026/hiveplane/issues/6) · [#7](https://github.com/deghosal-2026/hiveplane/issues/7) · [#8](https://github.com/deghosal-2026/hiveplane/issues/8) · [#9](https://github.com/deghosal-2026/hiveplane/issues/9) · [#10](https://github.com/deghosal-2026/hiveplane/issues/10)

- [ ] [#6](https://github.com/deghosal-2026/hiveplane/issues/6) — Core domain models (workload, run, event, usage, decision)
- [ ] [#7](https://github.com/deghosal-2026/hiveplane/issues/7) — Certification domain models (cert, attestation, corpus, task)
- [ ] [#8](https://github.com/deghosal-2026/hiveplane/issues/8) — Trigger, tool, fan-out, and health models
- [ ] [#9](https://github.com/deghosal-2026/hiveplane/issues/9) — Manifest parser, validator, and JSON Schema export
- [ ] [#10](https://github.com/deghosal-2026/hiveplane/issues/10) — Example workload manifests

**Done when:** every model is typed and tested, manifests validate strictly, invalid manifests produce specific errors, and the three example workloads validate.

## Dependencies

- None. This part unblocks everything else.

## Exit Gate (M1, M2)

> M1 passed this gate on commit `8ecab27`. Boxes are checked when M2 completes, since the gate is re-run against the full system before Part 2 begins.

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] Update all relevant docs affected by this milestone
- [ ] Verify all issues in this milestone are done
- [ ] Close all completed issues
- [ ] Commit and push changes

## See Also

- [Workload manifest design](../../design/workload-manifest-design.md)
- [Certification pipeline design](../../design/certification-pipeline-design.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)
