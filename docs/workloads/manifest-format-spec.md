# Agent Workload Manifest — Format Specification

> Status: draft (v1). The manifest is the stable contract between HivePlane and adapters.

## Envelope

```yaml
apiVersion: hiveplane/v1
kind: AgentWorkload
metadata:
  name: string          # required, unique, DNS-safe
  owner: string         # required, owning team
  description: string   # optional
spec:                   # required
  runtime: {}
  tools: {}
  budget: {}
  approvals: {}
  model: {}
  observability: {}
```

## `spec.runtime`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `adapter` | string | yes | `raw-worker` or `langgraph` in v0.1.0 |
| `entrypoint` | string | yes | module:callable for the worker/graph |
| `env` | map | no | non-secret environment values |

## `spec.tools`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `allow` | list[string] | no | explicit allowed tool IDs |
| `deny` | list[string] | no | explicit denied tool IDs; deny wins |

If neither is set, the default is **deny all**.

## `spec.budget`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `per_run_usd` | number | yes | > 0 |
| `per_day_usd` | number | yes | >= `per_run_usd` |

## `spec.approvals`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `required_for` | list[string] | no | action classes, e.g. `destructive` |
| `contact` | string | no | escalation target (e.g. a Slack channel) |

## `spec.model`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `strategy` | string | no | `fixed` \| `tiered` \| `router` |

## `spec.observability`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `contract` | string | no | e.g. `standard` |

## Validation Summary

- `metadata.name` unique and DNS-safe
- `tools.deny` takes precedence over `tools.allow`
- budgets positive; `per_day_usd >= per_run_usd`
- `required_for` classes must be known
- unknown fields rejected (strict schema)

## Versioning

`apiVersion` is `hiveplane/v1`. Future schema versions will ship with a migration note.
