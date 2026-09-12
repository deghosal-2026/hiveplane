# D5: Budget Enforcement Design

> Status: draft.

## Problem

Spend discipline is a fleet-level property. Budgets must be enforced during execution, not reported after the fact (DD-04).

## Budget Levels

| Level | Scope | Enforced when |
|-------|-------|---------------|
| Per run | One run | Before each model/tool call and at admission |
| Per day | Workload (or team) | At task admission and on usage events |

## Mechanism

1. On task submission, check remaining per-day budget; reject or queue if exhausted.
2. On each usage event (tokens/tools), decrement the run budget.
3. When a run exceeds its budget, transition to `escalate`/`failed` per policy.
4. Emit budget-burn metrics for fleet review.

## Cost Tracking

Cost is derived from token usage and tool-call metadata reported by adapters, using a per-model cost table (or an external router like TierForge).

## Open Questions

- handling of streaming usage and partial cost
- grace margin before hard stop
- per-team aggregate limits vs per-workload limits
