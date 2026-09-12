# WBS v0.1.0 — Part 2: Registry Service

**Milestones:** M3-M4 · **Issues:** #11-#18

## Goal

Desired state for agent workloads: register, validate, version, list, enforce certification status, and store attestations, triggers, and tools.

## M3 — Registry CRUD & Validation

**Issues:** [#11](https://github.com/deghosal-2026/hiveplane/issues/11) · [#12](https://github.com/deghosal-2026/hiveplane/issues/12) · [#13](https://github.com/deghosal-2026/hiveplane/issues/13)

- [ ] [#11](https://github.com/deghosal-2026/hiveplane/issues/11) — Workload CRUD endpoints
- [ ] [#12](https://github.com/deghosal-2026/hiveplane/issues/12) — Fleet catalog listing and filters
- [ ] [#13](https://github.com/deghosal-2026/hiveplane/issues/13) — Dry-run registration

**Done when:** full CRUD round-trips, invalid manifests return 422 with details, filters work, and dry-run reports enforcement without persisting.

## M4 — Versioning, Certification Status & Admission

**Issues:** [#14](https://github.com/deghosal-2026/hiveplane/issues/14) · [#15](https://github.com/deghosal-2026/hiveplane/issues/15) · [#16](https://github.com/deghosal-2026/hiveplane/issues/16) · [#17](https://github.com/deghosal-2026/hiveplane/issues/17) · [#18](https://github.com/deghosal-2026/hiveplane/issues/18)

- [ ] [#14](https://github.com/deghosal-2026/hiveplane/issues/14) — Append-only manifest versioning
- [ ] [#15](https://github.com/deghosal-2026/hiveplane/issues/15) — `certification_status` lifecycle and admission enforcement
- [ ] [#16](https://github.com/deghosal-2026/hiveplane/issues/16) — Attestation storage and retrieval
- [ ] [#17](https://github.com/deghosal-2026/hiveplane/issues/17) — Trigger-rule and MCP tool registry storage
- [ ] [#18](https://github.com/deghosal-2026/hiveplane/issues/18) — Re-certification requirement on manifest change

**Done when:** updates create versions; uncertified workloads are refused for production; attestations are immutable and verified on read; unknown/ untrusted tools are rejected; cert-relevant manifest changes require re-certification.

## Dependencies

- Part 1 (models + manifest parser)

## Exit Gate (M3, M4)

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] Update all relevant docs affected by this milestone
- [ ] Verify all issues in this milestone are done
- [ ] Close all completed issues
- [ ] Commit and push changes

## See Also

- [Registry service design](../../design/registry-service-design.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)
