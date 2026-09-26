# D30: Runtime Guards Design

> Status: draft

**Milestones:** M41 · **Extends:** D5

## Problem

D5 governs money and wall-clock with per-run, per-day, and per-team budgets, but three runtime failure modes remain ungoverned: an agent can blow its context window mid-run and crash or silently truncate, a workload can burn a month's budget in minutes through a runaway loop, and a flaky tool can be retried forever or fail the whole run. These are runtime stability concerns, not billing concerns, and must be handled live with clean pauses and recorded reasons.

D30 adds the **fourth budget** (context) alongside D5's money and time budgets, plus spend-velocity, retry, and circuit-breaker guards. All guards are enforced at the execution boundary and expressed as policy decisions, so every intervention carries a reason and an audit record.

## Overview

```
 provider usage ─┐
 tool results  ──┼──▶ Guard Manager ──▶ policy decision
                 │     context budget | spend velocity |
                 │     retry policy   | circuit breaker
                 └──▶ {action, reason, rule_id} ─▶ audit + run story
```

## Design

### Context-Window Budget (the Fourth Budget)

D5 tracks money and time; context is the third runtime resource and gets the same treatment — a per-run ceiling enforced live from **real provider token counts**, not estimates.

```yaml
spec:
  budgets:
    context_tokens: 200000    # per-run max context
    context_warn_at: 0.80     # warn threshold
```

After every model call the provider boundary reports actual input/output tokens; the guard maintains a running context total per run. On breach:

- The run **pauses cleanly** — no crash, no silent truncation.
- The breach accounting (`used`, `limit`, `step`, `model_identity`) is recorded and shown in the run story.
- Resume with a larger budget, or cancel, is an operator decision.

Shaping (DD-13) keeps tool payloads small, but shaping is not accounting: the guard reflects what the provider actually charged. Context is tracked independently per run even when D5 budgets are per-workload.

### Spend-Velocity Guards

Per-run and per-day budgets are ceilings; velocity is the derivative. A workload within budget but spending at an anomalous rate is paused before it consumes the rest.

| Parameter | Meaning | Default |
|-----------|---------|---------|
| `velocity_window` | Rolling window for burn rate | 5m |
| `velocity_limit_usd` | Max spend per window | workload-configurable |
| `velocity_multiplier` | Alert when rate exceeds N× trailing median | 5× |

On breach the run is auto-paused and the owner is alerted via fan-out (D15), with current rate, trailing baseline, and projected time-to-exhaustion. The pause happens before the next model/tool call.

### Retry Policies

Retries are declared per workload and may be overridden per tool.

```yaml
spec:
  retries:
    default: { max_attempts: 3, base_ms: 500, max_ms: 30000, jitter: full }
    tools:
      query-postgres: { max_attempts: 5, base_ms: 200 }
```

Backoff is exponential with jitter (`base_ms * 2^attempt`, clamped to `max_ms`), stopping at `max_attempts`; each attempt records its delay and outcome. Retries consume budget and context like any other call. Non-idempotent destructive tools are not retried unless the tool declares idempotency (D32).

### Circuit Breakers

| Scope | Trips on | Opens for | Probe |
|-------|----------|-----------|-------|
| Per tool | failure rate over window > threshold | `open_for` | one half-open request |
| Per workload | run failure rate over window > threshold | `open_for` | one half-open run |

State machine: `closed → open → half-open → closed` (success) or `half-open → open` (failure). A half-open probe bounds blast radius and prevents flapping. While open, calls are denied immediately with `circuit_open`; the agent gets a structured refusal and may continue on an alternate path. Breaker state is keyed by stable tool ID (D32) and workload, and is visible in health (D31).

### Guard ↔ Policy Integration

Guard activations are not side channels. Each produces a policy decision:

```
{ action: pause | deny | throttle, guard: context | velocity | retry | breaker,
  reason, rule_id, observed, threshold, run_id, tool_id? }
```

Decisions flow to the audit log and the run story. `allow`-class guard events (retry scheduled, probe succeeded) are recorded as run events without operator action. Because guards reuse the policy reason/rule-id shape (D29), dashboards and incident review treat them uniformly.

## Data Model

```
guard_events
  id, run_id, workload_id, guard, action, reason, rule_id,
  observed JSONB, threshold JSONB, created_at

context_accounting
  run_id, step, model_identity,
  input_tokens, output_tokens, context_total, recorded_at
  PRIMARY KEY (run_id, step)

circuit_breakers
  scope,          -- tool | workload
  scope_id,
  state,          -- closed | open | half_open
  failure_rate, opened_at, updated_at
  PRIMARY KEY (scope, scope_id)
```

## Failure Modes

| Failure | Behavior |
|---------|----------|
| Provider omits token counts | context guard uses a conservative estimate and flags the run |
| Velocity baseline missing (new workload) | absolute limit only; no multiplier trip |
| Breaker probe succeeds but next call fails | breaker reopens; half-open is one request, not a window |
| Retry storm against a down tool | breaker trips before the retry budget is exhausted |
| Guard manager unavailable | destructive calls fail-closed; non-destructive calls allowed and flagged |

## Security

- Guards are enforced at the control-plane boundary, not in agent code (DD-03).
- Context accounting and guard events never contain secret material (D33).
- Velocity alerts and breaker events are attributable and auditable (DD-07).

## Testing

- Exceeding the context budget pauses the run with accounting shown — no crash, no silent truncation.
- A spend-velocity anomaly pauses the run and alerts the owner.
- Retries honor exponential backoff + jitter and stop at `max_attempts`.
- A breaker trips after the threshold, half-opens, and recovers on a successful probe without flapping.
- Guard activations carry a reason and a rule id and appear in the run story.

## Open Questions

- Should context be budgeted per run only, or also per workload/team like D5?
- Is velocity measured in USD only, or also tokens/min for fixed-price local models?
- Should breaker thresholds adapt from observed baselines, or stay static?

## See Also

- [Budget Enforcement Design](budget-enforcement-design.md) (D5) — money and time budgets; D30 adds context
- [Defense & Policy v2 Design](defense-policy-v2-design.md) (D29) — guard decisions reuse the policy shape
- [Agent Health & SLO v2 Design](agent-health-slo-design.md) (D31) — breaker and burn signals feed health
- [MCP Registry v2 Design](mcp-registry-v2-design.md) (D32) — tool IDs and idempotency for retries
- [PRD 05: Features](../prd/05-features.md) — Safe Execution, Observability & Health
- [WBS Part 9](../wbs/v0.2.0/wbs-v0.2.0-part9-guards-health.md) — M41
