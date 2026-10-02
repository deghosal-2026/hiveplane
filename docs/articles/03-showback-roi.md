# Showback, Cost-per-Task & ROI for Agent Fleets

**Status:** draft · **Pillar:** cost attribution, budgets, caching, ROI

## Thesis

You cannot operate what you cannot price. HivePlane attributes every token and dollar to a
tenant → team → workload, computes cost-per-*completed*-task (including retries and
escalations), and flags the expensive-but-low-value agents you should turn off.

## Audience

Engineering leaders and FinOps-adjacent platform teams facing "which agent is burning the
budget, and is it worth it?"

## Outline

1. **No unattributed spend.** An event that cannot be attributed is dead-lettered and raises;
   the `unattributed` counter must be zero.
2. **Four budgets.** Run / day / team, plus a live context-window token budget and a
   spend-velocity guard that pauses an anomalous burn *before* exhaustion.
3. **Budget periods and alerts.** Day/week/month buckets with carry rules; 50/80/100%
   thresholds fire once per (period, threshold).
4. **Cost-per-completed-task.** Includes retries, escalations, and cache-hit savings
   (reported separately) — the honest denominator.
5. **Attestation-bound result cache.** A hit requires a valid attestation; re-certification or
   TTL invalidates it; savings are shown.
6. **Fleet ROI.** Spend-vs-outcome rows with evidence-backed `expensive_low_value` flags.

## Evidence to link

- Field test S21/S22 (showback + cache invalidation)
- [Cost service design](../design/cost-service-design.md)
- CLI: `hiveplane cost showback|forecast|roi`, `hiveplane top`
