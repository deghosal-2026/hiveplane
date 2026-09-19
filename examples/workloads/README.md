# Example Workloads

Three reference agent workloads that double as benchmark fixtures for the v0.1.0
field test. Each manifest exercises the full contract: runtime, model identity,
certification, tools, budget, approvals, triggers, sandbox, output shaping,
fan-out, and health.

| Workload | Runtime | What it does | Stresses |
|----------|---------|--------------|----------|
| [`repo-agent.yaml`](repo-agent.yaml) | `raw-worker` | Summarizes open PRs via read-only tools | Read-only tool policy, usage reporting, certification at the production threshold |
| [`docs-agent.yaml`](docs-agent.yaml) | `langgraph` | Drafts docs updates and opens review PRs | LangGraph adapter, state transitions, output shaping on large payloads |
| [`incident-agent.yaml`](incident-agent.yaml) | `raw-worker` | Triages an alert and acknowledges incidents | Destructive tool in sandbox, approval flow, budget escalation, fan-out delivery |

All three declare `certification.status: uncertified`. They cannot be admitted to
a production context until they pass their benchmark corpus and are certified
(DD-09).

## Benchmark corpora

Each manifest references a versioned corpus under [`examples/corpora/`](../corpora).
Only the demo workload ships a seeded corpus today:

| Workload | Corpus | Tasks |
|----------|--------|-------|
| `repo-agent` | [`corpora/repo-agent/v1`](../corpora/repo-agent/v1/corpus.yaml) | 7 positive risk-classification tasks + 4 counterexamples (refuse/flag) |

Corpora for `docs-agent` and `incident-agent` land with the field test (M23). See
[`docs/workloads/corpus-format.md`](../../docs/workloads/corpus-format.md) for the
format and check types.

## v0.1.0 scope

The manifests are forward-compatible: some declared sections are parsed and
validated but **not enforced until v0.2.0**.

- `spec.triggers[]` — parsed, but trigger ingestion is not implemented in v0.1.0.
- `spec.tools.mcp_servers[]` — parsed, but MCP transport is not implemented in
  v0.1.0 (tool calls use the fixture tool executor, #116).
- `spec.fan_out` — only the implemented transports `slack` and `webhook` are
  used by the examples. `teams` and `jira` are valid manifest values but have no
  transport yet; commented-out v0.2.0 examples are kept in `incident-agent.yaml`.

## Validate

```bash
hiveplane validate examples/workloads/repo-agent.yaml
hiveplane validate examples/workloads/docs-agent.yaml
hiveplane validate examples/workloads/incident-agent.yaml
```

Validation is strict: unknown fields are rejected, names must be DNS-safe,
budgets must be ordered (`per_team_usd >= per_day_usd >= per_run_usd`),
certification thresholds must be ordered, and a `certified` workload must carry a
non-expired `attestation_id`.

## Schema

The authoritative JSON Schema is published at
[`docs/workloads/manifest.schema.json`](../../docs/workloads/manifest.schema.json)
and served by the control plane at `GET /manifest/schema`. The field reference is
[`docs/workloads/manifest-format-spec.md`](../../docs/workloads/manifest-format-spec.md).
