# D28: Progressive Delivery Design

> Status: draft

**Milestones:** M37–M38 · **Extends:** D10

## Problem

Certification is a point-in-time gate: it proves a candidate passed the corpus, not that it behaves well on live traffic. Without a way to observe a candidate against the certified version on real work, teams either ship blind or never ship. This doc defines three graduated delivery modes — shadow, canary, and model experiments — that generate real evidence before a candidate replaces production.

Automation is **safe by default**: shadow runs cannot cause side effects, canaries evaluate over a minimum sample before acting, and every automated promote or abort has a manual override.

## Overview

```
 candidate manifest version
        │
        ├── SHADOW ──── mirror production task (no delivery)
        │               read-only tools · separate budget
        │               └─▶ outcome diff (output/tools/cost/latency/policy)
        │
        ├── CANARY ──── split % of eligible triggers
        │               evaluation window: live metrics + sampled judge scores
        │               ├─ clean ──▶ AUTO-PROMOTE (re-point traffic)
        │               └─ regression ──▶ AUTO-ABORT + rollback + quarantine
        │
        └── EXPERIMENT ─ route across ≥2 model configs
                        benchmark-score each ──▶ select winner (recorded evidence)
```

## Design

### Shadow runs

A shadow run executes a candidate workload on a **mirrored task** — the same input as a paired production run — and never delivers its result to any fan-out channel. Pairing links the shadow run to the production run it mirrors so diffs are apples-to-apples.

- **Isolation:** shadow runs default to a **read-only tool policy**; side-effecting tools are hard-blocked, so shadowing cannot alter external state (D4/D11).
- **Separate budget:** shadow spend is attributed to its own shadow budget cap and never consumes production budget.
- **Outcome diff:** compares production vs. candidate on outputs, tool calls, cost, latency, and policy decisions. The diff is the operator's evidence for whether to start a canary.

### Canary routing

A canary routes a configured percentage of **eligible** triggers to a candidate manifest version while the certified baseline serves the remainder. Eligibility rules restrict which triggers participate (e.g. by event type, workload, tenant, or a blast-radius cap).

- **Evaluation window:** candidate and baseline are compared on live metrics (error rate, latency, cost, policy escalations) **and** sampled judge scores (D27) over a fixed window.
- **Auto-promote:** on a clean window — no regressions, metrics within tolerance, minimum sample reached — the candidate is promoted and traffic re-points without manual action.
- **Auto-abort:** on a regression or error-rate breach, traffic rolls back to the baseline, the candidate is **quarantined** (D26), and the owner is notified with evidence.
- **Guardrails:** minimum sample size, blast-radius cap, evaluation window, and an always-available manual override (`promote` / `abort`). A canary never auto-promotes on insufficient sample size.

### Model experiment campaigns

A campaign routes traffic across **≥2 model configurations**, benchmark-scores each arm (D19), and selects the winner with recorded evidence. The campaign record stores each arm's model identity, benchmark result, aggregate metrics, and the selection rationale, so a routing decision can be audited and reproduced.

### State & audit

```
proposed → shadow → canary → promoted
                     │
                     ├── aborted   (regression) → rolled_back → quarantined
                     └── overridden (manual promote/abort)
```

Every transition records who/when/why — operator, timestamp, reason, and the evidence (diff, window metrics, sample count). Automated transitions are attributed to `progressive-delivery`; manual overrides name the operator.

## Data Model

```
shadow_runs          id, candidate_workload_id, production_run_id, input_ref, result JSONB, cost_usd, latency_ms, tool_calls JSONB, policy_decisions JSONB, budget_id, created_at
canary_rollouts      id, workload_id, baseline_version, candidate_version, traffic_pct, eligible_rule JSONB, window_start, window_end, min_sample, blast_radius_cap, state, created_at
canary_samples       id, rollout_id, run_id, arm, metrics JSONB, judge_score, sampled_at
experiment_campaigns id, workload_id, state, winner_arm_id, rationale, created_at
experiment_arms      id, campaign_id, model_identity, benchmark_run_id, score, metrics JSONB
delivery_audit       id, subject_type, subject_id, transition, actor, reason, evidence JSONB, timestamp
```

