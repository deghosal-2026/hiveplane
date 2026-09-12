# Contributing Workloads

HivePlane ships example workloads and welcomes community-contributed ones. This guide explains how to add a workload that others can run.

## What Makes a Good Workload

- it does one clear thing (summarize, triage, review, draft)
- it uses only tools that are safe to demonstrate
- it declares a realistic budget and an approval rule for anything destructive
- it fits the manifest format exactly — no runtime-specific fields in core

## Layout

```
examples/workloads/
├── README.md
├── repo-agent.yaml
├── docs-agent.yaml
└── incident-agent.yaml
```

## Steps

1. Copy an existing workload manifest.
2. Fill in `metadata` (name, owner, description) and `spec` (runtime, tools, budget, approvals, model, observability).
3. Validate it: `hiveplane validate examples/workloads/<name>.yaml`.
4. Add a short entry to `examples/workloads/README.md`.
5. Open a PR.

## Rules

- `tools.deny` must be explicit for anything destructive
- budgets must be positive and `per_day_usd >= per_run_usd`
- approval classes must be from the known action-class set
- no secrets or credentials in the manifest

## See Also

- [Manifest format spec](manifest-format-spec.md)
- [Workload manifest design](../design/workload-manifest-design.md)
