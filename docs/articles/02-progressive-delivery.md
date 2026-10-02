# Progressive Delivery for Agents

**Status:** draft · **Pillar:** shadow runs, canary, auto-promote/abort

## Thesis

Deploying an agent change should look like deploying code: shadow it, canary it, let the
evidence decide. HivePlane makes the rollout a first-class, evaluated operation instead of a
YAML flip and a prayer.

## Audience

Teams that already gate code on canaries and progressive delivery, and want the same for
agent versions.

## Outline

1. **Shadow runs.** Execute a candidate on the *same task* as a paired production run — no
   delivery, no side effects (read-only tools hard-blocked), separate capped budget. Produce
   an outcome diff: output, tool calls, cost, latency, policy decisions.
2. **Canary routing.** Route a deterministic slice (`sha256(run_id) mod 100 < pct`) to the
   candidate while the certified baseline serves the rest, bounded by a blast-radius cap.
3. **Auto-decision with guardrails.** Error-rate and sampled judge means over a window with a
   minimum-sample gate: a clean window auto-promotes; a regression auto-aborts and quarantines.
4. **Manual override always available.** Every transition is audited (actor =
   `progressive-delivery`).
5. **Model experiments.** Route across ≥2 model configs, score each arm, pick the winner with
   recorded rationale.

## Evidence to link

- Field test S7/S8 (pipeline completion + canary 10% auto-promote)
- [Tutorial 3 — Autonomy](../tutorials/03-autonomy-triggers-pipelines.md)
- [User Guide — Progressive Delivery](../USER_GUIDE.md)
- Canary/experiment CLI: `hiveplane canary start|status|promote|abort`, `experiment start`
