# WBS v0.1.0 — Part 2: Registry Service

**Milestones:** M3-M4

## Goal

Desired state: register, validate, version, and list agent workloads.

## M3 — Registry CRUD

- [ ] `POST/GET/PUT/DELETE /workloads` endpoints
- [ ] Manifest validation on write
- [ ] Uniqueness and DNS-safe name enforcement
- [ ] Fleet catalog list with owner/team filters

## M4 — Manifest Versioning

- [ ] Append-only manifest history
- [ ] Version listing endpoint
- [ ] Deregistration guards when active runs reference a workload

## Exit Criteria

- [ ] A workload can be registered, fetched, updated, and versioned
- [ ] Invalid manifests are rejected with actionable errors
- [ ] Exit gate checklist passed

## See Also

- [Registry service design](../../design/registry-service-design.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)
