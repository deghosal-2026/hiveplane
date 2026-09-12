# D1: Workload Manifest Design

> Status: draft

## Problem

HivePlane needs one declarative contract that describes any agent workload without leaking runtime specifics. The manifest is the stable interface between the control plane and adapters (DD-01). After the PRD rewrite, the manifest must also carry certification references, trigger rules, sandbox configuration, tool-output shaping, MCP tool IDs with trust levels, result fan-out destinations, and agent health SLOs — all the new capabilities that make HivePlane a self-operating, certification-gated fleet platform.

See [PRD 02: Architecture](../prd/02-architecture.md) for component context and [PRD 05: Features](../prd/05-features.md) for the full feature list.

## Manifest Shape

```yaml
apiVersion: hiveplane/v1
kind: AgentWorkload
metadata:
  name: example-agent
  owner: platform-team
  team: platform
  labels:
    environment: production
    criticality: medium
spec:
  runtime:
    adapter: raw-worker            # or langgraph
    entrypoint: examples.worker:run
  model:
    strategy: tiered               # fixed | tiered | router
    identity:                      # model identity bound to certification (DD-10)
      provider: openai
      family: gpt-4o
      version: "2024-08-06"
  certification:                   # SWE-bench-style certification (DD-09, DD-10, DD-11)
    benchmark_corpus: benchmarks/example-agent/v3
    staging_threshold: 0.80
    production_threshold: 0.90
    no_critical_failures: true
    latency_budget_ms: 30000
    re_cert_interval: 14d          # drift detection cadence (DD-12)
    status: certified              # uncertified | provisional | certified | quarantined
    attestation_id: att-7f3a1b2c   # signed attestation reference
    certified_at: "2025-09-01T14:22:00Z"
    certified_by: ci@hiveplane
    expires_at: "2025-09-29T14:22:00Z"
  triggers:                        # event-driven run auto-start (PRD 05: triggers)
    - type: webhook
      url: /hooks/example-agent/alert
      match:
        severity: [critical, warning]
    - type: alert
      source: pagerduty
      match:
        service: payment-service
    - type: github_pr
      events: [opened, synchronize]
      match:
        paths: ["src/payment/**"]
    - type: cron
      schedule: "0 */6 * * *"     # every 6 hours
      mode: watch                 # one-shot | watch | scheduled
      max_concurrent: 1
  sandbox:                         # execution isolation (DD-14)
    enabled: true                  # forced for destructive action classes
    resource_caps:
      memory_mb: 512
      cpu_cores: 1.0
      wall_clock_s: 300
    egress:
      allow: ["api.github.com", "api.pagerduty.com"]
      deny: ["169.254.169.254"]    # metadata endpoints
      mode: restricted             # open | restricted | none
    filesystem: isolated           # no shared FS with control plane
  output_shaping:                  # tool-output boundary (DD-13)
    max_bytes: 16384
    truncate_strategy: tail        # head | tail | summary
    filter_rules:
      - pattern: "password|secret|token"
        action: redact
      - pattern: "\\b\\d{16}\\b"
        action: mask               # credit card numbers
    injection_scan: true           # prompt-injection defense (T14)
  tools:                           # MCP tool registry references (PRD 05: tools & MCP)
    allow:
      - tool_id: mcp.github.read_issue
        trust_level: read_only
      - tool_id: mcp.github.create_pr
        trust_level: destructive
        require_approval: true
    deny:
      - mcp.github.delete_repo
    mcp_servers:
      - name: github-mcp
        endpoint: http://mcp-fabric/github
    default_trust: read_only       # deny-by-default for unlisted tools
  budget:
    per_run_usd: 0.50
    per_day_usd: 5.00
    per_team_usd: 50.00
  approvals:
    required_for:
      - destructive
      - production_write
    contact: "#platform-oncall"
    auto_escalate_after: 300s
  fan_out:                         # result delivery (PRD 05: result delivery)
    on_completed:
      - type: slack
        channel: "#example-agent-results"
      - type: webhook
        url: https://hooks.example.com/hiveplane
    on_failed:
      - type: slack
        channel: "#platform-oncall"
    on_escalation:
      - type: teams
        channel: "Platform Escalations"
      - type: jira
        project: PLAT
        issue_type: Task
    always_include:
      - trace_link
      - attestation_link
  health:                          # agent health model (PRD 05: observability & health)
    readiness_probe:
      type: dry_run
      interval: 60s
    slo:
      availability_target: 0.99
      quality_target: 0.90         # completion without escalation
      error_budget_window: 24h
    failure_rate_threshold: 0.15   # auto-quarantine trigger
  observability:
    contract: standard
    trace_sampling: 1.0            # 100% for v0.1.0
```

