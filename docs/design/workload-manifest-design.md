# D1: Workload Manifest Design

> Status: draft.

## Problem

HivePlane needs one declarative contract that describes any agent workload without leaking runtime specifics. The manifest is the stable interface between the control plane and adapters (DD-01).

## Manifest Shape

```yaml
apiVersion: hiveplane/v1
kind: AgentWorkload
metadata:
  name: example-agent
  owner: platform-team
spec:
  runtime:
    adapter: raw-worker        # or langgraph
    entrypoint: examples.worker:run
  tools:
    allow:
      - github.read_issue
      - slack.post_message
    deny:
      - github.delete_repo
  budget:
    per_run_usd: 0.50
    per_day_usd: 5.00
  approvals:
    required_for:
      - destructive
    contact: "#platform-oncall"
  model:
    strategy: tiered           # or fixed | router
  observability:
    contract: standard
```

## Fields

| Field | Purpose |
|-------|---------|
| `metadata.name` | Unique workload identity |
| `metadata.owner` | Owning team (for fleet review and escalation) |
| `spec.runtime` | Adapter type + entrypoint |
| `spec.tools` | Explicit allow/deny tool permissions (deny by default) |
| `spec.budget` | Per-run and per-day spend ceilings |
| `spec.approvals` | Which action classes require human approval, and whom to contact |
| `spec.model` | Model selection strategy |
| `spec.observability` | Observability contract to honor |

## Validation Rules

- names are unique and DNS-safe
- `tools.deny` wins over `tools.allow`
- budgets are positive and per-day ≥ per-run
- every `required_for` class is a known action class

## Open Questions

- version of the manifest schema and migration strategy
- how much runtime config belongs in `spec.runtime` before it becomes framework-specific
