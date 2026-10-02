"""Storage for progressive delivery — shadow, canary, and experiments (M37, M38)."""

from __future__ import annotations

import threading
from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import (
    CanaryRolloutRow,
    CanarySampleRow,
    ExperimentArmRow,
    ExperimentCampaignRow,
    ShadowRunRow,
)
from hiveplane.progressive.models import (
    CanaryRollout,
    CanarySample,
    ExperimentArm,
    ExperimentCampaign,
    ShadowRun,
)
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext, TenantScopeError


class ProgressiveStore(Protocol):
    """Storage interface for progressive-delivery records."""

    def add_shadow_run(
        self, record: ShadowRun, *, ctx: TenantContext = ...
    ) -> None: ...

    def get_shadow_run(
        self, shadow_run_id: str, *, ctx: TenantContext = ...
    ) -> ShadowRun | None: ...

    def list_shadow_runs(
        self,
        *,
        workload: str | None = None,
        budget_id: str | None = None,
        ctx: TenantContext = ...,
    ) -> list[ShadowRun]: ...

    def add_canary_rollout(
        self, record: CanaryRollout, *, ctx: TenantContext = ...
    ) -> None: ...

    def get_canary_rollout(
        self, rollout_id: str, *, ctx: TenantContext = ...
    ) -> CanaryRollout | None: ...

    def list_canary_rollouts(
        self,
        *,
        workload: str | None = None,
        ctx: TenantContext = ...,
    ) -> list[CanaryRollout]: ...

    def add_canary_sample(
        self, record: CanarySample, *, ctx: TenantContext = ...
    ) -> None: ...

    def list_canary_samples(
        self, rollout_id: str, *, ctx: TenantContext = ...
    ) -> list[CanarySample]: ...

    def add_campaign(
        self, record: ExperimentCampaign, *, ctx: TenantContext = ...
    ) -> None: ...

    def get_campaign(
        self, campaign_id: str, *, ctx: TenantContext = ...
    ) -> ExperimentCampaign | None: ...

    def list_campaigns(
        self, *, workload: str | None = None, ctx: TenantContext = ...
    ) -> list[ExperimentCampaign]: ...

    def add_arm(
        self, record: ExperimentArm, *, ctx: TenantContext = ...
    ) -> None: ...

    def get_arm(
        self, arm_id: str, *, ctx: TenantContext = ...
    ) -> ExperimentArm | None: ...

    def list_arms(
        self, campaign_id: str, *, ctx: TenantContext = ...
    ) -> list[ExperimentArm]: ...

    def clear(self) -> None: ...


