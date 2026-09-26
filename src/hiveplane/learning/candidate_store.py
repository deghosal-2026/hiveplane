"""Storage for corpus candidates and their reviews (M36-02, M36-03)."""

from __future__ import annotations

import threading
from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.learning.models import CandidateReview, CandidateStatus, CorpusCandidate
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import CandidateReviewRow, CandidateRow
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext, TenantScopeError


class CandidateStore(Protocol):
    """Storage interface for corpus candidates and reviews."""

    def add_candidate(
        self, candidate: CorpusCandidate, *, ctx: TenantContext = ...
    ) -> None: ...

    def get_candidate(
        self, candidate_id: str, *, ctx: TenantContext = ...
    ) -> CorpusCandidate | None: ...

    def list_candidates(
        self,
        *,
        workload: str | None = None,
        status: CandidateStatus | None = None,
        ctx: TenantContext = ...,
    ) -> list[CorpusCandidate]: ...

    def add_review(
        self, review: CandidateReview, *, ctx: TenantContext = ...
    ) -> None: ...

    def list_reviews(
        self, candidate_id: str, *, ctx: TenantContext = ...
    ) -> list[CandidateReview]: ...

    def clear(self) -> None: ...


class InMemoryCandidateStore:
    """A process-local, thread-safe candidate store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._candidates: dict[str, tuple[CorpusCandidate, str]] = {}
        self._reviews: dict[str, list[tuple[CandidateReview, str]]] = {}

    def add_candidate(
        self, candidate: CorpusCandidate, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(candidate.tenant_id)
        with self._lock:
            self._candidates[candidate.candidate_id] = (
                candidate.model_copy(deep=True),
                candidate.tenant_id,
            )

    def get_candidate(
        self, candidate_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> CorpusCandidate | None:
        with self._lock:
            entry = self._candidates.get(candidate_id)
            if entry is None or not ctx.scopes(entry[1]):
                return None
            return entry[0].model_copy(deep=True)

    def list_candidates(
        self,
        *,
        workload: str | None = None,
        status: CandidateStatus | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[CorpusCandidate]:
        with self._lock:
            records = [
                candidate
                for candidate, tenant in self._candidates.values()
                if ctx.scopes(tenant)
                and (workload is None or candidate.workload_id == workload)
                and (status is None or candidate.status is status)
            ]
        records.sort(key=lambda candidate: (candidate.created_at, candidate.candidate_id))
        return records

    def add_review(
        self, review: CandidateReview, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(review.tenant_id)
        with self._lock:
            self._reviews.setdefault(review.candidate_id, []).append(
                (review.model_copy(deep=True), review.tenant_id)
            )

    def list_reviews(
        self, candidate_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[CandidateReview]:
        with self._lock:
            entries = self._reviews.get(candidate_id, [])
            return [
                review.model_copy(deep=True)
                for review, tenant in entries
                if ctx.scopes(tenant)
            ]

    def clear(self) -> None:
        with self._lock:
            self._candidates.clear()
            self._reviews.clear()


class PostgresCandidateStore:
    """A durable candidate store backed by PostgreSQL (M36-02, M36-03)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def add_candidate(
        self, candidate: CorpusCandidate, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(candidate.tenant_id)
        payload = candidate.model_dump(mode="json")
        with self._session.begin() as session:
            row = session.get(CandidateRow, candidate.candidate_id)
            if row is None:
                session.add(
                    CandidateRow(
                        candidate_id=candidate.candidate_id,
                        source_run_id=candidate.source_run_id,
                        workload_id=candidate.workload_id,
                        status=candidate.status.value,
                        created_at=candidate.created_at,
                        tenant_id=candidate.tenant_id,
                        payload=payload,
                    )
                )
            else:
                if not ctx.scopes(row.tenant_id):
                    raise TenantScopeError(
                        ctx.tenant_id, f"cannot modify candidate {candidate.candidate_id!r}"
                    )
                row.status = candidate.status.value
                row.payload = payload

    def get_candidate(
        self, candidate_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> CorpusCandidate | None:
        with self._session() as session:
            row = session.get(CandidateRow, candidate_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return CorpusCandidate.model_validate(row.payload)

    def list_candidates(
        self,
        *,
        workload: str | None = None,
        status: CandidateStatus | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[CorpusCandidate]:
        statement = select(CandidateRow)
        if workload is not None:
            statement = statement.where(CandidateRow.workload_id == workload)
        if status is not None:
            statement = statement.where(CandidateRow.status == status.value)
        if not ctx.is_system:
            statement = statement.where(CandidateRow.tenant_id == ctx.tenant_id)
        statement = statement.order_by(CandidateRow.created_at, CandidateRow.candidate_id)
        with self._session() as session:
            return [
                CorpusCandidate.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def add_review(
        self, review: CandidateReview, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(review.tenant_id)
        with self._session.begin() as session:
            session.add(
                CandidateReviewRow(
                    review_id=review.review_id,
                    candidate_id=review.candidate_id,
                    decision=review.decision.value,
                    reviewer=review.reviewer,
                    reviewed_at=review.reviewed_at,
                    tenant_id=review.tenant_id,
                    payload=review.model_dump(mode="json"),
                )
            )

    def list_reviews(
        self, candidate_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[CandidateReview]:
        statement = select(CandidateReviewRow).where(
            CandidateReviewRow.candidate_id == candidate_id
        )
        if not ctx.is_system:
            statement = statement.where(CandidateReviewRow.tenant_id == ctx.tenant_id)
        statement = statement.order_by(
            CandidateReviewRow.reviewed_at, CandidateReviewRow.review_id
        )
        with self._session() as session:
            return [
                CandidateReview.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def clear(self) -> None:
        with self._session.begin() as session:
            session.execute(delete(CandidateReviewRow))
            session.execute(delete(CandidateRow))


def build_candidate_store(settings: Settings | None = None) -> CandidateStore:
    """Build the configured candidate store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresCandidateStore(create_engine_from_settings(resolved))
    return InMemoryCandidateStore()
