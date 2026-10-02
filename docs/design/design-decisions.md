# Design Decisions

Centralized log of significant HivePlane design decisions, referenced as DD-NN.

| DD | Decision | Status |
|----|----------|--------|
| DD-01 | The workload manifest is the stable contract; control-plane concepts must not leak runtime specifics | accepted |
| DD-02 | Runtimes sit behind adapters; HivePlane must not become a framework wrapper | accepted |
| DD-03 | Policy is evaluated at the control-plane boundary, visible in config — not hidden in agent code | accepted |
| DD-04 | Budgets are enforced per run and per day, at the point of task admission and tool call | accepted |
| DD-05 | PostgreSQL is the system of record for desired state, run state, and audit history | accepted |
| DD-06 | Telemetry is OpenTelemetry-native; traces, metrics, and logs share run correlation IDs | accepted |
| DD-07 | Every operator action is auditable and attributable | accepted |
| DD-08 | The system must be useful locally (Docker Compose) before it claims scale | accepted |
| DD-09 | Production admission requires valid, unexpired certification; uncertified agents are refused at the gate | accepted |
| DD-10 | Certifications are signed attestations binding benchmark version, model identity, and environment | accepted |
| DD-11 | Manifest changes require re-certification before promotion; regressions are blocked | accepted |
| DD-12 | Drift is measured against the agent's own certification baseline, not a global standard | accepted |
| DD-13 | Tool outputs are shaped at the boundary (filter, truncate, budget), not inside the agent | accepted |
| DD-14 | Destructive runs execute in an isolated sandbox with resource caps and restricted egress | accepted |
| DD-15 | Certification is necessary but not sufficient — budget, policy, sandbox, and approvals remain enforced at runtime | accepted |
| DD-16 | Resource names are globally unique; tools are platform-shared for reads with tenant-owned writes (#589) | accepted |

## Format

Each new decision adds a row above and a section below:

```markdown
## DD-NN: <title>

**Context:** ...
**Decision:** ...
**Consequences:** ...
**Alternatives considered:** ...
```

## DD-16: Resource naming and tenancy scope

**Context:** v0.2.0 added multi-tenancy (M58). Some resource names are natural keys used as
primary keys — `workloads.name`, `tools.tool_id` — which makes them globally unique across
tenants. The field test hit this as defect **D-6** (M61-20, #589): a second tenant could neither
see nor re-register a platform tool, and two tenants could not own the same workload name.

**Decision:** Keep natural keys globally unique and define the scope per resource class:

| Resource | Key | Scope |
|---|---|---|
| workload | `name` | globally unique (one owner); cross-tenant re-registration → `WorkloadAlreadyExistsError` |
| tool | `tool_id` | **platform-shared read** (visible to all tenants); ownership governs modification |
| trigger | `(workload, id)` | per-tenant, scoped by the owning workload |
| secret | `name` | per-tenant (no cross-tenant resolve) |
| policy pack | `name` | per-tenant (same name allowed in two tenants) |

**Consequences:** Platform tools are usable by every tenant's workloads without re-registration;
a tenant cannot shadow another tenant's workload name (a deliberate, documented limitation, not a
bug). Where per-tenant naming is required, the resource uses a composite/tenant-qualified key
(secrets, policy packs, triggers).

**Alternatives considered:** (a) migrate `workloads`/`tools` to composite `(tenant, name)` keys —
deferred as a large migration with FK churn (`runs_workload_id_tenant_id_fkey`) and no current
requirement; (b) fully per-tenant tools — rejected because the platform ships a shared MCP tool
catalogue. Revisit if a tenant must own a private same-named workload/tool.

**Tests:** `tests/test_tenant_scoping_isolation.py` —
`test_tools_are_platform_global`, `test_policy_pack_store_allows_the_same_name_in_two_tenants`,
`test_registry_store_rejects_cross_tenant_name_collision`.
