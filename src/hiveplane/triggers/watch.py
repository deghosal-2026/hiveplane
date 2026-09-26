"""Watch mode: scheduled 24/7 operators as a first-class trigger type (M28-03, D23).

A watch trigger fires on schedule but the workload decides whether to act. If a
prior watch run is still active, the tick is skipped (not queued) and recorded as
``skipped_concurrent``; the persisted run history means a restart resumes rather
than losing its place.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.fleet.triggers import TriggerEvent, TriggerOutcome, TriggerSource
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext
from hiveplane.triggers.engine import TriggerDecision, TriggerEngine
from hiveplane.triggers.scheduler import TriggerScheduler
from hiveplane.triggers.schema import TriggerSpec
from hiveplane.triggers.store import TriggerStore


class WatchRunner:
    """Drives scheduled watch ticks with a concurrency guard."""

    def __init__(
        self,
        store: TriggerStore,
        scheduler: TriggerScheduler,
        engine: TriggerEngine,
        *,
        active_runs: Callable[[str], int],
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._scheduler = scheduler
        self._engine = engine
        self._active_runs = active_runs
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"watch-{uuid.uuid4().hex[:12]}")

    def tick(
        self,
        spec: TriggerSpec,
        *,
        last_fired: datetime | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[TriggerDecision]:
        """Fire due watch ticks, skipping any while a prior run is still active."""
        if spec.source is not TriggerSource.WATCH:
            return []
        max_concurrent = spec.max_concurrent_runs or 1
        decisions: list[TriggerDecision] = []
        for fire_at in self._scheduler.due(spec, last_fired=last_fired, ctx=ctx):
            if self._active_runs(spec.target.ref) >= max_concurrent:
                self._record_skipped(spec, fire_at, ctx)
                continue
            decisions.append(
                self._engine.ingest(
                    spec,
                    {"scheduled_at": fire_at.isoformat()},
                    source=TriggerSource.WATCH,
                    ctx=ctx,
                )
            )
        return decisions

    def _record_skipped(
        self, spec: TriggerSpec, fire_at: datetime, ctx: TenantContext
    ) -> None:
        self._store.add_event(
            TriggerEvent(
                event_id=self._id_factory(),
                trigger_id=spec.id,
                tenant_id=ctx.tenant_id,
                source=TriggerSource.WATCH,
                payload={"scheduled_at": fire_at.isoformat()},
                received_at=self._clock(),
                outcome=TriggerOutcome.SKIPPED_CONCURRENT,
                reason=f"max_concurrent_runs={spec.max_concurrent_runs or 1} already active",
            ),
            ctx=ctx,
        )