## Interfaces / API

```
POST  /shadow                       { candidate_workload_id, production_run_id }
GET   /shadow/{id}/report           → outcome diff
POST  /canary                       { workload_id, candidate_version, traffic_pct, window }
GET   /canary/{id}                  → state + window metrics + sample count
POST  /canary/{id}/promote          { operator, reason }
POST  /canary/{id}/abort            { operator, reason }
POST  /experiments                  { workload_id, arms: [{ model_identity }] }
GET   /experiments/{id}             → per-arm scores + selected winner
```

```
hiveplane shadow report <id>
hiveplane canary start <workload> --candidate <version> --pct 10
hiveplane canary status <id>
hiveplane canary promote <id> --reason "..."
hiveplane canary abort <id> --reason "..."
hiveplane experiment start <workload> --arms gpt-4o,gpt-4o-mini
```

## Failure Modes

| Condition | Behavior |
|-----------|----------|
| Shadow attempts a destructive tool | Blocked by read-only policy; recorded as a policy decision |
| Shadow result delivered | Impossible by construction; shadow runs have no fan-out target |
| Shadow exceeds budget | Shadow run stops at its own cap; production budget untouched |
| Canary regression detected | Auto-abort, rollback to baseline, candidate quarantined, owner notified |
| Insufficient sample or blast-radius exceeded | No auto-promote; extra triggers not routed; window extends or expires without action |
| Canary mid-flight, baseline changes | Rollout aborts; a new canary is required |
| Experiment arm fails benchmark | Arm is excluded; winner chosen from passing arms with evidence |
| Operator overrides | Transition recorded with actor, reason, and evidence |

## Security

- Shadow runs default to read-only tools and cannot mutate external systems (DD-14); side-effecting calls are hard-blocked, not merely discouraged.
- Canary traffic percentage, eligibility, and blast-radius caps bound the exposure of an unproven candidate.
- Auto-abort rolls back traffic and quarantines the candidate so a regression cannot persist.
- Every automated or manual transition is attributable and audited (DD-07).
- Candidate model identities are exact, never aliases, and are bound to the resulting attestation (DD-10).

## Testing

- A shadow run mirrors its paired production run's input, never delivers, and respects its own budget.
- Shadow runs cannot call destructive tools (verified by test).
- A canary routes exactly the configured fraction of eligible triggers.
- A clean canary auto-promotes and re-points traffic; a seeded regression auto-aborts and rolls back.
- Minimum-sample and blast-radius guardrails prevent premature promotion.
- An experiment selects the benchmark-best arm with recorded evidence.

## Open Questions

- How long may a canary run before it is stale, and how is blast radius measured — traffic fraction, tenants affected, or cost at risk?
- Should canary judge sampling reuse the D27 production quality score, or maintain a separate rollout-scoped rubric?
- Can shadow and canary run simultaneously for the same candidate, or must shadow gate the canary?

## See Also

- [Certification Pipeline Design](certification-pipeline-design.md) (D10) — certification, promotion gate, drift
- [Certification v2 Design](certification-v2-design.md) (D26) — quarantine, rollback, provenance
- [Learning Loop Design](learning-loop-design.md) (D27) — sampled judge scores
- [Benchmark Execution Design](benchmark-execution-design.md) (D19) — benchmark-scored experiment arms
- [Policy Engine Design](policy-engine-design.md) (D4) / [Execution Sandbox Design](execution-sandbox-design.md) (D11) — read-only shadow isolation
- [Budget Enforcement Design](budget-enforcement-design.md) (D5) — separate shadow budget
- [PRD 05: Features](../prd/05-features.md) — progressive delivery · [PRD 09: Roadmap](../prd/09-roadmap.md) — pillar D
- [WBS v0.2.0 Part 7](../wbs/v0.2.0/wbs-v0.2.0-part7-progressive-delivery.md) (M37–M38)
