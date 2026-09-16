# Benchmark Corpus Format

A benchmark corpus is the versioned set of reproducible tasks a workload is
certified against. Certification is the thesis of HivePlane: an agent earns
production admission by passing its corpus (DD-09, DD-10).

A corpus lives in a YAML file named `corpus.yaml` (or a directory containing
that file). The document is a strict [`BenchmarkCorpus`](#document-shape): the
authoritative JSON Schema is available from
`hiveplane.certification.corpus.corpus_json_schema()`.

## Document shape

```yaml
id: repo-agent-corpus     # stable corpus id
version: 3                # monotonic; bump on any task change
tasks:
  - id: task-001
    name: classify severity from an alert payload
    input:
      alert_payload: { severity: high, service: checkout }
    expected:
      outcome: "severity: high"
      required_fields: [severity, summary, owner]
      forbidden_actions: []
    check:
      type: exact_match
      field: severity
      value: high
    critical: false
    timeout_seconds: 30
    allow_network: false
```

| Field | Required | Meaning |
|-------|----------|---------|
| `id` | yes | Stable, DNS-safe corpus identifier. |
| `version` | yes | Positive integer. Every task change requires a new version; the version is recorded in the signed attestation. |
| `tasks` | yes | One or more unique tasks. |
| `tasks[].id` | yes | Task id, unique within the corpus. |
| `tasks[].name` | yes | Human-readable description. |
| `tasks[].input` | no | JSON input handed to the agent (fixtures, not live data). |
| `tasks[].expected` | no | Expected outcome, required fields, and forbidden actions. |
| `tasks[].check` | yes | Deterministic pass/fail check (see below). |
| `tasks[].critical` | no | A critical task failure is a hard certification block. Default `false`. |
| `tasks[].timeout_seconds` | no | Per-task wall-clock bound. Default `30`. |
| `tasks[].allow_network` | no | Network is disabled unless explicitly allowed. Default `false`. |

Validation is strict: unknown fields are rejected, `tasks` must be non-empty,
task ids must be unique, and each check must declare the fields its type needs.

## Check types

| Type | Fields | Determinism |
|------|--------|-------------|
| `exact_match` | `field`, `value` | Fully deterministic |
| `action_audit` | `required_actions`, `forbidden_actions` | Fully deterministic |
| `schema_match` | `field`, `value` (the schema) | Fully deterministic |
| `rubric` | `criteria`, `min_score` | Deterministic given a pinned evaluator model at temperature 0 |
| `custom` | `callable` | Deterministic given fixed inputs |

## Versioning

Corpus identity is `(id, version)`. Attestations bind both the corpus id and
version, so a corpus change cannot silently re-use an old certification. Bump
`version` whenever tasks are added, removed, or modified.

## Loading

```python
from hiveplane.certification.corpus import load_corpus

corpus = load_corpus("corpora/repo-agent/v1")  # dir or corpus.yaml path
print(corpus.id, corpus.version, len(corpus.tasks))
```

`load_corpus` raises `CorpusError` with specific field-level detail when a
corpus is missing, malformed YAML, or structurally invalid.

## See Also

- [Certification Pipeline Design](../design/certification-pipeline-design.md)
- [Manifest Format Spec](manifest-format-spec.md)
