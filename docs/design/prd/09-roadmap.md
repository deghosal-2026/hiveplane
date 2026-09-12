# PRD 09: Roadmap

## TLDR

Four versions take HivePlane from a proven control loop to a governed, multi-runtime, multi-tenant fleet platform.

## Timeline

| Version | Theme | Focus |
|---------|-------|-------|
| v0.1.0 | Prove the control loop | Registry, run lifecycle, budget enforcement, pause/resume, audit, minimal UI |
| v0.2.0 | Governance surface | Approval queue UI, richer policy, budget analytics, stronger adapter contract |
| v0.3.0 | Multi-runtime & reliability | Multi-runtime, state diff/replay, reliability metrics and SLO hooks |
| v0.4.0 | Scale & tenancy | Multi-tenant, ROI dashboards, Helm chart and reference cluster |

## v0.1.0 Scope Boundary

**Done when:** three real agents run through the same lifecycle and operators can meaningfully inspect and stop them from one place.

## Release Cadence

Each version ships with: release notes (`docs/release/`), field test report (`docs/field-test/`), and an updated WBS (`docs/wbs/`).

## See Also

- [Features](05-features.md)
- [Success metrics](07-success-metrics.md)
- [v0.1.0 WBS](../../wbs/v0.1.0/wbs-v0.1.0-index.md)
