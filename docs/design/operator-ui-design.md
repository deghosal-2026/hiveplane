# D9: Operator UI Design

> Status: draft.

## Problem

Operators need one surface to see the fleet, inspect a run, approve an escalation, and understand spend — and to act, not just look.

## v0.1.0 Screens

| Screen | Contents |
|--------|----------|
| Fleet list | All workloads: owner, state counts, recent failures, budget burn |
| Run detail | State timeline, tool calls, model calls, cost, trace link |
| Approval queue | Pending escalations with evidence and approve/deny actions |
| Spend view | Budget burn by workload and team |

## Principles

- action-first: pause/resume/stop and approve/deny are first-class
- every operator action is attributed and audited (DD-07)
- show *why* a policy decision was made
- keep v0.1.0 to a fleet list and a run detail page before adding breadth

## Stack

React, backed by the Execution API and telemetry pipeline.

## Open Questions

- real-time updates (polling vs streaming)
- how to present trace-linked debug context compactly
