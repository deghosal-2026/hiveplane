# Agent Workload Manifest — Format Specification

> Status: v1 (aligned to the [workload manifest design](../design/workload-manifest-design.md) and the implementation in `hiveplane.core`). The authoritative machine-readable schema is published at [`manifest.schema.json`](manifest.schema.json) and served at `GET /manifest/schema`. Validate locally with `hiveplane validate <manifest>`.

The manifest is the stable contract between HivePlane and runtime adapters (DD-01). It declares a workload's runtime, model identity, certification, tools, budget, approvals, triggers, sandbox, output shaping, fan-out, health, and observability. Validation is strict: unknown fields are rejected.

## Design Principles

- **Production is earned, not assumed** — the manifest carries certification status; production contexts require `certified`.
- **The contract stays stable** — runtime-specific fields never leak into core spec.
- **Policy is visible, not hidden in code** — tool permissions, budgets, approvals, and triggers are declarative.
- **Tool outputs are shaped at the boundary** — the manifest declares shaping rules, not the agent.
- **Certifications bind to model identity** — a change to `spec.model.identity` requires re-certification.

## Envelope

```yaml
apiVersion: hiveplane/v1
kind: AgentWorkload
metadata:
  name: incident-agent
  owner: platform-team
  team: platform
  description: Triages alerts and proposes remediation
  labels:
    environment: production
    criticality: high
spec:
  runtime: {}
  model: {}
  certification: {}
  tools: {}
  budget: {}
  approvals: {}
  triggers: []
  sandbox: {}
  output_shaping: {}
  fan_out: {}
  health: {}
  observability: {}
```

## Field Reference

### `metadata`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `name` | string | yes | Unique, DNS-safe (`[a-z0-9]([-a-z0-9]*[a-z0-9])?`), max 63 chars |
| `owner` | string | yes | Owning team — used for fleet review and escalation |
| `team` | string | no | Team for cost attribution and policy-pack binding |
| `description` | string | no | Human-readable summary |
| `labels` | map&lt;string, string&gt; | no | Arbitrary labels for filtering and policy context |

### `spec.runtime`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `adapter` | string | yes | `raw-worker` or `langgraph` in v0.1.0 |
| `entrypoint` | string | yes | `module:callable` for the worker or compiled graph |
| `env` | map&lt;string, string&gt; | no | Non-secret environment values; secrets are referenced, never inlined |

### `spec.model`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `strategy` | string | no | `fixed`, `tiered` (default), or `router` |
| `identity.provider` | string | when certification present | e.g. `openai` |
| `identity.family` | string | when certification present | e.g. `gpt-4o` |
| `identity.version` | string | when certification present | e.g. `2024-08-06` |

`model.identity` is **required whenever `spec.certification` is present**, because a certification binds to an exact model identity (DD-10). At runtime, a model swap that does not match the attestation blocks the run (T11).

### `spec.certification`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `benchmark_corpus` | string | no | Reference to the versioned benchmark corpus |
| `staging_threshold` | number (0–1) | no | Pass rate to earn `provisional` (default 0.80) |
| `production_threshold` | number (0–1) | no | Pass rate to earn `certified` (default 0.90); must be ≥ `staging_threshold` |
| `no_critical_failures` | boolean | no | If true, any critical task failure blocks certification (default true) |
| `latency_budget_ms` | integer &gt; 0 | no | Per-task latency ceiling (default 30000) |
| `re_cert_interval` | duration | no | Drift-detection cadence (default `14d`) |
| `status` | enum | no | `uncertified` (default), `provisional`, `certified`, `quarantined` |
| `attestation_id` | string | when `certified` | Reference to the signed attestation |
| `certified_at` | timestamp | no | Last successful certification |
| `certified_by` | string | no | Identity that performed certification |
| `expires_at` | timestamp | when `certified` | Must be in the future |

**Status lifecycle:** `uncertified` (sandbox only) → `provisional` (staging) → `certified` (production); failed re-certification or drift moves a workload to `quarantined`, which blocks runs until re-certification passes.

### `spec.tools`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `allow[]` | list&lt;ToolRef&gt; | no | Explicitly allowed tools |
| `deny[]` | list&lt;string&gt; | no | Denied tool IDs; **deny wins** over allow |
| `mcp_servers[]` | list&lt;McpServer&gt; | no | `{name, endpoint}` MCP servers served by mcp-fabric |
| `default_trust` | string | no | `read_only` (default) or `destructive` |

**ToolRef:** `tool_id` (string, required), `trust_level` (`read_only` or `destructive`, required), `require_approval` (boolean, default false).

If neither `allow` nor `deny` is set, the default is **deny all**.

### `spec.budget`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `per_run_usd` | number &gt; 0 | yes | Per-run spend ceiling |
| `per_day_usd` | number &gt; 0 | yes | Must be ≥ `per_run_usd` |
| `per_team_usd` | number &gt; 0 | no | Must be ≥ `per_day_usd` |

