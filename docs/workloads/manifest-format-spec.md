# Agent Workload Manifest — Format Specification

> Status: draft (v1). The manifest is the stable contract between HivePlane and adapters. It is versioned, validated against a strict JSON Schema, and changes to a live manifest require re-certification before promotion to production.

## Design Principles

- **Production is earned, not assumed** — the manifest carries certification status; production contexts require `certified`.
- **The contract stays stable** — runtime-specific fields never leak into core spec.
- **Policy is visible, not hidden in code** — tool permissions, budgets, approvals, and triggers are declarative.
- **Tool outputs are shaped at the boundary** — the manifest declares shaping rules, not the agent.
- **Certifications bind to model identity** — a manifest change to the model requires re-certification.
- **Changes to live workloads are gated by eval** — manifest diffs trigger re-certification via the promotion gate.

## Full Envelope

```yaml
apiVersion: hiveplane/v1
kind: AgentWorkload
metadata:
  name: string                    # required, unique, DNS-safe
  owner: string                   # required, owning team
  description: string             # optional
spec:                             # required
  runtime: {}                     # adapter, entrypoint, env
  tools: {}                       # tool objects with trust levels and MCP refs
  budget: {}                      # per-run and per-day USD ceilings
  approvals: {}                   # action classes requiring human consent
  model: {}                       # model strategy + identity for certification binding
  observability: {}               # telemetry contract
  certification: {}               # benchmark corpus reference + thresholds
  triggers: []                    # webhook/alert/cron/PR trigger rules
  sandbox: {}                     # execution isolation + resource caps + egress
  output_shaping: {}              # tool-output truncation/filtering/budget
  fan_out: []                     # result delivery destinations + trigger conditions
  health: {}                      # SLO config for agent health monitoring
```

### Complete Example

```yaml
apiVersion: hiveplane/v1
kind: AgentWorkload
metadata:
  name: incident-agent
  owner: platform-team
  description: Triages alerts and proposes remediation
spec:
  runtime:
    adapter: raw-worker
    entrypoint: examples.worker:run
    env:
      LOG_LEVEL: info
  tools:
    allow:
      - tool_id: prometheus.query
        trust_level: read_only
        mcp_server: monitoring-tools
      - tool_id: github.create_pr_comment
        trust_level: read_only
        mcp_server: github-tools
      - tool_id: pagerduty.acknowledge
        trust_level: destructive
        mcp_server: incident-tools
    deny:
      - tool_id: github.delete_repo
        trust_level: destructive
        mcp_server: github-tools
  budget:
    per_run_usd: 0.80
    per_day_usd: 8.00
  approvals:
    required_for:
      - destructive
    contact: "#platform-oncall"
  model:
    strategy: tiered
    identity: gpt-4o-2024-08-06
  observability:
    contract: standard
  certification:
    benchmark_corpus: corpora/incident-agent/v1
    staging_threshold:
      pass_rate: 0.70
      max_critical_failures: 2
      max_latency_p99_seconds: 60
    production_threshold:
      pass_rate: 0.85
      max_critical_failures: 0
      max_latency_p99_seconds: 30
    re_cert_interval_hours: 168       # weekly drift check
    grace_margin: 0.05               # drift must exceed 5% below baseline
  triggers:
    - type: alert
      config:
        source: pagerduty
        severity: [high, critical]
      target_workload: incident-agent
    - type: cron
      config:
        schedule: "0 */6 * * *"
      target_workload: incident-agent
  sandbox:
    enabled: true
    resource_caps:
      memory_mb: 2048
      cpu_cores: 2.0
      wall_clock_seconds: 900
      max_output_bytes: 1048576
    egress_allowlist:
      - api.pagerduty.com
      - api.github.com
  output_shaping:
    max_bytes_per_tool_call: 65536
    truncation_strategy: head_tail   # keep first N and last M bytes
    filter_rules:
      - pattern: "(?i)(api[_-]?key|secret|token|password)"
        action: redact
      - pattern: "^\\s+$"
        action: collapse_whitespace
  fan_out:
    - type: slack
      config:
        webhook_url: "${SLACK_WEBHOOK_URL}"
        channel: "#incident-reports"
      trigger: on_completion
    - type: slack
      config:
        webhook_url: "${SLACK_WEBHOOK_URL}"
        channel: "#platform-oncall"
      trigger: on_escalation
    - type: jira
      config:
        project_key: INC
        issue_type: Incident
      trigger: on_failure
    - type: webhook
      config:
        url: "https://internal.example.com/hiveplane-callback"
      trigger: on_completion
  health:
    slo:
      target_success_rate: 0.95
      target_latency_p99_seconds: 45
      evaluation_window_hours: 168
```

