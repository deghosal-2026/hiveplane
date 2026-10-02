"""Storage for online-eval samples, judge scores, and rubrics (M36-05, M36-06)."""

from __future__ import annotations

import threading
from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.learning.models import EvalSample, JudgeScore, Rubric
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import EvalSampleRow, JudgeScoreRow, RubricRow
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext, TenantScopeError


class EvalStore(Protocol):
    """Storage interface for eval samples, judge scores, and rubrics."""

    def add_sample(self, sample: EvalSample, *, ctx: TenantContext = ...) -> None: ...

    def get_sample(
        self, sample_id: str, *, ctx: TenantContext = ...
    ) -> EvalSample | None: ...

    def list_samples(
        self,
        *,
        workload: str | None = None,
        run_id: str | None = None,
        ctx: TenantContext = ...,
    ) -> list[EvalSample]: ...

    def add_score(self, score: JudgeScore, *, ctx: TenantContext = ...) -> None: ...

    def list_scores(
        self,
        *,
        workload: str | None = None,
        sample_id: str | None = None,
        ctx: TenantContext = ...,
    ) -> list[JudgeScore]: ...

    def add_rubric(self, rubric: Rubric, *, ctx: TenantContext = ...) -> None: ...

    def get_rubric(
        self, name: str, version: int, *, ctx: TenantContext = ...
    ) -> Rubric | None: ...

    def list_rubrics(
        self, name: str | None = None, *, ctx: TenantContext = ...
    ) -> list[Rubric]: ...

    def clear(self) -> None: ...


