"""Online evaluation: sample production runs, judge them, and roll up quality
(M36-05, M36-06, M36-07)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.core.run import Run
from hiveplane.learning.eval_store import EvalStore
from hiveplane.learning.judge import RubricJudge
from hiveplane.learning.models import (
    EvalSample,
    JudgeScore,
    QualityScore,
    Rubric,
)
from hiveplane.learning.rubrics import RubricRegistry
from hiveplane.learning.sampling import is_pii, should_sample
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext


class EvalService:
    """Samples runs, records judge scores, and aggregates production quality."""

    def __init__(
        self,
        store: EvalStore,
        *,
        judge: RubricJudge,
        rubrics: RubricRegistry,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        score_id_factory: Callable[[], str] | None = None,
        quality_target: float | None = None,
    ) -> None:
        self._store = store
        self._judge = judge
        self._rubrics = rubrics
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"sample-{uuid.uuid4().hex[:12]}")
        self._score_id_factory = score_id_factory or (
            lambda: f"score-{uuid.uuid4().hex[:12]}"
        )
        self._quality_target = quality_target

    def maybe_sample(
        self,
        run: Run,
        *,
        sample_rate: int,
        rubric: Rubric,
        pii_patterns: tuple[str, ...] = (),
        cost_cap_usd: float | None = None,
        spent_usd: float = 0.0,
        pii: bool = False,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> EvalSample | None:
        """Select a run for evaluation, honoring PII and cost guardrails.

        Returns ``None`` when the run is not selected, is PII-marked/matches a
        sensitive pattern, or the judge cost cap is already exhausted.
        """
        if pii or is_pii(run, pii_patterns):
            return None
        if cost_cap_usd is not None and spent_usd >= cost_cap_usd:
            return None
        if not should_sample(run.id, sample_rate):
            return None
        sample = EvalSample(
            sample_id=self._id_factory(),
            run_id=run.id,
            workload_id=run.workload_id,
            rubric_version=rubric.version,
            sampled_at=self._clock(),
            tenant_id=run.tenant_id,
        )
        self._store.add_sample(sample, ctx=ctx)
        return sample

    def score(
        self,
        sample: EvalSample,
        run: Run,
        *,
        rubric: Rubric,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> JudgeScore:
        """Judge a sampled run and record the score under the rubric version."""
        result = self._judge.judge(run, rubric)
        score = JudgeScore(
            score_id=self._score_id_factory(),
            sample_id=sample.sample_id,
            rubric_version=sample.rubric_version,
            score=result.score,
            criteria=result.criteria,
            model_identity=result.model_identity,
            created_at=self._clock(),
            tenant_id=sample.tenant_id,
        )
        self._store.add_score(score, ctx=ctx)
        return score

    def samples(
        self,
        *,
        workload: str | None = None,
        run_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[EvalSample]:
        """List eval samples, optionally filtered."""
        return self._store.list_samples(workload=workload, run_id=run_id, ctx=ctx)

    def scores(
        self,
        *,
        workload: str | None = None,
        sample_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[JudgeScore]:
        """List recorded judge scores, optionally filtered."""
        return self._store.list_scores(
            workload=workload, sample_id=sample_id, ctx=ctx
        )

    def quality(
        self,
        workload: str,
        *,
        window: int = 50,
        quality_target: float | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> QualityScore:
        """Return the rolling-window production quality score for a workload.

        A mean score below ``quality_target`` sets ``dip`` so the health/drift
        layer can alert and schedule an early re-certification.
        """
        scores = self._store.list_scores(workload=workload, ctx=ctx)[-window:]
        target = quality_target if quality_target is not None else self._quality_target
        if not scores:
            return QualityScore(
                workload_id=workload,
                window=window,
                sample_count=0,
                mean_score=0.0,
                quality_target=target,
                dip=False,
            )
        mean = sum(score.score for score in scores) / len(scores)
        dip = target is not None and mean < target
        return QualityScore(
            workload_id=workload,
            window=window,
            sample_count=len(scores),
            mean_score=mean,
            quality_target=target,
            dip=dip,
        )
