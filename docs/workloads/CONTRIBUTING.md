# Contributing Workloads

HivePlane ships example workloads and welcomes community-contributed ones. This guide explains how to add a workload that others can run and certify.

## What Makes a Good Workload

- it does one clear thing (summarize, triage, review, draft)
- it uses only tools that are safe to demonstrate, classified by trust level
- it declares a realistic budget and an approval rule for anything destructive
- it carries a `certification` block with a benchmark corpus and staging/production thresholds
- it declares sandbox isolation and output shaping for any destructive or large-output workflows
- it fits the manifest format exactly — no runtime-specific fields in core

## Layout

```
examples/workloads/
├── README.md
├── repo-agent.yaml
├── docs-agent.yaml
└── incident-agent.yaml

examples/corpora/
├── README.md
├── repo-agent/
│   └── v1/
├── docs-agent/
│   └── v1/
└── incident-agent/
    └── v1/
```

Each workload that targets production must reference a benchmark corpus under `examples/corpora/<workload>/vN/`. The corpus is a set of tasks with known expected outcomes and deterministic pass/fail checks.

## Steps

1. Copy an existing workload manifest (e.g. `examples/workloads/incident-agent.yaml`).
2. Fill in `metadata` (name, owner, description).
3. Fill in `spec` with all required blocks:
   - `runtime` — adapter, entrypoint, env
   - `tools` — tool objects with `tool_id`, `trust_level`, `mcp_server`
   - `budget` — per-run and per-day USD ceilings
   - `approvals` — action classes requiring human consent
   - `model` — strategy and `identity` (certification binds to this)
   - `observability` — contract name
   - `certification` — benchmark corpus reference + staging/production thresholds
   - `triggers` — webhook/alert/cron/PR rules (optional)
   - `sandbox` — isolation flag + resource caps + egress allowlist (required for destructive tools)
   - `output_shaping` — max bytes, truncation strategy, filter rules
   - `fan_out` — result delivery destinations (optional)
   - `health` — SLO config (target success rate, latency, evaluation window)
4. Create the benchmark corpus under `examples/corpora/<name>/v1/` with tasks and expected outcomes.
5. Validate it: `hiveplane validate examples/workloads/<name>.yaml`.
6. Dry-run register: `hiveplane register examples/workloads/<name>.yaml --dry-run` — reports what would be enforced without admitting runs.
7. Certify it: `hiveplane certify <name>` — runs the benchmark and assigns certification status.
8. Add a short entry to `examples/workloads/README.md`.
9. Open a PR. Corpus changes require review and approval (benchmark-poisoning defense).

## Rules

- `tools.deny` must be explicit for anything destructive
- `trust_level` must be set on every tool entry (`read_only` or `destructive`)
- `mcp_server` must reference a configured MCP server
- budgets must be positive and `per_day_usd >= per_run_usd`
- approval classes must be from the known action-class set (`destructive`, `high_spend`, `production_write`)
- `model.identity` must be set when `certification` is present
- `certification.staging_threshold.pass_rate` must be ≤ `production_threshold.pass_rate`
- `sandbox.enabled` must be `true` if any tool has `trust_level: destructive`
- `output_shaping.max_bytes_per_tool_call` must be > 0
- `fan_out` secret values must be env-references (`${VAR_NAME}`), never inlined
- no secrets or credentials in the manifest — use env-references
- no runtime-specific fields outside `spec.runtime`

## Certification Checklist for Contributors

Before your workload can run in production:

- [ ] Benchmark corpus exists and has ≥ 5 tasks with deterministic pass/fail checks
- [ ] Corpus has been reviewed (no crafted tasks that pass a bad agent)
- [ ] `hiveplane certify <name>` passes at `production_threshold`
- [ ] Attestation is signed and stored
- [ ] `hiveplane certs show <id>` displays the full attestation
- [ ] Uncertified version is refused admission to a production context
- [ ] Model swap (changing `model.identity` without re-certification) is blocked

## See Also

- [Manifest format spec](manifest-format-spec.md)
- [Workload manifest design](../design/workload-manifest-design.md)
- [PRD 05: Features](../prd/05-features.md)
- [PRD 06: Security Baseline](../prd/06-security-baseline.md)
