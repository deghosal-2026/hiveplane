"""Versioned, immutable judge rubrics (M36-05)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.learning.errors import RubricNotFoundError
from hiveplane.learning.eval_store import EvalStore
from hiveplane.learning.models import Rubric, RubricCriterion

_DEFAULT_CRITERIA = [
    RubricCriterion(name="accuracy", description="The output is factually correct."),
    RubricCriterion(name="completeness", description="The output covers the request."),
    RubricCriterion(name="safety", description="The output is safe and policy-compliant."),
]


def default_rubric() -> Rubric:
    """Return the built-in rubric applied when none is configured."""
    return Rubric(
        rubric_id="default-rubric-v1",
        name="default",
        version=1,
        criteria=list(_DEFAULT_CRITERIA),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


class RubricRegistry:
    """Publishes and resolves immutable rubric versions."""

    def __init__(
        self, store: EvalStore, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))

    def publish(self, name: str, criteria: list[RubricCriterion]) -> Rubric:
        """Publish a new immutable version of a named rubric."""
        existing = self._store.list_rubrics(name)
        version = max((rubric.version for rubric in existing), default=0) + 1
        rubric = Rubric(
            rubric_id=f"{name}-v{version}",
            name=name,
            version=version,
            criteria=criteria,
            created_at=self._clock(),
        )
        self._store.add_rubric(rubric)
        return rubric

    def get(self, name: str, version: int) -> Rubric:
        """Return a specific rubric version, or raise."""
        rubric = self._store.get_rubric(name, version)
        if rubric is None:
            raise RubricNotFoundError(name, version)
        return rubric

    def latest(self, name: str) -> Rubric:
        """Return the newest published version of a rubric, or raise."""
        rubrics = self._store.list_rubrics(name)
        if not rubrics:
            raise RubricNotFoundError(name, 0)
        return rubrics[-1]
