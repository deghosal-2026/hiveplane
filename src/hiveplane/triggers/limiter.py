"""Dedup, cooldown, rate limits, and backpressure (M27-04/M27-05, D23).

Dedup and cooldown read persisted event history, so restarts do not forget
in-flight keys. The per-trigger token bucket returns a client-retryable rate
rejection; a global ingest bucket protects the control plane under a storm and
rejects excess as backpressure. Nothing is silently dropped — every decision is
returned so the caller can record it in the event history.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from hiveplane.fleet.triggers import TriggerEvent, TriggerOutcome
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext
from hiveplane.triggers.schema import TriggerSpec
from hiveplane.triggers.store import TriggerStore


class LimiterDecision(StrEnum):
    """The disposition of an event after dedup/cooldown/rate checks."""

    ACCEPT = "accept"
    DEDUPLICATED = "deduplicated"
    SUPPRESSED_COOLDOWN = "suppressed_cooldown"
    REJECTED_RATE = "rejected_rate"
    REJECTED_BACKPRESSURE = "rejected_backpressure"

    @property
    def outcome(self) -> TriggerOutcome | None:
        """Map the decision onto the recorded event outcome, if not accepted."""
        if self is LimiterDecision.ACCEPT:
            return None
        return TriggerOutcome(self.value)


class TokenBucket:
    """A token bucket refilling at ``rate_per_minute`` with ``burst`` headroom."""

    def __init__(
        self,
        *,
        rate_per_minute: int,
        burst: int,
        clock: Callable[[], datetime],
    ) -> None:
        self._rate = rate_per_minute / 60.0
        self._capacity = float(rate_per_minute + burst)
        self._tokens = self._capacity
        self._clock = clock
        self._updated = clock()

    def allow(self, now: datetime | None = None) -> bool:
        """Consume a token if one is available, refilling for elapsed time."""
        moment = now or self._clock()
        elapsed = max(0.0, (moment - self._updated).total_seconds())
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._updated = moment
        if self._tokens < 1.0:
            return False
        self._tokens -= 1.0
        return True


class TriggerLimiter:
    """Applies dedup, cooldown, per-trigger rate limits, and backpressure."""

    def __init__(
        self,
        store: TriggerStore,
        *,
        clock: Callable[[], datetime] | None = None,
        global_max_per_minute: int = 600,
        global_burst: int = 0,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._buckets: dict[str, TokenBucket] = {}
        self._global = TokenBucket(
            rate_per_minute=global_max_per_minute,
            burst=global_burst,
            clock=self._clock,
        )

    def check(
        self,
        spec: TriggerSpec,
        *,
        dedup_key: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> LimiterDecision:
        """Return the disposition of an event for ``spec``."""
        now = self._clock()
        if spec.dedup is not None and dedup_key is not None:
            since = now - timedelta(minutes=spec.dedup.window_minutes)
            if self._store.find_dedup_event(
                spec.id, dedup_key, since=since, ctx=ctx
            ) is not None:
                return LimiterDecision.DEDUPLICATED
        if spec.cooldown_seconds > 0:
            last = self._last_accepted(spec.id, ctx=ctx)
            if last is not None and (
                now - last.received_at
            ).total_seconds() < spec.cooldown_seconds:
                return LimiterDecision.SUPPRESSED_COOLDOWN
        if spec.rate_limit is not None:
            bucket = self._bucket_for(spec)
            if not bucket.allow(now):
                return LimiterDecision.REJECTED_RATE
        if not self._global.allow(now):
            return LimiterDecision.REJECTED_BACKPRESSURE
        return LimiterDecision.ACCEPT

    def _last_accepted(
        self, trigger_id: str, *, ctx: TenantContext
    ) -> TriggerEvent | None:
        accepted = [
            event
            for event in self._store.list_events(trigger_id, ctx=ctx)
            if event.outcome is TriggerOutcome.ACCEPTED
        ]
        return accepted[-1] if accepted else None

    def _bucket_for(self, spec: TriggerSpec) -> TokenBucket:
        bucket = self._buckets.get(spec.id)
        if bucket is None:
            assert spec.rate_limit is not None
            bucket = TokenBucket(
                rate_per_minute=spec.rate_limit.max_per_minute,
                burst=spec.rate_limit.burst,
                clock=self._clock,
            )
            self._buckets[spec.id] = bucket
        return bucket
