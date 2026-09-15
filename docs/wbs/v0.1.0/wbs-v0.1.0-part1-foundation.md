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

- [x] [#6](https://github.com/deghosal-2026/hiveplane/issues/6) — Core domain models (workload, run, event, usage, decision)
- [x] [#7](https://github.com/deghosal-2026/hiveplane/issues/7) — Certification domain models (cert, attestation, corpus, task)
- [x] [#8](https://github.com/deghosal-2026/hiveplane/issues/8) — Trigger, tool, fan-out, and health models
- [x] [#9](https://github.com/deghosal-2026/hiveplane/issues/9) — Manifest parser, validator, and JSON Schema export
- [x] [#10](https://github.com/deghosal-2026/hiveplane/issues/10) — Example workload manifests

**Done when:** every model is typed and tested, manifests validate strictly, invalid manifests produce specific errors, and the three example workloads validate.

**Status:** Complete. Domain models under `hiveplane.core` and `hiveplane.certification`; `hiveplane validate` CLI; `GET /manifest/schema`; `docs/workloads/manifest.schema.json`; three example workloads under `examples/workloads/`. Tests pass, coverage 98.6%, ruff clean, mypy strict clean. Milestone M2 closed; issues #6-#10 closed.

## Dependencies

- None. This part unblocks everything else.

## Exit Gate (M1, M2)

> M1 passed this gate on commit `8ecab27`; M2 passed on the M2 commit. Part 1 is complete.

- [x] All tests in the system pass: `pytest`
- [x] Code coverage total > 95%
- [x] Ruff clean
- [x] Mypy strict clean
- [x] Update all relevant docs affected by this milestone
- [x] Verify all issues in this milestone are done
- [x] Close all completed issues
- [x] Commit and push changes

## See Also

- [Workload manifest design](../../design/workload-manifest-design.md)
- [Certification pipeline design](../../design/certification-pipeline-design.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)
