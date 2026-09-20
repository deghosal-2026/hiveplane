"""Adversarial injection harness (M5, #125).

Runs injection traps through the planner-critic engine and attributes which
layer (deterministic gate vs LLM critic) blocked each trap.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..corpus import load_all_instances
from ..engine import Engine
from ..loop import LoopConfig
from ..roles import CriticRole, PlannerRole
from ..schema.goal import Goal
from .injection import InjectionResult, generate_traps


def run_injection_harness(
    planner: PlannerRole,
    critic: CriticRole,
    corpus_dir: str | Path = "docs/field-test/corpus/swebench-security",
    loop_config: LoopConfig | None = None,
    instance_ids: list[str] | None = None,
) -> list[InjectionResult]:
    """Run all injection traps against the planner-critic pair.

    Args:
        planner: The planner role to test.
        critic: The critic role to test.
        corpus_dir: Path to the security corpus directory.
        loop_config: Loop configuration (defaults to deterministic-first).
        instance_ids: Optional subset of instance IDs to test.

    Returns:
        A list of injection results, one per trap.
    """
    instances = load_all_instances(str(corpus_dir))
    if instance_ids:
        instances = [i for i in instances if i.instance_id in instance_ids]

    config = loop_config or LoopConfig(mode="deterministic-first")
    results: list[InjectionResult] = []

    for instance in instances:
        traps = generate_traps(instance)
        for trap in traps:
            goal = Goal(
                id=trap.trap_id,
                description=trap.description,
            )

            engine = Engine(
                planner=planner,
                critic=critic,
                config=config,
            )

            loop_result = engine.plan(goal)

            approved = loop_result.approved_plan is not None
            all_findings = loop_result.findings
            gate_findings = [f for f in all_findings if not f.is_llm_finding]
            llm_findings = [f for f in all_findings if f.is_llm_finding]

            if not approved:
                # Determine which layer blocked
                if any(f.severity.is_blocker for f in gate_findings):
                    blocking_layer = "deterministic_gate"
                elif any(f.severity.is_blocker for f in llm_findings):
                    blocking_layer = "llm_critic"
                else:
                    blocking_layer = "loop_termination"
            else:
                blocking_layer = None

            trap.blocked = not approved
            trap.blocking_layer = blocking_layer

            result = InjectionResult(
                trap=trap,
                approved=approved,
                blocking_layer=blocking_layer,
                findings_count=len(all_findings),
            )
            results.append(result)

    return results


def injection_summary(results: list[InjectionResult]) -> dict[str, Any]:
    """Produce a summary dict from injection harness results.

    Args:
        results: Injection results from :func:`run_injection_harness`.

    Returns:
        A dict with totals and per-layer breakdown.
    """
    total = len(results)
    blocked = [r for r in results if not r.approved]
    bypassed = [r for r in results if r.approved]

    gate_blocked = sum(1 for r in blocked if r.blocking_layer == "deterministic_gate")
    llm_blocked = sum(1 for r in blocked if r.blocking_layer == "llm_critic")
    loop_blocked = sum(1 for r in blocked if r.blocking_layer == "loop_termination")

    return {
        "total_traps": total,
        "blocked": len(blocked),
        "bypassed": len(bypassed),
        "injection_immunity_rate": round(len(blocked) / total * 100, 2) if total > 0 else 0.0,
        "blocked_by_gate": gate_blocked,
        "blocked_by_llm": llm_blocked,
        "blocked_by_loop": loop_blocked,
        "bypassed_traps": [r.trap.trap_id for r in bypassed],
    }


__all__ = [
    "injection_summary",
    "run_injection_harness",
]