## Field Reference

### `metadata`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `name` | string | yes | Unique, DNS-safe (`[a-z0-9]([-a-z0-9]*[a-z0-9])?`) |
| `owner` | string | yes | Owning team — used for fleet review, escalation, and cost attribution |
| `description` | string | no | Human-readable summary |

### `spec.runtime`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `adapter` | string | yes | `raw-worker` or `langgraph` in v0.1.0 |
| `entrypoint` | string | yes | `module:callable` for the worker or compiled graph |
| `env` | map&lt;string, string&gt; | no | Non-secret environment values; secrets are referenced, never inlined |

### `spec.tools`

Tools are objects, not bare strings. Each tool entry carries a trust level and an MCP server reference so the policy engine can evaluate permissions contextually.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `allow` | list[ToolRef] | no | Explicitly allowed tools; each entry is an object |
| `deny` | list[ToolRef] | no | Explicitly denied tools; **deny wins** over `allow` |

**ToolRef object:**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `tool_id` | string | yes | Stable tool ID from the MCP Tool Registry (e.g. `github.read_issue`) |
| `trust_level` | string | yes | `read_only` or `destructive`; determines sandbox + approval requirements |
| `mcp_server` | string | yes | MCP server name the tool is served by (built on mcp-fabric) |

If neither `allow` nor `deny` is set, the default is **deny all**.

### `spec.budget`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `per_run_usd` | number | yes | Must be > 0 |
| `per_day_usd` | number | yes | Must be ≥ `per_run_usd` |

Budgets are enforced at admission (before a run starts) and on each usage event (during a run). If a run would exceed `per_run_usd`, it is blocked or escalated. If daily aggregate spend across all runs for this workload would exceed `per_day_usd`, new runs are refused.

### `spec.approvals`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `required_for` | list[string] | no | Action classes that require human approval, e.g. `destructive` |
| `contact` | string | no | Escalation target (e.g. `#platform-oncall` Slack channel) |

Known action classes: `destructive`, `high_spend`, `production_write`.

### `spec.model`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `strategy` | string | no | `fixed`, `tiered`, or `router` |
| `identity` | string | yes (for certification) | Model identifier the certification binds to (e.g. `gpt-4o-2024-08-06`) |

**Model-identity binding:** The certification attestation records `model.identity`. At runtime, the adapter reports the model in use. If the reported model does not match the attestation, the run is blocked (model-swap defense, threat T11).

### `spec.observability`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `contract` | string | no | Observability contract name, e.g. `standard` |

The contract defines which signals the workload emits (spans, metrics, audit events), making fleet-level comparisons possible.

### `spec.certification`

Certification is the thesis. A workload without a `certification` block cannot be admitted to production — it can only run in sandbox contexts.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `benchmark_corpus` | string | yes (for production) | Path/reference to the benchmark corpus for this workload type |
| `staging_threshold` | Threshold | yes | Pass/fail criteria for `provisional` (staging) status |
| `production_threshold` | Threshold | yes | Pass/fail criteria for `certified` (production) status |
| `re_cert_interval_hours` | integer | no | Periodic re-certification interval for drift detection (default: 168 = weekly) |
| `grace_margin` | number | no | Drift must exceed this margin below baseline before quarantine (default: 0.05) |

