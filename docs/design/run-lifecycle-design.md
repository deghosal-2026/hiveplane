# D2: Run Lifecycle Design

> Status: draft.

## Problem

Every run, regardless of runtime, must move through one observable and intervenable state machine.

## States

```
queued ──> running ──> completed
             │  ▲
             ▼  │
           paused ──> cancelled
             │
             └────> failed
```

| State | Meaning |
|-------|---------|
| `queued` | Accepted, awaiting an adapter slot |
| `running` | Executing |
| `paused` | Suspended by operator or policy, state retained |
| `completed` | Finished successfully |
| `failed` | Terminated by error or policy |
| `cancelled` | Stopped by operator |

## Transitions

| From | To | Trigger |
|------|----|---------|
| queued | running | Adapter picks up the run |
| running | paused | Operator action or policy escalation |
| paused | running | Operator resumes |
| running | completed | Runtime reports success |
| running | failed | Runtime error or budget/policy violation |
| running/paused | cancelled | Operator stops the run |

## Requirements

- transitions are persisted before side effects are acknowledged
- every transition is attributed (who/what caused it) and audited
- pause/resume must not lose run context (DD-05)
- runs survive a control-plane restart

## Open Questions

- cooperative vs preemptive pause semantics per adapter
- max pause duration before a run is reclaimed
