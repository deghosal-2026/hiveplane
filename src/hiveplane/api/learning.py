"""Run feedback API (M36-01)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from hiveplane.api.deps import get_candidate_service, get_eval_service, get_feedback_service
from hiveplane.learning.candidates import CandidateService
from hiveplane.learning.eval import EvalService
from hiveplane.learning.feedback import FeedbackService
from hiveplane.learning.models import (
    CandidateStatus,
    CorpusCandidate,
    EvalSample,
    FeedbackVerdict,
    QualityScore,
    RunFeedback,
)

router = APIRouter(tags=["learning"])

FeedbackDep = Annotated[FeedbackService, Depends(get_feedback_service)]
CandidateDep = Annotated[CandidateService, Depends(get_candidate_service)]
EvalDep = Annotated[EvalService, Depends(get_eval_service)]


class FeedbackRequest(BaseModel):
    """Request body for recording run feedback."""

    model_config = ConfigDict(extra="forbid")

    verdict: FeedbackVerdict
    notes: str = ""
    operator: str = Field(min_length=1)

    @model_validator(mode="after")
    def _lesson_requires_notes(self) -> FeedbackRequest:
        if self.verdict is FeedbackVerdict.FAILED_WITH_LESSON and not self.notes.strip():
            raise ValueError("failed-with-lesson feedback requires non-empty notes")
        return self


@router.post(
    "/runs/{run_id}/feedback",
    response_model=RunFeedback,
    status_code=status.HTTP_201_CREATED,
)
def record_feedback(
    run_id: str, payload: FeedbackRequest, service: FeedbackDep
) -> RunFeedback:
    """Record attributable operator feedback on a terminal run."""
    return service.record(
        run_id,
        verdict=payload.verdict,
        notes=payload.notes,
        operator=payload.operator,
    )


@router.get("/runs/{run_id}/feedback", response_model=list[RunFeedback])
def list_run_feedback(run_id: str, service: FeedbackDep) -> list[RunFeedback]:
    """List feedback recorded for one run."""
    return service.list(run_id=run_id)


@router.get("/feedback", response_model=list[RunFeedback])
def list_feedback(
    service: FeedbackDep,
    workload: str | None = None,
    verdict: FeedbackVerdict | None = None,
) -> list[RunFeedback]:
    """List feedback, optionally filtered by workload or verdict."""
    return service.list(workload=workload, verdict=verdict)


class ReviewRequest(BaseModel):
    """Request body for reviewing a corpus candidate."""

    model_config = ConfigDict(extra="forbid")

    reviewer: str = Field(min_length=1)
    reason: str | None = None


@router.get("/corpus/candidates", response_model=list[CorpusCandidate])
def list_candidates(
    service: CandidateDep,
    workload: str | None = None,
    status_filter: CandidateStatus | None = None,
) -> list[CorpusCandidate]:
    """List corpus candidates, optionally filtered."""
    return service.list(workload=workload, status=status_filter)


@router.post("/corpus/candidates/{candidate_id}/approve", response_model=CorpusCandidate)
def approve_candidate(
    candidate_id: str, payload: ReviewRequest, service: CandidateDep
) -> CorpusCandidate:
    """Approve a candidate, staging it for the next corpus version."""
    return service.approve(candidate_id, reviewer=payload.reviewer)


@router.post("/corpus/candidates/{candidate_id}/reject", response_model=CorpusCandidate)
def reject_candidate(
    candidate_id: str, payload: ReviewRequest, service: CandidateDep
) -> CorpusCandidate:
    """Reject a candidate, archiving it with a reason."""
    if payload.reason is None or not payload.reason.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="rejection requires a non-empty reason",
        )
    return service.reject(
        candidate_id, reviewer=payload.reviewer, reason=payload.reason
    )


@router.get("/eval/samples", response_model=list[EvalSample])
def list_eval_samples(
    service: EvalDep,
    workload: str | None = None,
    run_id: str | None = None,
) -> list[EvalSample]:
    """List production runs selected for online evaluation."""
    return service.samples(workload=workload, run_id=run_id)


@router.get("/workloads/{workload}/quality", response_model=QualityScore)
def workload_quality(
    workload: str,
    service: EvalDep,
    window: int = Query(default=50, ge=1),
) -> QualityScore:
    """Return a workload's rolling-window production quality score."""
    return service.quality(workload, window=window)