class InMemoryProgressiveStore:
    """A process-local, thread-safe progressive store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._shadow_runs: dict[str, tuple[ShadowRun, str]] = {}
        self._rollouts: dict[str, tuple[CanaryRollout, str]] = {}
        self._samples: list[tuple[CanarySample, str]] = []
        self._campaigns: dict[str, tuple[ExperimentCampaign, str]] = {}
        self._arms: list[tuple[ExperimentArm, str]] = []

    def add_shadow_run(
        self, record: ShadowRun, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(record.tenant_id)
        with self._lock:
            self._shadow_runs[record.shadow_run_id] = (
                record.model_copy(deep=True),
                record.tenant_id,
            )

    def get_shadow_run(
        self, shadow_run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ShadowRun | None:
        with self._lock:
            entry = self._shadow_runs.get(shadow_run_id)
            if entry is None or not ctx.scopes(entry[1]):
                return None
            return entry[0].model_copy(deep=True)

    def list_shadow_runs(
        self,
        *,
        workload: str | None = None,
        budget_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[ShadowRun]:
        with self._lock:
            records = [
                record
                for record, tenant in self._shadow_runs.values()
                if ctx.scopes(tenant)
                and (workload is None or record.candidate_workload_id == workload)
                and (budget_id is None or record.budget_id == budget_id)
            ]
        records.sort(key=lambda record: (record.created_at, record.shadow_run_id))
        return records

    def add_canary_rollout(
        self, record: CanaryRollout, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(record.tenant_id)
        with self._lock:
            self._rollouts[record.rollout_id] = (
                record.model_copy(deep=True),
                record.tenant_id,
            )

    def get_canary_rollout(
        self, rollout_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> CanaryRollout | None:
        with self._lock:
            entry = self._rollouts.get(rollout_id)
            if entry is None or not ctx.scopes(entry[1]):
                return None
            return entry[0].model_copy(deep=True)

    def list_canary_rollouts(
        self, *, workload: str | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[CanaryRollout]:
        with self._lock:
            records = [
                record
                for record, tenant in self._rollouts.values()
                if ctx.scopes(tenant) and (workload is None or record.workload_id == workload)
            ]
        records.sort(key=lambda record: (record.created_at, record.rollout_id))
        return records

    def add_canary_sample(
        self, record: CanarySample, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(record.tenant_id)
        with self._lock:
            self._samples.append((record.model_copy(deep=True), record.tenant_id))

    def list_canary_samples(
        self, rollout_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[CanarySample]:
        with self._lock:
            samples = [
                sample
                for sample, tenant in self._samples
                if ctx.scopes(tenant) and sample.rollout_id == rollout_id
            ]
        samples.sort(key=lambda sample: (sample.sampled_at, sample.sample_id))
        return samples

    def add_campaign(
        self, record: ExperimentCampaign, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(record.tenant_id)
        with self._lock:
            self._campaigns[record.campaign_id] = (
                record.model_copy(deep=True),
                record.tenant_id,
            )

    def get_campaign(
        self, campaign_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ExperimentCampaign | None:
        with self._lock:
            entry = self._campaigns.get(campaign_id)
            if entry is None or not ctx.scopes(entry[1]):
                return None
            return entry[0].model_copy(deep=True)

    def list_campaigns(
        self, *, workload: str | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ExperimentCampaign]:
        with self._lock:
            records = [
                record
                for record, tenant in self._campaigns.values()
                if ctx.scopes(tenant) and (workload is None or record.workload_id == workload)
            ]
        records.sort(key=lambda record: (record.created_at, record.campaign_id))
        return records

    def add_arm(
        self, record: ExperimentArm, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(record.tenant_id)
        with self._lock:
            for index, (existing, _) in enumerate(self._arms):
                if existing.arm_id == record.arm_id:
                    self._arms[index] = (record.model_copy(deep=True), record.tenant_id)
                    return
            self._arms.append((record.model_copy(deep=True), record.tenant_id))

    def get_arm(
        self, arm_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ExperimentArm | None:
        with self._lock:
            for record, tenant in self._arms:
                if record.arm_id == arm_id and ctx.scopes(tenant):
                    return record.model_copy(deep=True)
            return None

    def list_arms(
        self, campaign_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ExperimentArm]:
        with self._lock:
            arms = [
                arm
                for arm, tenant in self._arms
                if ctx.scopes(tenant) and arm.campaign_id == campaign_id
            ]
        arms.sort(key=lambda arm: (arm.created_at, arm.arm_id))
        return arms

    def clear(self) -> None:
        with self._lock:
            self._shadow_runs.clear()
            self._rollouts.clear()
            self._samples.clear()
            self._campaigns.clear()
            self._arms.clear()


class PostgresProgressiveStore:
    """A durable progressive store backed by PostgreSQL (M37, M38)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def add_shadow_run(
        self, record: ShadowRun, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(record.tenant_id)
        payload = record.model_dump(mode="json")
        with self._session.begin() as session:
            row = session.get(ShadowRunRow, record.shadow_run_id)
            if row is None:
                session.add(
                    ShadowRunRow(
                        shadow_run_id=record.shadow_run_id,
                        candidate_workload_id=record.candidate_workload_id,
                        production_run_id=record.production_run_id,
                        budget_id=record.budget_id,
                        status=record.status.value,
                        created_at=record.created_at,
                        tenant_id=record.tenant_id,
                        payload=payload,
                    )
                )
            else:
                if not ctx.scopes(row.tenant_id):
                    raise TenantScopeError(
                        ctx.tenant_id, f"cannot modify shadow run {record.shadow_run_id!r}"
                    )
                row.status = record.status.value
                row.payload = payload

    def get_shadow_run(
        self, shadow_run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ShadowRun | None:
        with self._session() as session:
            row = session.get(ShadowRunRow, shadow_run_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return ShadowRun.model_validate(row.payload)

    def list_shadow_runs(
        self,
        *,
        workload: str | None = None,
        budget_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[ShadowRun]:
        statement = select(ShadowRunRow)
        if workload is not None:
            statement = statement.where(ShadowRunRow.candidate_workload_id == workload)
        if budget_id is not None:
            statement = statement.where(ShadowRunRow.budget_id == budget_id)
        if not ctx.is_system:
            statement = statement.where(ShadowRunRow.tenant_id == ctx.tenant_id)
        statement = statement.order_by(ShadowRunRow.created_at, ShadowRunRow.shadow_run_id)
        with self._session() as session:
            return [
                ShadowRun.model_validate(row.payload) for row in session.scalars(statement)
            ]

    def add_canary_rollout(
        self, record: CanaryRollout, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(record.tenant_id)
        payload = record.model_dump(mode="json")
        with self._session.begin() as session:
            row = session.get(CanaryRolloutRow, record.rollout_id)
            if row is None:
                session.add(
                    CanaryRolloutRow(
                        rollout_id=record.rollout_id,
                        workload_id=record.workload_id,
                        state=record.state.value,
                        created_at=record.created_at,
                        tenant_id=record.tenant_id,
                        payload=payload,
                    )
                )
            else:
                if not ctx.scopes(row.tenant_id):
                    raise TenantScopeError(
                        ctx.tenant_id, f"cannot modify rollout {record.rollout_id!r}"
                    )
                row.state = record.state.value
                row.payload = payload

    def get_canary_rollout(
        self, rollout_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> CanaryRollout | None:
        with self._session() as session:
            row = session.get(CanaryRolloutRow, rollout_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return CanaryRollout.model_validate(row.payload)

    def list_canary_rollouts(
        self, *, workload: str | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[CanaryRollout]:
        statement = select(CanaryRolloutRow)
        if workload is not None:
            statement = statement.where(CanaryRolloutRow.workload_id == workload)
        if not ctx.is_system:
            statement = statement.where(CanaryRolloutRow.tenant_id == ctx.tenant_id)
        statement = statement.order_by(
            CanaryRolloutRow.created_at, CanaryRolloutRow.rollout_id
        )
        with self._session() as session:
            return [
                CanaryRollout.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def add_canary_sample(
        self, record: CanarySample, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(record.tenant_id)
        with self._session.begin() as session:
            session.add(
                CanarySampleRow(
                    sample_id=record.sample_id,
                    rollout_id=record.rollout_id,
                    run_id=record.run_id,
                    arm=record.arm.value,
                    sampled_at=record.sampled_at,
                    tenant_id=record.tenant_id,
                    payload=record.model_dump(mode="json"),
                )
            )

    def list_canary_samples(
        self, rollout_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[CanarySample]:
        statement = select(CanarySampleRow).where(CanarySampleRow.rollout_id == rollout_id)
        if not ctx.is_system:
            statement = statement.where(CanarySampleRow.tenant_id == ctx.tenant_id)
        statement = statement.order_by(CanarySampleRow.sampled_at, CanarySampleRow.sample_id)
        with self._session() as session:
            return [
                CanarySample.model_validate(row.payload) for row in session.scalars(statement)
            ]

    def add_campaign(
        self, record: ExperimentCampaign, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(record.tenant_id)
        payload = record.model_dump(mode="json")
        with self._session.begin() as session:
            row = session.get(ExperimentCampaignRow, record.campaign_id)
            if row is None:
                session.add(
                    ExperimentCampaignRow(
                        campaign_id=record.campaign_id,
                        workload_id=record.workload_id,
                        state=record.state.value,
                        created_at=record.created_at,
                        tenant_id=record.tenant_id,
                        payload=payload,
                    )
                )
            else:
                if not ctx.scopes(row.tenant_id):
                    raise TenantScopeError(
                        ctx.tenant_id, f"cannot modify campaign {record.campaign_id!r}"
                    )
                row.state = record.state.value
                row.payload = payload

    def get_campaign(
        self, campaign_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ExperimentCampaign | None:
        with self._session() as session:
            row = session.get(ExperimentCampaignRow, campaign_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return ExperimentCampaign.model_validate(row.payload)

    def list_campaigns(
        self, *, workload: str | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ExperimentCampaign]:
        statement = select(ExperimentCampaignRow)
        if workload is not None:
            statement = statement.where(ExperimentCampaignRow.workload_id == workload)
        if not ctx.is_system:
            statement = statement.where(ExperimentCampaignRow.tenant_id == ctx.tenant_id)
        statement = statement.order_by(
            ExperimentCampaignRow.created_at, ExperimentCampaignRow.campaign_id
        )
        with self._session() as session:
            return [
                ExperimentCampaign.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def add_arm(
        self, record: ExperimentArm, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(record.tenant_id)
        payload = record.model_dump(mode="json")
        with self._session.begin() as session:
            row = session.get(ExperimentArmRow, record.arm_id)
            if row is None:
                session.add(
                    ExperimentArmRow(
                        arm_id=record.arm_id,
                        campaign_id=record.campaign_id,
                        model_identity=record.model_identity,
                        benchmark_run_id=record.benchmark_run_id,
                        score=record.score,
                        created_at=record.created_at,
                        tenant_id=record.tenant_id,
                        payload=payload,
                    )
                )
            else:
                if not ctx.scopes(row.tenant_id):
                    raise TenantScopeError(
                        ctx.tenant_id, f"cannot modify arm {record.arm_id!r}"
                    )
                row.score = record.score
                row.benchmark_run_id = record.benchmark_run_id
                row.payload = payload

    def get_arm(
        self, arm_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ExperimentArm | None:
        with self._session() as session:
            row = session.get(ExperimentArmRow, arm_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return ExperimentArm.model_validate(row.payload)

    def list_arms(
        self, campaign_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ExperimentArm]:
        statement = select(ExperimentArmRow).where(
            ExperimentArmRow.campaign_id == campaign_id
        )
        if not ctx.is_system:
            statement = statement.where(ExperimentArmRow.tenant_id == ctx.tenant_id)
        statement = statement.order_by(ExperimentArmRow.created_at, ExperimentArmRow.arm_id)
        with self._session() as session:
            return [
                ExperimentArm.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def clear(self) -> None:
        with self._session.begin() as session:
            session.execute(delete(CanarySampleRow))
            session.execute(delete(CanaryRolloutRow))
            session.execute(delete(ExperimentArmRow))
            session.execute(delete(ExperimentCampaignRow))
            session.execute(delete(ShadowRunRow))


def build_progressive_store(settings: Settings | None = None) -> ProgressiveStore:
    """Build the configured progressive store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresProgressiveStore(create_engine_from_settings(resolved))
    return InMemoryProgressiveStore()
