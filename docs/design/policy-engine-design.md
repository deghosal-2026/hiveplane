# D4: Policy Engine Design

> Status: draft.

## Problem

Tool permissions, approval requirements, and escalation must be enforced consistently at the control-plane boundary — visible in config, not buried in agent code (DD-03).

## Inputs

- workload manifest (`spec.tools`, `spec.approvals`)
- run context (agent, team, environment, action class)
- budget state (from D5)

## Decisions

| Decision | Meaning |
|----------|---------|
| `allow` | Proceed |
| `deny` | Block, record reason |
| `escalate` | Pause for human approval; attach evidence |

## Evaluation Order

1. explicit deny
2. explicit allow
3. action-class approval requirement
4. default deny

## Explainability

Every decision returns a reason and the rule that produced it. Operators must be able to answer "why was this allowed/denied?" without reading code.

## Open Questions

- policy expression language (structured config vs embedded rules)
- how policy packs are versioned and distributed by team