## Fields

### Core Fields

| Field | Purpose |
|-------|---------|
| `metadata.name` | Unique workload identity (DNS-safe) |
| `metadata.owner` | Owning team (for fleet review and escalation) |
| `metadata.team` | Team for cost attribution and policy pack binding |
| `metadata.labels` | Arbitrary labels for filtering and context-aware policy |
| `spec.runtime` | Adapter type + entrypoint |
| `spec.model.strategy` | Model selection strategy: `fixed`, `tiered`, or `router` |
| `spec.model.identity` | Model identity binding — provider, family, version; checked against attestation at runtime (DD-10, T11) |
| `spec.budget` | Per-run, per-day, and per-team spend ceilings |
| `spec.approvals` | Which action classes require human approval, whom to contact, auto-escalation timeout |
| `spec.observability` | Observability contract and trace sampling rate |

### Certification Fields (DD-09, DD-10, DD-11, DD-12)

| Field | Purpose |
|-------|---------|
| `spec.certification.benchmark_corpus` | Reference to the benchmark corpus (versioned, reviewed) |
| `spec.certification.staging_threshold` | Pass-rate threshold to earn `provisional` status |
| `spec.certification.production_threshold` | Pass-rate threshold to earn `certified` status |
| `spec.certification.no_critical_failures` | If true, any critical task failure blocks certification |
| `spec.certification.latency_budget_ms` | Per-task latency bound; tasks exceeding it count as failures |
| `spec.certification.re_cert_interval` | Cadence for periodic re-certification / drift detection |
| `spec.certification.status` | Current status: `uncertified`, `provisional`, `certified`, `quarantined` |
| `spec.certification.attestation_id` | Reference to the signed attestation record |
| `spec.certification.certified_at` | Timestamp of last successful certification |
| `spec.certification.certified_by` | Identity that performed the certification |
| `spec.certification.expires_at` | Expiry timestamp — expired certifications are treated as stale |

### Trigger Fields (PRD 05: triggers)

| Field | Purpose |
|-------|---------|
| `spec.triggers[].type` | `webhook`, `alert`, `github_pr`, or `cron` |
| `spec.triggers[].match` | Event-matching rules (severity, service, paths, etc.) |
| `spec.triggers[].mode` | `one-shot`, `watch`, or `scheduled` — controls 24/7 operation |
| `spec.triggers[].max_concurrent` | Cap on concurrent trigger-originated runs |

### Sandbox Fields (DD-14)

| Field | Purpose |
|-------|---------|
| `spec.sandbox.enabled` | Whether sandbox execution is active (forced for destructive actions) |
| `spec.sandbox.resource_caps` | Memory, CPU, and wall-clock limits per run |
| `spec.sandbox.egress` | Network egress allow/deny lists and mode |
| `spec.sandbox.filesystem` | `isolated` — no shared filesystem with the control plane |

### Output Shaping Fields (DD-13, T14)

| Field | Purpose |
|-------|---------|
| `spec.output_shaping.max_bytes` | Maximum bytes of tool output allowed into agent context |
| `spec.output_shaping.truncate_strategy` | `head`, `tail`, or `summary` when output exceeds cap |
| `spec.output_shaping.filter_rules` | Pattern-based redaction/masking rules |
| `spec.output_shaping.injection_scan` | If true, tool outputs are scanned for prompt-injection patterns before reaching the agent |

### Tools & MCP Fields (PRD 05: tools & MCP)