class InMemoryEvalStore:
    """A process-local, thread-safe eval store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._samples: dict[str, tuple[EvalSample, str]] = {}
        self._scores: list[tuple[JudgeScore, str]] = []
        self._rubrics: list[Rubric] = []

    def add_sample(
        self, sample: EvalSample, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(sample.tenant_id)
        with self._lock:
            self._samples[sample.sample_id] = (
                sample.model_copy(deep=True),
                sample.tenant_id,
            )

    def get_sample(
        self, sample_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> EvalSample | None:
        with self._lock:
            entry = self._samples.get(sample_id)
            if entry is None or not ctx.scopes(entry[1]):
                return None
            return entry[0].model_copy(deep=True)

    def list_samples(
        self,
        *,
        workload: str | None = None,
        run_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[EvalSample]:
        with self._lock:
            samples = [
                sample
                for sample, tenant in self._samples.values()
                if ctx.scopes(tenant)
                and (workload is None or sample.workload_id == workload)
                and (run_id is None or sample.run_id == run_id)
            ]
        samples.sort(key=lambda sample: (sample.sampled_at, sample.sample_id))
        return samples

    def add_score(
        self, score: JudgeScore, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(score.tenant_id)
        with self._lock:
            self._scores.append((score.model_copy(deep=True), score.tenant_id))

    def list_scores(
        self,
        *,
        workload: str | None = None,
        sample_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[JudgeScore]:
        sample_workloads = {
            sample.sample_id: sample.workload_id
            for sample, _ in self._samples.values()
        }
        with self._lock:
            scores = [
                score
                for score, tenant in self._scores
                if ctx.scopes(tenant)
                and (sample_id is None or score.sample_id == sample_id)
                and (
                    workload is None
                    or sample_workloads.get(score.sample_id) == workload
                )
            ]
        scores.sort(key=lambda score: (score.created_at, score.score_id))
        return scores

    def add_rubric(self, rubric: Rubric, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        with self._lock:
            self._rubrics.append(rubric.model_copy(deep=True))

    def get_rubric(
        self, name: str, version: int, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> Rubric | None:
        with self._lock:
            for rubric in self._rubrics:
                if rubric.name == name and rubric.version == version:
                    return rubric.model_copy(deep=True)
            return None

    def list_rubrics(
        self, name: str | None = None, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[Rubric]:
        with self._lock:
            rubrics = [
                rubric.model_copy(deep=True)
                for rubric in self._rubrics
                if name is None or rubric.name == name
            ]
        rubrics.sort(key=lambda rubric: (rubric.name, rubric.version))
        return rubrics

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()
            self._scores.clear()
            self._rubrics.clear()


class PostgresEvalStore:
    """A durable eval store backed by PostgreSQL (M36-05, M36-06)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def add_sample(
        self, sample: EvalSample, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(sample.tenant_id)
        with self._session.begin() as session:
            row = session.get(EvalSampleRow, sample.sample_id)
            if row is None:
                session.add(
                    EvalSampleRow(
                        sample_id=sample.sample_id,
                        run_id=sample.run_id,
                        workload_id=sample.workload_id,
                        rubric_version=sample.rubric_version,
                        sampled_at=sample.sampled_at,
                        tenant_id=sample.tenant_id,
                        payload=sample.model_dump(mode="json"),
                    )
                )
            else:
                if not ctx.scopes(row.tenant_id):
                    raise TenantScopeError(
                        ctx.tenant_id, f"cannot modify sample {sample.sample_id!r}"
                    )
                row.payload = sample.model_dump(mode="json")

    def get_sample(
        self, sample_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> EvalSample | None:
        with self._session() as session:
            row = session.get(EvalSampleRow, sample_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return EvalSample.model_validate(row.payload)

    def list_samples(
        self,
        *,
        workload: str | None = None,
        run_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[EvalSample]:
        statement = select(EvalSampleRow)
        if workload is not None:
            statement = statement.where(EvalSampleRow.workload_id == workload)
        if run_id is not None:
            statement = statement.where(EvalSampleRow.run_id == run_id)
        if not ctx.is_system:
            statement = statement.where(EvalSampleRow.tenant_id == ctx.tenant_id)
        statement = statement.order_by(EvalSampleRow.sampled_at, EvalSampleRow.sample_id)
        with self._session() as session:
            return [
                EvalSample.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def add_score(
        self, score: JudgeScore, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(score.tenant_id)
        with self._session.begin() as session:
            session.add(
                JudgeScoreRow(
                    score_id=score.score_id,
                    sample_id=score.sample_id,
                    rubric_version=score.rubric_version,
                    score=score.score,
                    created_at=score.created_at,
                    tenant_id=score.tenant_id,
                    payload=score.model_dump(mode="json"),
                )
            )

    def list_scores(
        self,
        *,
        workload: str | None = None,
        sample_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[JudgeScore]:
        statement = select(JudgeScoreRow)
        if sample_id is not None:
            statement = statement.where(JudgeScoreRow.sample_id == sample_id)
        if workload is not None:
            sample_ids = [
                sample.sample_id
                for sample in self.list_samples(workload=workload, ctx=ctx)
            ]
            statement = statement.where(JudgeScoreRow.sample_id.in_(sample_ids))
        if not ctx.is_system:
            statement = statement.where(JudgeScoreRow.tenant_id == ctx.tenant_id)
        statement = statement.order_by(JudgeScoreRow.created_at, JudgeScoreRow.score_id)
        with self._session() as session:
            return [
                JudgeScore.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def add_rubric(self, rubric: Rubric, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        with self._session.begin() as session:
            row = session.get(RubricRow, rubric.rubric_id)
            if row is None:
                session.add(
                    RubricRow(
                        rubric_id=rubric.rubric_id,
                        name=rubric.name,
                        version=rubric.version,
                        created_at=rubric.created_at,
                        payload=rubric.model_dump(mode="json"),
                    )
                )

    def get_rubric(
        self, name: str, version: int, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> Rubric | None:
        statement = select(RubricRow).where(
            RubricRow.name == name, RubricRow.version == version
        )
        with self._session() as session:
            row = session.scalars(statement).first()
            if row is None:
                return None
            return Rubric.model_validate(row.payload)

    def list_rubrics(
        self, name: str | None = None, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[Rubric]:
        statement = select(RubricRow)
        if name is not None:
            statement = statement.where(RubricRow.name == name)
        statement = statement.order_by(RubricRow.name, RubricRow.version)
        with self._session() as session:
            return [Rubric.model_validate(row.payload) for row in session.scalars(statement)]

    def clear(self) -> None:
        with self._session.begin() as session:
            session.execute(delete(JudgeScoreRow))
            session.execute(delete(EvalSampleRow))
            session.execute(delete(RubricRow))


def build_eval_store(settings: Settings | None = None) -> EvalStore:
    """Build the configured eval store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresEvalStore(create_engine_from_settings(resolved))
    return InMemoryEvalStore()
