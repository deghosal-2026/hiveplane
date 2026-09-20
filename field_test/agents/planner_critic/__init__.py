"""PlannerCritic Engine — the pre-execution planning-quality engine.

Hierarchical task planning with an independent LLM critic. A ``PlannerRole``
decomposes a goal into a typed plan; a ``CriticRole`` audits every subtask;
``run_loop`` revises until approval or escalates to a human. The core engine
is model- and framework-agnostic: it speaks plain typed JSON and never
executes a plan itself.

The public surface is minimal:

* :class:`Engine` — the convenience wrapper around :func:`~.loop.run_loop`.
* ``__version__`` — the installed package version.
* The core types every consumer uses (``Finding``, ``ApprovedPlan``,
  ``Escalation``, ...).

Safe import — importing ``planner_critic`` pulls in only the core engine
modules; the LLM transports, stores, adapters, and servers are opt-in.
"""

from __future__ import annotations

from .engine import Engine
from .types import (
    ApprovedPlan,
    Escalation,
    ExecutionTrace,
    Finding,
    PlanComplexity,
    PlanningError,
)

__version__ = "0.2.3"

__all__ = [
    "ApprovedPlan",
    "Engine",
    "Escalation",
    "ExecutionTrace",
    "Finding",
    "PlanComplexity",
    "PlanningError",
    "__version__",
]