| Field | Purpose |
|-------|---------|
| `spec.tools.allow[]` | Allowed tools referenced by MCP tool ID, with trust level and approval requirement |
| `spec.tools.deny[]` | Explicitly denied tool IDs (deny wins over allow) |
| `spec.tools.mcp_servers[]` | MCP server definitions (endpoint, name) |
| `spec.tools.default_trust` | Default trust level for unlisted tools (deny-by-default) |

### Fan-Out Fields (PRD 05: result delivery)

| Field | Purpose |
|-------|---------|
| `spec.fan_out.on_completed` | Destinations notified on run completion |
| `spec.fan_out.on_failed` | Destinations notified on run failure |
| `spec.fan_out.on_escalation` | Destinations notified on escalation |
| `spec.fan_out.always_include` | Metadata always attached to fan-out messages (trace link, attestation link) |

### Health Fields (PRD 05: observability & health)

| Field | Purpose |
|-------|---------|
| `spec.health.readiness_probe` | How to check agent readiness (dry-run probe) |
| `spec.health.slo` | Availability and quality SLO targets with error budget window |
| `spec.health.failure_rate_threshold` | Failure rate that triggers auto-quarantine |

## Validation Rules

- names are unique and DNS-safe
- `tools.deny` wins over `tools.allow`
- every `allow` entry must reference a registered MCP tool ID (validated against the [MCP tool registry](registry-service-design.md))
- `trust_level` must be `read_only` or `destructive`; `destructive` tools require `require_approval: true` if listed in production contexts
- budgets are positive and `per_day_usd >= per_run_usd`; `per_team_usd >= per_day_usd`
- every `required_for` class is a known action class (`destructive`, `production_write`, `read_only`)
- `certification.production_threshold >= certification.staging_threshold`
- if `certification.status == certified`, `attestation_id` must be non-empty and `expires_at` must be in the future
- if `spec.model.identity` is specified, it must match the model identity in the attestation (DD-10, T11)
- `sandbox.egress.deny` must always include cloud metadata endpoints (`169.254.169.254`)
- `output_shaping.max_bytes` must be > 0
- trigger rules must have a valid `type` and at least one `match` criterion or `schedule` (for cron)
- fan-out destinations of type `jira` require `project` and `issue_type`; `slack` and `teams` require `channel`
- `health.slo.availability_target` and `health.slo.quality_target` must be in [0, 1]

## Manifest Lifecycle

1. **Submit** — manifest is validated against JSON Schema; `--dry-run` reports what would be enforced without admitting runs.
2. **Certify** — benchmark runner executes the workload against its corpus; on pass, attestation is signed and `certification.status` is updated. See [Run lifecycle](run-lifecycle-design.md).
3. **Admit** — registry checks certification status; only `certified` workloads are admitted to production contexts (DD-09). See [Registry service](registry-service-design.md).
4. **Promote change** — manifest updates require re-certification before promotion; regressions are blocked (DD-11). See [Registry service](registry-service-design.md).
5. **Drift detect** — periodic re-certification per `re_cert_interval`; drift auto-quarantines (DD-12).
6. **Deregister** — blocked if active runs reference the workload.

## Open Questions

- manifest schema versioning and migration strategy across versions
- how much runtime config belongs in `spec.runtime` before it becomes framework-specific
- whether `fan_out` destinations are defined per-workload or inherited from team policy packs
- whether health SLO config is shared across workloads of the same type
- how benchmark corpus references are validated (corpus exists, is approved, is current)

## See Also

- [Registry service](registry-service-design.md) — manifest storage, certification status, admission enforcement
- [Run lifecycle](run-lifecycle-design.md) — how triggers, sandbox, and fan-out integrate into the run flow
- [Policy engine](policy-engine-design.md) — how tool trust levels and context-aware policy consume manifest fields
- [Budget enforcement](budget-enforcement-design.md) — how budget and cost showback consume manifest fields
- [Runtime adapter](runtime-adapter-design.md) — how sandbox and output shaping integrate into the adapter contract
- [Design decisions](design-decisions.md) — DD-01, DD-09, DD-10, DD-11, DD-13, DD-14
