"""The smart task router engine (M30-01..M30-03).

Routes a plain-language task to the best certified workload. The router is
deliberately conservative: it considers only certified candidates, requires the
top score to clear a confidence threshold and beat the runner-up by a margin,
and otherwise refuses with ranked candidates rather than guessing. Every
decision — scores, classifier identity, and outcome — is recorded.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.core.run import AdmissionContext
from hiveplane.router.catalog import CatalogCandidate, CertifiedCatalog
from hiveplane.router.classifier import TaskClassifier
from hiveplane.router.models import (
    RefusalReason,
    RouteDecision,
    RouteOutcome,
    RouterCandidate,
)
from hiveplane.router.store import RouterStore
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext


def hash_task(task: str) -> str:
    """Return a stable digest of a task (the raw text is never stored)."""
    return hashlib.sha256(task.encode("utf-8")).hexdigest()


class RouterEngine:
    """Chooses the best certified workload for a task, or refuses."""

    def __init__(
        self,
        catalog: CertifiedCatalog,
        classifier: TaskClassifier,
        *,
        store: RouterStore | None = None,
        confidence_threshold: float = 0.6,
        margin: float = 0.15,
        context: AdmissionContext = AdmissionContext.STAGING,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._catalog = catalog
        self._classifier = classifier
        self._store = store
        self._confidence_threshold = confidence_threshold
        self._margin = margin
        self._context = context
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"route-{uuid.uuid4().hex[:12]}")

    def route(
        self,
        task: str,
        *,
        context: AdmissionContext | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> RouteDecision:
        """Route ``task`` to a certified workload, or refuse with a reason."""
        resolved = context or self._context
        candidates = self._catalog.candidates(resolved, ctx=ctx)
        if not candidates:
            decision = self._refuse(
                task, resolved, RefusalReason.NO_CANDIDATES, [], "unconfigured", ctx
            )
            return self._record(decision, ctx)
        result = self._classifier.classify(task, candidates)
        ranked = _rank(candidates, result.scores)
        top = ranked[0]
        runner_up = ranked[1].score if len(ranked) > 1 else 0.0
        if top.score < self._confidence_threshold:
            decision = self._refuse(
                task,
                resolved,
                RefusalReason.LOW_CONFIDENCE,
                ranked,
                result.model_identity,
                ctx,
            )
        elif len(ranked) > 1 and top.score - runner_up < self._margin:
            decision = self._refuse(
                task,
                resolved,
                RefusalReason.AMBIGUOUS,
                ranked,
                result.model_identity,
                ctx,
            )
        else:
            decision = RouteDecision(
                decision_id=self._id_factory(),
                tenant_id=ctx.tenant_id,
                task_hash=hash_task(task),
                context=resolved,
                classifier_model=result.model_identity,
                candidates=ranked,
                chosen=top.workload,
                outcome=RouteOutcome.ROUTED,
                confidence_threshold=self._confidence_threshold,
                margin=self._margin,
                created_at=self._clock(),
            )
        return self._record(decision, ctx)

    def _refuse(
        self,
        task: str,
        context: AdmissionContext,
        reason: RefusalReason,
        candidates: list[RouterCandidate],
        classifier_model: str,
        ctx: TenantContext,
    ) -> RouteDecision:
        return RouteDecision(
            decision_id=self._id_factory(),
            tenant_id=ctx.tenant_id,
            task_hash=hash_task(task),
            context=context,
            classifier_model=classifier_model or "unconfigured",
            candidates=candidates,
            chosen=None,
            outcome=RouteOutcome.REFUSED,
            reason=reason,
            confidence_threshold=self._confidence_threshold,
            margin=self._margin,
            created_at=self._clock(),
        )

    def _record(self, decision: RouteDecision, ctx: TenantContext) -> RouteDecision:
        if self._store is not None:
            self._store.save_decision(decision, ctx=ctx)
        return decision


def _rank(
    candidates: list[CatalogCandidate], scores: dict[str, float]
) -> list[RouterCandidate]:
    scored = [
        (candidate.workload, scores.get(candidate.workload, 0.0))
        for candidate in candidates
    ]
    scored.sort(key=lambda item: (-item[1], item[0]))
    return [
        RouterCandidate(workload=workload, score=score, rank=index + 1)
        for index, (workload, score) in enumerate(scored)
    ]
