"""The cron/watch tick: fire due scheduled triggers across tenants (M27-03/M28-03).

The API stores and validates scheduled triggers but nothing evaluates them; this
runner is the driver a jittered background tick calls. It enumerates enabled
triggers system-wide, then evaluates each under its own tenant context: cron
triggers ingest every due instant, watch triggers go through the concurrency
guard in :class:`~hiveplane.triggers.watch.WatchRunner`.
"""

from __future__ import annotations

from hiveplane.fleet.triggers import TriggerSource
from hiveplane.tenancy import SYSTEM_CONTEXT, TenantContext
from hiveplane.tenancy.context import context_for_run
from hiveplane.triggers.engine import TriggerDecision, TriggerEngine
from hiveplane.triggers.scheduler import TriggerScheduler
from hiveplane.triggers.store import TriggerStore
from hiveplane.triggers.watch import WatchRunner


class TriggerRunner:
    """Evaluates due cron and watch triggers and hands them to the engine."""

    def __init__(
        self,
        store: TriggerStore,
        scheduler: TriggerScheduler,
        engine: TriggerEngine,
        watch: WatchRunner,
    ) -> None:
        self._store = store
        self._scheduler = scheduler
        self._engine = engine
        self._watch = watch

    def tick(
        self, *, ctx: TenantContext = SYSTEM_CONTEXT
    ) -> list[TriggerDecision]:
        """Fire every due scheduled trigger; one bad trigger never stops the tick."""
        decisions: list[TriggerDecision] = []
        for spec in self._store.list_triggers(enabled=True, ctx=ctx):
            tick_ctx = context_for_run(spec.tenant_id)
            if spec.source is TriggerSource.CRON:
                for fire_at in self._scheduler.due(spec, ctx=tick_ctx):
                    decisions.append(
                        self._engine.ingest(
                            spec,
                            {"scheduled_at": fire_at.isoformat()},
                            source=TriggerSource.CRON,
                            ctx=tick_ctx,
                        )
                    )
            elif spec.source is TriggerSource.WATCH:
                decisions.extend(self._watch.tick(spec, ctx=tick_ctx))
        return decisions
