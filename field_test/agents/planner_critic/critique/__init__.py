"""Critique engine package (F-04, F-10..F-14, F-78).

The six-heuristic LLM critic that audits a plan beyond what the deterministic
gates can resolve (§2.5.1), the dual-mode dispatch (deterministic-first vs
llm-every-revision, §2.5), and diff-aware re-audit on revision N>1 (§2.5.3).

The :class:`~planner_critic.critique.critic.LLMCritic` runs a model through
the structured-output enforcer and maps structured results to typed
:class:`~planner_critic.types.Finding` objects with catalog reason codes.
"""

from __future__ import annotations

from .critic import CritiqueItem, CritiqueOutput, LLMCritic
from .diff import audit_scope, changed_tasks, dependent_closure
from .mode import CriticMode, should_invoke_llm, validate_mode

__all__ = [
    "CriticMode",
    "CritiqueItem",
    "CritiqueOutput",
    "LLMCritic",
    "audit_scope",
    "changed_tasks",
    "dependent_closure",
    "should_invoke_llm",
    "validate_mode",
]
