# WBS v0.1.0 — Part 1: Foundation & Data Models

**Milestones:** M1-M2

## Goal

Stand up the project skeleton, core domain models, and the workload manifest contract that everything else depends on.

## M1 — Project Skeleton

- [ ] Package layout (`src/hiveplane/`), `pyproject.toml`, dev tooling (ruff, mypy, pytest)
- [ ] CI workflow (lint, type, test)
- [ ] Docker Compose skeleton (API, PostgreSQL, Redis, OTel Collector)
- [ ] Configuration module and environment overrides

## M2 — Domain Models & Manifest

- [ ] Pydantic models for `AgentWorkload`, `Run`, `RunEvent`, `UsageReport`, `PolicyDecision`
- [ ] Manifest parser + validator (see [D1](../../design/workload-manifest-design.md))
- [ ] Manifest JSON Schema export
- [ ] Example workload manifests under `examples/workloads/`

## Exit Criteria

- [ ] `hiveplane validate <manifest>` accepts a valid manifest and rejects an invalid one
- [ ] Tests cover manifest validation rules
- [ ] Exit gate checklist passed

## See Also

- [Workload manifest design](../../design/workload-manifest-design.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)
