# D7: State Store Design

> Status: draft.

## Problem

Desired state, run state, and audit history must be durable enough to survive restarts and to answer "what happened?" after the fact (DD-05).

## Entities

| Entity | Contents |
|--------|----------|
| `workloads` / `workload_versions` | Desired state (registry) |
| `runs` | Run identity, workload, caller, current state, timestamps |
| `run_events` | Append-only transition log with attribution |
| `usage_events` | Token/tool usage for budget accounting |
| `audit_log` | Operator actions and policy outcomes |
| `approvals` | Pending and resolved approval requests |

## Guarantees

- transitions persisted before side effects are acknowledged
- event logs append-only
- audit records are tamper-evident
- queries by run, workload, owner/team, and time window

## Technology

PostgreSQL as the system of record. Exact schema lands with the v0.1.0 WBS (Part 1 and Part 3).

## Open Questions

- retention windows for events vs audit records
- partitioning strategy for high-volume usage events
