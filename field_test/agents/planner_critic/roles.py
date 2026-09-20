"""Role protocols (F-03 planner, F-04 critic) — the model-agnostic seams.

The core engine never talks to a model directly. It interacts through two
roles:

* :class:`PlannerRole` — decomposes a goal into a typed plan and revises a
  plan in response to findings.
* :class:`CriticRole` — audits a plan and returns severity-graded findings.

M1 ships the protocols plus the fake roles used to drive the loop in tests.
Concrete LLM-backed implementations land in M3 (critic) on top of the M2
provider registry; framework adapters (M5) wrap third-party agents into
these same protocols.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from .schema.goal import Goal
from .schema.plan import PlanVersion
from .types import Finding


class PlannerRole(Protocol):
    """Anything that can decompose a goal and revise a plan."""

    def decompose(self, goal: Goal) -> PlanVersion:
        """Produce a first typed plan for the goal.

        Args:
            goal: The typed planning request.

        Returns:
            A plan version (revision 1).

        Raises:
            PlanningError: If the role cannot produce a valid plan; the loop
                must fail closed rather than continue.
        """
        ...

    def revise(self, plan: PlanVersion, findings: list[Finding]) -> PlanVersion:
        """Revise a plan in response to critique findings.

        Args:
            plan: The previous plan version.
            findings: Findings (from gates and/or critic) to address.

        Returns:
            A new plan revision.

        Raises:
            PlanningError: If the role cannot produce a valid revision.
        """
        ...


@runtime_checkable
class CriticRole(Protocol):
    """Anything that audits a plan and returns findings."""

    def audit(self, plan: PlanVersion, findings: list[Finding]) -> list[Finding]:
        """Audit a plan, extending any already-collected findings.

        Args:
            plan: The current plan revision.
            findings: Findings produced so far (deterministic gates); the
                critic appends its own.

        Returns:
            The complete finding list for this revision.
        """
        ...


class BasePlanner(ABC):
    """Abstract base planner for convenience implementations."""

    @abstractmethod
    def decompose(self, goal: Goal) -> PlanVersion: ...

    @abstractmethod
    def revise(self, plan: PlanVersion, findings: list[Finding]) -> PlanVersion: ...


class BaseCritic(ABC):
    """Abstract base critic for convenience implementations."""

    @abstractmethod
    def audit(self, plan: PlanVersion, findings: list[Finding]) -> list[Finding]: ...
