"""Deterministic critique gates — the free, injection-immune layer (F-12).

These gates are pure code operating on the typed plan; they never touch a
model, so a goal crafted to corrupt the plan cannot weaken them. Each gate
returns :class:`~planner_critic.types.Finding` objects — gates never raise
on malformed input; they report a finding instead.

:func:`run_deterministic_gates` is the single entry point the loop calls,
and it runs every gate in a stable order.

.. note::
   ``parallel_safety`` is M1-scoped to its schema-level counterpart: it
   audits the unsafe cases the :class:`PlanVersion` constructor permits by
   design (e.g. parallel tasks whose preconditions race). See
   docs/design/plan-schema-design.md for the exact responsibilities split.
"""

from __future__ import annotations

from ..schema.plan import PlanVersion
from ..types import Finding, Severity
from . import (
    dep_cycles,
    ordering,
    parallel_safety,
    preconditions,
    requirement_trace,
    rollback,
    rollback_credible,
    schema_valid,
    verification,
    verification_ordering,
)
from .base import BaseGate

GATES = [
    schema_valid.Gate(),
    dep_cycles.Gate(),
    ordering.Gate(),
    verification.Gate(),
    verification_ordering.Gate(),
    rollback.Gate(),
    rollback_credible.Gate(),
    preconditions.Gate(),
    parallel_safety.Gate(),
    requirement_trace.Gate(),
]


def run_deterministic_gates(
    plan: PlanVersion,
    extra_gates: list[BaseGate] | None = None,
) -> list[Finding]:
    """Run every deterministic gate against a plan version.

    Args:
        plan: The typed plan to audit.
        extra_gates: Optional domain-pack gate evaluators to run *in
            addition* to the built-in six. Never replace built-in gates.

    Returns:
        A list of findings in gate-stable order. Empty when the plan passes
        every gate.

    The function is a pure, deterministic function of ``plan`` — identical
    input produces identical findings (F-74).
    """
    findings: list[Finding] = []
    for gate in GATES:
        findings.extend(gate.run(plan))
    if extra_gates:
        for gate in extra_gates:
            findings.extend(gate.run(plan))
    return findings


__all__ = ["GATES", "BaseGate", "Finding", "Severity", "run_deterministic_gates"]
