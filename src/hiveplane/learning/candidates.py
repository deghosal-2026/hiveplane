"""Feedback → corpus candidates and the mandatory review gate (M36-02, M36-03).

A ``failed-with-lesson`` feedback becomes an inert **candidate** corpus case. A
human must approve it before it can enter the next corpus version; rejection
archives it with a reason. There is no auto-approval path.
"""

from __future__ import annotations

import builtins
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.certification.models import (
    BenchmarkTaskCheck,
    CheckType,
    ExpectedOutcome,
)
from hiveplane.core.run import Run
from hiveplane.learning.candidate_store import CandidateStore
from hiveplane.learning.errors import (
    CandidateAlreadyExistsError,
    CandidateAlreadyReviewedError,
    CandidateNotAllowedError,
    CandidateNotFoundError,
)
from hiveplane.learning.models import (
    CandidateReview,
    CandidateStatus,
    CorpusCandidate,
    FeedbackVerdict,
    RunFeedback,
)
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext


class CandidateService:
    """Proposes corpus candidates and enforces the human review gate."""

    def __init__(
        self,
        store: CandidateStore,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        review_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"cand-{uuid.uuid4().hex[:12]}")
        self._review_id_factory = review_id_factory or (
            lambda: f"rev-{uuid.uuid4().hex[:12]}"
        )

    def propose(
        self,
        feedback: RunFeedback,
        run: Run,
        *,
        judge_hint: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> CorpusCandidate:
        """Convert a ``failed-with-lesson`` feedback into a pending candidate."""
        if feedback.verdict is not FeedbackVerdict.FAILED_WITH_LESSON:
            raise CandidateNotAllowedError(
                f"verdict {feedback.verdict.value!r} does not produce a corpus candidate; "
                "only failed-with-lesson does"
            )
        if any(
            candidate.source_run_id == feedback.run_id
            for candidate in self._store.list_candidates(ctx=ctx)
        ):
            raise CandidateAlreadyExistsError(feedback.run_id)
        expected_value = judge_hint or feedback.notes
        candidate = CorpusCandidate(
            candidate_id=self._id_factory(),
            source_run_id=feedback.run_id,
            workload_id=feedback.workload_id,
            input=dict(run.task),
            expected=ExpectedOutcome(outcome=expected_value),
            check=BenchmarkTaskCheck(
                type=CheckType.EXACT_MATCH, field="output", value=expected_value
            ),
            status=CandidateStatus.PENDING,
            lesson=feedback.notes,
            proposed_by=feedback.operator,
            created_at=self._clock(),
            tenant_id=feedback.tenant_id,
        )
        self._store.add_candidate(candidate, ctx=ctx)
        return candidate

    def get(
        self, candidate_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> CorpusCandidate:
        """Return a candidate, or raise when absent/out of scope."""
        candidate = self._store.get_candidate(candidate_id, ctx=ctx)
        if candidate is None:
            raise CandidateNotFoundError(candidate_id)
        return candidate

    def list(
        self,
        *,
        workload: str | None = None,
        status: CandidateStatus | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[CorpusCandidate]:
        """List candidates, optionally filtered."""
        return self._store.list_candidates(workload=workload, status=status, ctx=ctx)

    def approve(
        self, candidate_id: str, *, reviewer: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> CorpusCandidate:
        """Approve a pending candidate, promoting it into the next corpus version."""
        return self._review(
            candidate_id,
            decision=CandidateStatus.APPROVED,
            reviewer=reviewer,
            reason=None,
            ctx=ctx,
        )

    def reject(
        self,
        candidate_id: str,
        *,
        reviewer: str,
        reason: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> CorpusCandidate:
        """Reject a pending candidate, archiving it with a reason."""
        if not reason.strip():
            raise CandidateNotAllowedError("rejection requires a non-empty reason")
        return self._review(
            candidate_id,
            decision=CandidateStatus.REJECTED,
            reviewer=reviewer,
            reason=reason,
            ctx=ctx,
        )

    def reviews(
        self, candidate_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> builtins.list[CandidateReview]:
        """Return the review history for a candidate."""
        return self._store.list_reviews(candidate_id, ctx=ctx)

    def _review(
        self,
        candidate_id: str,
        *,
        decision: CandidateStatus,
        reviewer: str,
        reason: str | None,
        ctx: TenantContext,
    ) -> CorpusCandidate:
        candidate = self.get(candidate_id, ctx=ctx)
        if candidate.status is not CandidateStatus.PENDING:
            raise CandidateAlreadyReviewedError(candidate_id)
        updated = candidate.model_copy(update={"status": decision})
        self._store.add_candidate(updated, ctx=ctx)
        self._store.add_review(
            CandidateReview(
                review_id=self._review_id_factory(),
                candidate_id=candidate_id,
                reviewer=reviewer,
                decision=decision,
                reason=reason,
                reviewed_at=self._clock(),
                tenant_id=candidate.tenant_id,
            ),
            ctx=ctx,
        )
        return updated