**Threshold object:**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `pass_rate` | number | yes | Minimum fraction of benchmark tasks that must pass (0.0–1.0) |
| `max_critical_failures` | integer | yes | Maximum allowed critical-task failures (0 for production) |
| `max_latency_p99_seconds` | number | yes | p99 latency ceiling across benchmark tasks |

**Certification status lifecycle:**

| Status | Meaning | Where it can run |
|--------|---------|------------------|
| `uncertified` | Registered, never benchmarked | Sandbox only |
| `provisional` | Passed benchmark at `staging_threshold` | Staging |
| `certified` | Passed benchmark at `production_threshold` | Production |
| `quarantined` | Failed re-certification or drifted below threshold | Runs blocked |

### `spec.triggers`

A list of trigger rules that auto-start runs. Each trigger matches an external event to this workload.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `type` | string | yes | `webhook`, `alert`, `cron`, or `pr` |
| `config` | object | yes | Type-specific configuration (see below) |
| `target_workload` | string | yes | Workload to start when the trigger fires |

**Trigger type configurations:**

| Type | Config fields | Example |
|------|---------------|---------|
| `webhook` | `path` (string), `secret` (string, env-ref) | `{path: /hooks/incident, secret: ${WEBHOOK_SECRET}}` |
| `alert` | `source` (string), `severity` (list[string]) | `{source: pagerduty, severity: [high, critical]}` |
| `cron` | `schedule` (string, cron expression) | `{schedule: "0 */6 * * *"}` |
| `pr` | `repo` (string), `events` (list[string]) | `{repo: org/repo, events: [opened, synchronize]}` |

The Trigger & Ingress Service handles dedup and idempotency. Before starting a run, it checks certification status — uncertified workloads are not auto-started in production contexts.

### `spec.sandbox`

Execution isolation for destructive or production-affecting runs. When `enabled`, the run executes in a separate context with resource caps and restricted network egress.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `enabled` | boolean | yes | Whether sandbox isolation is active |
| `resource_caps` | ResourceCaps | yes (if enabled) | Per-run resource limits |
| `egress_allowlist` | list[string] | no | Allowed outbound hostnames; all other egress is blocked |

**ResourceCaps object:**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `memory_mb` | integer | yes | Maximum memory in MB |
| `cpu_cores` | number | yes | Maximum CPU cores |
| `wall_clock_seconds` | integer | yes | Maximum wall-clock time |
| `max_output_bytes` | integer | yes | Maximum combined tool output size |

The sandbox has no shared filesystem with the control plane. Network egress is restricted to the allowlist. Suspicious patterns in tool outputs are escalated for approval.

### `spec.output_shaping`

Tool-output governance applied at the boundary before outputs reach the agent context window.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `max_bytes_per_tool_call` | integer | yes | Maximum bytes retained per individual tool call result |
| `truncation_strategy` | string | yes | `head_tail` (keep first N + last M), `head_only`, or `summary` |
| `filter_rules` | list[FilterRule] | no | Pattern-based filtering applied before truncation |

**FilterRule object:**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `pattern` | string | yes | Regex pattern to match in tool output |
| `action` | string | yes | `redact`, `collapse_whitespace`, `drop`, or `escalate` |

The shaping layer also includes injection scanning: tool outputs are scanned for prompt-injection patterns before reaching the agent context. Suspicious patterns escalate for approval rather than silently passing through.

### `spec.fan_out`

Result delivery destinations. When a run completes, fails, or escalates, results are pushed to configured channels with a trace link and (for certifications) an attestation link.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `type` | string | yes | `slack`, `teams`, `jira`, `pr_comment`, or `webhook` |
| `config` | object | yes | Type-specific configuration (see below) |
| `trigger` | string | yes | `on_completion`, `on_failure`, or `on_escalation` |

