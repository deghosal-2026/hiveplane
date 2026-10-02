# Running a Multi-Tenant Agent Fleet on Kubernetes

**Status:** draft · **Pillar:** tenancy, Helm, distributed workers, HA

## Thesis

One agent is a script; a fleet is a platform. HivePlane makes tenants, budgets, policy, keys,
and secrets first-class, then ships the whole stack to a k3d cluster from one Helm chart.

## Audience

Platform teams standing up an internal agent platform for multiple teams or business units.

## Outline

1. **The single tenant boundary.** Every table carries a `tenant_id`; every request becomes a
   `TenantContext` in one place. Cross-tenant reads look like "not found"; writes raise.
2. **Isolation you can attack.** Per-tenant budgets, policy packs, keys, and secrets; a viewer
   can never approve; an adversarial suite attacks each boundary.
3. **Encrypted secrets.** Envelope-encrypted at rest, injected only at the execution boundary,
   redacted from context/logs/traces/audit/fan-out/artifacts.
4. **Distributed workers.** Signed worker tokens, lease-based execution with fencing tokens,
   crash detection and lease-expiry reassignment.
5. **HA.** Leader election over a lease table with a fencing epoch — a second replica cannot
   double-reconcile.
6. **Ship it.** Helm chart for the full stack (API, UI, Postgres, Redis, telemetry), k3d
   reference runbook, backup/restore with signed manifests.

## Evidence to link

- Field test S23 (GitOps deregister/re-cert), worker reclaim scenarios
- [k3d reference deployment](../runbooks/k3d-reference-deploy.md) · [Operator runbook](../runbooks/operator-runbook.md)
- [Multi-tenant isolation plan](../plans/m58-multi-tenant-isolation.md)
