"""Framework adapters for the PlannerCritic Engine (M5).

Each adapter wraps the core :class:`~planner_critic.engine.Engine` into a
consumable shape for a specific agent framework (LangGraph, CrewAI, PydanticAI,
OpenAI Agents SDK) or exposes a plain Python entry point.

All adapters optionally produce an :class:`AuditTrail` so callers can observe
when a plan was requested, approved, re-gate-checked, or re-planned.
"""

from ._audit import AuditEvent, AuditTrail
from .python import PlannerCriticPlan, plan

__all__ = [
    "AuditEvent",
    "AuditTrail",
    "PlannerCriticPlan",
    "plan",
]
