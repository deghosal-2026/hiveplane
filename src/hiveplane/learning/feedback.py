"""Run feedback capture service (M36-01).

Operators mark terminal production runs ``good``/``bad``/``failed-with-lesson``
with notes. Every record is attributable; ``failed-with-lesson`` is the only
verdict that later produces a corpus candidate.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from hiveplane.core.run import Run, RunState
from hiveplane.learning.candidates import CandidateService
from hiveplane.learning.errors import FeedbackNotAllowedError, FeedbackNotFoundError
from hiveplane.learning.models import FeedbackVerdict, RunFeedback
from hiveplane.learning.store import FeedbackStore
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext

#: Verdicts are only meaningful once a run has finished.
_TERMINAL_STATES = frozenset({RunState.COMPLETED, RunState.FAILED})


class RunReader(Protocol):
    """The subset of the run service feedback needs."""

    def get(self, run_id: str, *, ctx: TenantContext = ...) -> Run: ...


class FeedbackService:
    """Records and reads operator feedback on runs."""

    def __init__(
        self,
        store: FeedbackStore,
        *,
        run_reader: RunReader,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        candidates: CandidateService | None = None,
    ) -> None:
        self._store = store
        self._run_reader = run_reader
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"fb-{uuid.uuid4().hex[:12]}")
        self._candidates = candidates

    def record(
        self,
        run_id: str,
        *,
        verdict: FeedbackVerdict,
        operator: str,
        notes: str = "",
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> RunFeedback:
        """Record feedback for a terminal run (raises for unknown/non-terminal)."""
        run = self._run_reader.get(run_id, ctx=ctx)
        if run.state not in _TERMINAL_STATES:
            raise FeedbackNotAllowedError(run_id, run.state)
        record = RunFeedback(
            feedback_id=self._id_factory(),
            run_id=run.id,
            workload_id=run.workload_id,
            verdict=verdict,
            notes=notes,
            operator=operator,
            created_at=self._clock(),
            tenant_id=run.tenant_id,
        )
        self._store.add_feedback(record, ctx=ctx)
        if (
            self._candidates is not None
            and record.verdict is FeedbackVerdict.FAILED_WITH_LESSON
        ):
            self._candidates.propose(record, run, ctx=ctx)
        return record

    def get(
        self, feedback_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> RunFeedback:
        """Return a feedback record, or raise when absent/out of scope."""
        record = self._store.get_feedback(feedback_id, ctx=ctx)
        if record is None:
            raise FeedbackNotFoundError(feedback_id)
        return record

    def list(
        self,
        *,
        run_id: str | None = None,
        workload: str | None = None,
        verdict: FeedbackVerdict | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[RunFeedback]:
        """List feedback, optionally filtered."""
        return self._store.list_feedback(
            run_id=run_id, workload=workload, verdict=verdict, ctx=ctx
        )
