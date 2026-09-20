"""Shadow mode (F-14, PRD §2.7c) — the adoption wedge.

``dry_run`` runs the full planner→critic→revise→approve loop and records what
PlannerCritic **would** have blocked / approved / escalated — without gating
execution. This is the tooltrust ``dry_run`` pattern: deploy in observe mode,
compare against your current planner's output, tune, then flip to enforce.

The plan store records shadow decisions distinctly (``mode: shadow``) so a
diff between shadow and live decisions is one query.
"""

from __future__ import annotations

from dataclasses import replace

from .loop import LoopConfig, LoopResult, run_loop
from .roles import CriticRole, PlannerRole
from .schema.goal import Goal
from .store.base import PlanStore


def run_shadow(
    goal: Goal,
    planner: PlannerRole,
    critic: CriticRole,
    store: PlanStore | None = None,
    config: LoopConfig | None = None,
) -> LoopResult:
    """Run the loop in observe mode and record the decision as shadow (F-14).

    Args:
        goal: The typed planning request.
        planner: Role that decomposes/revises plans.
        critic: Role that audits plans.
        store: Optional store to persist the shadow decision; when None the
            decision is returned without persistence (still mode:shadow).
        config: Loop tuning; defaults to deterministic-first with cap 3.

    Returns:
        The loop result stamped ``mode="shadow"``. Gating is never applied —
        the caller decides what to do with the would-be decision.
    """
    result = run_loop(goal, planner, critic, config=config)
    shadow = replace(result, mode="shadow")
    if store is not None:
        _record_shadow(store, shadow)
    return shadow


def _record_shadow(store: PlanStore, result: LoopResult) -> None:
    """Persist the shadow decision so it is diffable vs live decisions.

    Args:
        store: The plan store to write into.
        result: The shadow loop result.
    """
    if result.plan is not None:
        store.put_plan_version(result.plan)
        if result.findings:
            store.put_findings(result.plan.id, result.plan.version, result.findings)
    if result.escalation is not None:
        store.put_escalation(result.escalation)