**Fan-out type configurations:**

| Type | Config fields |
|------|---------------|
| `slack` | `webhook_url` (env-ref), `channel` |
| `teams` | `webhook_url` (env-ref) |
| `jira` | `project_key`, `issue_type` |
| `pr_comment` | `repo`, `pr_number` (or auto from trigger context) |
| `webhook` | `url`, `headers` (optional) |

### `spec.health`

Agent health SLO configuration used by the agent health model and drift detector.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `slo` | SLOConfig | yes | Target SLO for this workload |

**SLOConfig object:**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `target_success_rate` | number | yes | Minimum acceptable run success rate (0.0–1.0) |
| `target_latency_p99_seconds` | number | yes | p99 latency target |
| `evaluation_window_hours` | integer | yes | Rolling window for SLO evaluation (e.g. 168 = 1 week) |

Health signals derived from this config: readiness (recent success rate ≥ target), failure rate, SLO status (meeting/breaching), and drift indicators (re-certification results vs. baseline).

## Validation Rules

### Metadata

- `metadata.name` is unique across the registry and DNS-safe
- `metadata.owner` is non-empty
- unknown fields rejected (strict schema)

### Tools

- Each tool entry in `allow`/`deny` must have `tool_id`, `trust_level`, and `mcp_server`
- `trust_level` must be `read_only` or `destructive`
- `tool_id` must be a registered tool in the MCP Tool Registry
- `mcp_server` must reference a configured MCP server
- `tools.deny` takes precedence over `tools.allow`
- If neither `allow` nor `deny` is set, default is deny all

### Budget

- `per_run_usd` > 0
- `per_day_usd` ≥ `per_run_usd`

### Approvals

- Every `required_for` value must be a known action class (`destructive`, `high_spend`, `production_write`)

### Model

- `model.identity` is required when `certification` is present (certification binds to model identity)
- Changing `model.identity` on a live manifest triggers re-certification

### Certification

- `benchmark_corpus` must reference an existing, versioned corpus
- `staging_threshold.pass_rate` must be ≤ `production_threshold.pass_rate`
- `staging_threshold.max_critical_failures` must be ≥ `production_threshold.max_critical_failures`
- `re_cert_interval_hours` must be > 0 if set
- `grace_margin` must be in [0.0, 0.5)

### Triggers

- Each trigger must have a valid `type` from the known set
- `config` must match the schema for the trigger type
- `target_workload` must match this manifest's `metadata.name` or another registered workload

### Sandbox

- If `sandbox.enabled` is `true`, `resource_caps` must be fully specified
- All `resource_caps` values must be > 0
- `egress_allowlist` entries must be valid hostnames

### Output Shaping

- `max_bytes_per_tool_call` must be > 0
- `truncation_strategy` must be from the known set
- `filter_rules` patterns must be valid regex
- `filter_rules` actions must be from the known set

### Fan-out

- Each fan-out entry must have a valid `type` and `trigger`
- `config` must match the schema for the fan-out type
- Secret values must be env-references (`${VAR_NAME}`), never inlined

### Health

- `slo.target_success_rate` must be in (0.0, 1.0]
- `slo.target_latency_p99_seconds` must be > 0
- `slo.evaluation_window_hours` must be > 0

## Versioning

`apiVersion` is `hiveplane/v1`. Manifest history is append-only — every change creates a new version. Changes to a live manifest's `runtime`, `tools`, `model`, `certification`, `sandbox`, or `output_shaping` require re-certification before promotion to production (enforced by the promotion gate).

## See Also

- [Workload manifest design](../design/workload-manifest-design.md)
- [PRD 02: Architecture](../prd/02-architecture.md)
- [PRD 05: Features](../prd/05-features.md)
- [PRD 06: Security Baseline](../prd/06-security-baseline.md)
- [Contributing workloads](CONTRIBUTING.md)
- [Adapters](../ADAPTERS.md)
