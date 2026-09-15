# WBS v0.1.0 — Part 2: Registry Service

**Milestones:** M3-M4 · **Issues:** #11-#18

## Goal

Desired state for agent workloads: register, validate, version, list, enforce certification status, and store attestations, triggers, and tools.

## M3 — Registry CRUD & Validation

**Issues:** [#11](https://github.com/deghosal-2026/hiveplane/issues/11) · [#12](https://github.com/deghosal-2026/hiveplane/issues/12) · [#13](https://github.com/deghosal-2026/hiveplane/issues/13)

- [x] [#11](https://github.com/deghosal-2026/hiveplane/issues/11) — Workload CRUD endpoints
- [x] [#12](https://github.com/deghosal-2026/hiveplane/issues/12) — Fleet catalog listing and filters
- [x] [#13](https://github.com/deghosal-2026/hiveplane/issues/13) — Dry-run registration

**Done when:** full CRUD round-trips, invalid manifests return 422 with details, filters work, and dry-run reports enforcement without persisting.

**Status:** Complete. `hiveplane.registry` (models, store, service) + `/workloads` CRUD, catalog filters/pagination, and dry-run via `POST /workloads?dry_run=true` and `hiveplane register --dry-run`. Storage is an in-memory `RegistryStore`; PostgreSQL lands in M18 (Part 9).

## M4 — Versioning, Certification Status & Admission

**Issues:** [#14](https://github.com/deghosal-2026/hiveplane/issues/14) · [#15](https://github.com/deghosal-2026/hiveplane/issues/15) · [#16](https://github.com/deghosal-2026/hiveplane/issues/16) · [#17](https://github.com/deghosal-2026/hiveplane/issues/17) · [#18](https://github.com/deghosal-2026/hiveplane/issues/18)

- [x] [#14](https://github.com/deghosal-2026/hiveplane/issues/14) — Append-only manifest versioning
- [x] [#15](https://github.com/deghosal-2026/hiveplane/issues/15) — `certification_status` lifecycle and admission enforcement
- [x] [#16](https://github.com/deghosal-2026/hiveplane/issues/16) — Attestation storage and retrieval
- [x] [#17](https://github.com/deghosal-2026/hiveplane/issues/17) — Trigger-rule and MCP tool registry storage
- [x] [#18](https://github.com/deghosal-2026/hiveplane/issues/18) — Re-certification requirement on manifest change

**Done when:** updates create versions; uncertified workloads are refused for production; attestations are immutable and verified on read; unknown/ untrusted tools are rejected; cert-relevant manifest changes require re-certification.

**Status:** Complete. Append-only versions with `/versions` and `/versions/diff`; `certification_status` events + admission enforcement per context (`/admission`); immutable attestations with Ed25519 signing verified on read (`/attestations`); MCP tool registry (`/tools`) with unknown-tool and destructive-approval rejection; re-certification requirement with promotion blocking (`/promote`).

## Dependencies

- Part 1 (models + manifest parser)

## Exit Gate (M3, M4)

> M3 and M4 passed this gate on the M3-M4 commit. Part 2 is complete.

- [x] All tests in the system pass: `pytest`
- [x] Code coverage total > 95%
- [x] Ruff clean
- [x] Mypy strict clean
- [x] Update all relevant docs affected by this milestone
- [x] Verify all issues in this milestone are done
- [x] Close all completed issues
- [x] Commit and push changes

## See Also

- [Registry service design](../../design/registry-service-design.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)
