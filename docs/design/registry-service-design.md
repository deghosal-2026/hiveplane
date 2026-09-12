# D3: Registry Service Design

> Status: draft.

## Problem

The registry holds desired state: which agents exist, who owns them, and what they are allowed to do. It is the source of truth the rest of the control plane reconciles against.

## Responsibilities

- store and validate workload manifests
- expose the fleet catalog (list, get, by owner, by team)
- version manifests and record change history
- reject invalid or conflicting definitions

## API Sketch

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/workloads` | Register a workload |
| GET | `/workloads` | List workloads |
| GET | `/workloads/{name}` | Get a workload |
| PUT | `/workloads/{name}` | Update a workload |
| DELETE | `/workloads/{name}` | Deregister (if no active runs) |
| GET | `/workloads/{name}/versions` | Manifest history |

## Data

- `workloads` — current manifest, owner, team, timestamps
- `workload_versions` — append-only manifest history
- uniqueness on `name`; ownership metadata indexed for fleet review

## Open Questions

- deletion semantics when runs reference a workload
- whether manifests are stored whole or normalized