### `spec.approvals`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `required_for` | list&lt;string&gt; | no | Action classes requiring approval: `read_only`, `destructive`, `production_write`, `high_spend` |
| `contact` | string | no | Escalation target (e.g. `#platform-oncall`) |
| `auto_escalate_after` | duration | no | Auto-escalation timeout (default `300s`) |

### `spec.triggers[]`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `type` | string | yes | `webhook`, `alert`, `github_pr`, or `cron` |
| `url` | string | for `webhook` | Hook path |
| `source` | string | for `alert` | e.g. `pagerduty` |
| `events` | list&lt;string&gt; | for `github_pr` | e.g. `[opened, synchronize]` |
| `schedule` | string | for `cron` | Cron expression |
| `match` | Match | for non-cron | At least one of `severity`, `service`, `paths` |
| `mode` | string | no | `one-shot`, `watch`, or `scheduled` |
| `max_concurrent` | integer ≥ 1 | no | Cap on concurrent trigger-originated runs |

### `spec.sandbox`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `enabled` | boolean | no | Default true |
| `resource_caps` | ResourceCaps | when enabled | Required when `enabled` is true |
| `egress.allow` | list&lt;string&gt; | no | Allowed outbound hostnames |
| `egress.deny` | list&lt;string&gt; | no | Denied hostnames; cloud metadata endpoints are always denied |
| `egress.mode` | string | no | `open`, `restricted` (default), or `none` |
| `filesystem` | string | no | `isolated` (default) |

**ResourceCaps:** `memory_mb` (integer &gt; 0), `cpu_cores` (number &gt; 0), `wall_clock_s` (integer &gt; 0).

### `spec.output_shaping`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `max_bytes` | integer &gt; 0 | yes | Max bytes of tool output allowed into agent context |
| `truncate_strategy` | string | no | `head` (default), `tail`, or `summary` |
| `filter_rules[]` | list&lt;FilterRule&gt; | no | `{pattern (valid regex), action}` |
| `injection_scan` | boolean | no | Scan tool output for injection patterns (default true) |

`action` is `redact` or `mask`.

### `spec.fan_out`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `on_completed` | list&lt;Destination&gt; | no | Notified on completion |
| `on_failed` | list&lt;Destination&gt; | no | Notified on failure |
| `on_escalation` | list&lt;Destination&gt; | no | Notified on escalation |
| `always_include` | list&lt;string&gt; | no | e.g. `[trace_link, attestation_link]` |

**Destination:** `type` (`slack`, `teams`, `jira`, `webhook`), plus type-specific fields:

| Type | Required fields |
|------|-----------------|
| `slack` | `channel` |
| `teams` | `channel` |
| `jira` | `project`, `issue_type` |
| `webhook` | `url` |

### `spec.health`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `readiness_probe.type` | string | no | `dry_run` (default) |
| `readiness_probe.interval` | duration | no | Default `60s` |
| `slo.availability_target` | number (0–1] | no | Default 0.99 |
| `slo.quality_target` | number (0–1] | no | Default 0.90 |
| `slo.error_budget_window` | duration | no | Default `24h` |
| `failure_rate_threshold` | number [0–1] | no | Auto-quarantine trigger (default 0.15) |

### `spec.observability`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `contract` | string | no | Observability contract name (default `standard`) |
| `trace_sampling` | number (0–1] | no | Trace sampling rate (default 1.0) |

## Durations

Duration fields accept an integer number of seconds or a unit-suffixed string: `30s`, `15m`, `24h`, `14d`.

## Validation Rules

- unknown fields are rejected at every level (strict schema)
- `metadata.name` is DNS-safe and at most 63 characters
- `budget`: `per_team_usd >= per_day_usd >= per_run_usd`, all positive
- `certification.production_threshold >= certification.staging_threshold`
- `certification.status == certified` requires a non-empty `attestation_id` and a future `expires_at`
- `spec.model.identity` is required when `spec.certification` is present
- `tools.deny` wins over `tools.allow`; unlisted tools are denied
- triggers must satisfy their type-specific requirements and have a match criterion (or a schedule for `cron`)
- `sandbox.resource_caps` is required when `sandbox.enabled`; cloud metadata endpoints are always denied
- `output_shaping.max_bytes > 0`; filter-rule patterns must be valid regex
- fan-out destinations must satisfy their type-specific requirements
- `health.slo` targets are in `(0, 1]`; `failure_rate_threshold` is in `[0, 1]`

## Examples

Runnable examples live in [`examples/workloads/`](../../examples/workloads/): `repo-agent.yaml`, `docs-agent.yaml`, and `incident-agent.yaml`. Validate any of them:

```bash
hiveplane validate examples/workloads/incident-agent.yaml
```

## See Also

- [Workload manifest design](../design/workload-manifest-design.md)
- [JSON Schema](manifest.schema.json)
- [Certification pipeline design](../design/certification-pipeline-design.md)
- [Examples README](../../examples/workloads/README.md)
