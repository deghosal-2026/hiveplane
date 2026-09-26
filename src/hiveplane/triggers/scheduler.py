"""Timezone-aware cron scheduling and missed-schedule policy (M27-03, D23).

The scheduler computes which scheduled instants are due since a trigger last
fired and applies its ``missed_schedule_policy``: ``skip`` drops ticks that fell
outside the current tick window, ``catch_up`` fires at most once for a gap, and
``catch_up_all`` fires every missed interval up to ``max_catch_up``. Matching is
wall-clock in the trigger's timezone; a repeated wall-clock hour fires once.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext
from hiveplane.triggers.cron import CronExpression
from hiveplane.triggers.schema import MissedSchedulePolicy, TriggerSpec
from hiveplane.triggers.store import TriggerStore


class TriggerScheduler:
    """Decides which cron/watch ticks are due for a trigger."""

    def __init__(
        self,
        store: TriggerStore,
        *,
        clock: Callable[[], datetime] | None = None,
        tick_seconds: int = 60,
        max_catch_up: int = 10,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._tick_seconds = tick_seconds
        self._max_catch_up = max_catch_up

    def due(
        self,
        spec: TriggerSpec,
        *,
        last_fired: datetime | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[datetime]:
        """Return the scheduled instants due now, applying the missed-schedule policy."""
        if spec.schedule is None:
            return []
        now = self._clock()
        anchor = last_fired if last_fired is not None else self._last_fired(spec.id, ctx)
        anchor = anchor or (now - timedelta(seconds=self._tick_seconds))
        cron = CronExpression.parse(spec.schedule)
        zone = ZoneInfo(spec.timezone)

        missed: list[datetime] = []
        cursor = anchor
        for _ in range(self._max_catch_up * 4):
            nxt = cron.next_after(cursor, tz=zone)
            if nxt is None or nxt > now:
                break
            missed.append(nxt)
            cursor = nxt
        if not missed:
            return []
        if spec.missed_schedule_policy is MissedSchedulePolicy.SKIP:
            most_recent = missed[-1]
            if (now - most_recent).total_seconds() <= self._tick_seconds:
                return [most_recent]
            return []
        if spec.missed_schedule_policy is MissedSchedulePolicy.CATCH_UP:
            return [missed[-1]]
        return missed[-self._max_catch_up :]

    def _last_fired(self, trigger_id: str, ctx: TenantContext) -> datetime | None:
        runs = self._store.list_runs(trigger_id, ctx=ctx)
        return max((run.fired_at for run in runs), default=None)
