"""Delivery manager, notification preferences, and escalation (M51-01/05/06/07)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from hiveplane.delivery.channels import MessageSender, render
from hiveplane.delivery.models import (
    DeliveryAttempt,
    DeliveryDestination,
    DeliveryEnvelope,
    DeliveryStatus,
    EscalationRecord,
    NotificationPreference,
)
from hiveplane.delivery.store import DeliveryStore
from hiveplane.tenancy.context import DEFAULT_CONTEXT, TenantContext, context_for_run


def new_attempt_id() -> str:
    """Return a fresh opaque delivery-attempt id."""
    return f"deliv-{uuid4().hex[:20]}"


@dataclass(frozen=True)
class _BufferedBatch:
    """A BATCHED envelope awaiting its window digest."""

    envelope: DeliveryEnvelope
    destination: DeliveryDestination
    due_at: datetime
    ctx: TenantContext


class DeliveryService:
    """Delivers envelopes across nine channels, honoring prefs, with audit+retries."""

    def __init__(
        self,
        store: DeliveryStore,
        sender: MessageSender,
        *,
        clock: Callable[[], datetime] | None = None,
        max_attempts: int = 3,
        attempt_id_factory: Callable[[], str] = new_attempt_id,
        require_active: Callable[[str], None] | None = None,
    ) -> None:
        self._store = store
        self._sender = sender
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_attempts = max_attempts
        self._attempt_id_factory = attempt_id_factory
        self._require_active = require_active
        self._batches: list[_BufferedBatch] = []

    @property
    def store(self) -> DeliveryStore:
        """Return the delivery store backing this service."""
        return self._store

    def bind_require_active(self, require_active: Callable[[str], None] | None) -> None:
        """Bind the tenant-lifecycle gate applied before delivering (M58-06)."""
        self._require_active = require_active

    def set_preference(
        self,
        preference: NotificationPreference,
        *,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> None:
        """Persist a team's notification preferences."""
        self._store.save_preference(preference, ctx=ctx)

    def preference(
        self,
        team_id: str | None,
        tenant_id: str | None = None,
        *,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> NotificationPreference | None:
        """Return a team's persisted preferences within its tenant, if configured."""
        if team_id is None or tenant_id is None:
            return None
        return self._store.get_preference(tenant_id, team_id, ctx=ctx)

    def deliver(
        self,
        envelope: DeliveryEnvelope,
        destinations: list[DeliveryDestination] | None = None,
        *,
        critical: bool = False,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[DeliveryAttempt]:
        """Deliver an envelope to each destination, recorded in the audit.

        When ``destinations`` is omitted, the team's persisted preference
        destinations are used (M57-02). A bound lifecycle gate refuses delivery
        for a suspended destination tenant (M58-06).
        """
        if self._require_active is not None:
            self._require_active(envelope.tenant_id)
        scope = ctx if ctx.scopes(envelope.tenant_id) else context_for_run(envelope.tenant_id)
        resolved = destinations
        if resolved is None:
            preference = self.preference(envelope.team_id, envelope.tenant_id, ctx=scope)
            if preference is not None and preference.tenant_id == envelope.tenant_id:
                resolved = list(preference.destinations)
            else:
                resolved = []
        results: list[DeliveryAttempt] = []
        for destination in resolved:
            attempt = self._deliver_one(envelope, destination, critical=critical, ctx=scope)
            self._store.save_attempt(attempt, ctx=scope)
            results.append(attempt)
        return results

    def _deliver_one(
        self,
        envelope: DeliveryEnvelope,
        destination: DeliveryDestination,
        *,
        critical: bool,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> DeliveryAttempt:
        now = self._clock()
        status = self._classify(envelope, destination, critical=critical, now=now, ctx=ctx)
        if status is not None:
            attempt = DeliveryAttempt(
                attempt_id=self._attempt_id_factory(),
                tenant_id=envelope.tenant_id,
                event_type=envelope.event_type,
                channel=destination.channel,
                target=destination.target,
                status=status,
                attempts=0,
                run_id=envelope.run_id,
                approval_id=envelope.approval_id,
                created_at=now,
            )
            if status is DeliveryStatus.BATCHED:
                self._buffer(envelope, destination, now=now, ctx=ctx)
            return attempt
        return self._send(envelope, destination, now=now, ctx=ctx)

    def _buffer(
        self,
        envelope: DeliveryEnvelope,
        destination: DeliveryDestination,
        *,
        now: datetime,
        ctx: TenantContext,
    ) -> None:
        """Hold a BATCHED envelope until its preference window elapses."""
        preference = self.preference(envelope.team_id, envelope.tenant_id, ctx=ctx)
        window = preference.batch_window_s if preference is not None else 0
        if window <= 0:
            return
        self._batches.append(
            _BufferedBatch(
                envelope=envelope,
                destination=destination,
                due_at=now + timedelta(seconds=window),
                ctx=ctx,
            )
        )

    def flush_due(self) -> list[DeliveryAttempt]:
        """Send every buffered batch whose window has elapsed; return the attempts."""
        now = self._clock()
        due = [batch for batch in self._batches if batch.due_at <= now]
        self._batches = [batch for batch in self._batches if batch.due_at > now]
        attempts: list[DeliveryAttempt] = []
        for batch in due:
            attempts.append(self._record_send(batch, now=now))
        return attempts

    def pending_batches(self) -> int:
        """Return the number of envelopes still waiting for their batch window."""
        return len(self._batches)

    def _record_send(self, batch: _BufferedBatch, *, now: datetime) -> DeliveryAttempt:
        attempt = self._send(
            batch.envelope, batch.destination, now=now, ctx=batch.ctx
        )
        self._store.save_attempt(attempt, ctx=batch.ctx)
        return attempt

    def _send(
        self,
        envelope: DeliveryEnvelope,
        destination: DeliveryDestination,
        *,
        now: datetime,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> DeliveryAttempt:
        payload = render(destination.channel, envelope)
        error: str | None = None
        for count in range(1, self._max_attempts + 1):
            try:
                self._sender.send(destination.channel, destination.target, payload)
            except Exception as exc:
                error = str(exc)
                continue
            return DeliveryAttempt(
                attempt_id=self._attempt_id_factory(),
                tenant_id=envelope.tenant_id,
                event_type=envelope.event_type,
                channel=destination.channel,
                target=destination.target,
                status=DeliveryStatus.DELIVERED,
                attempts=count,
                run_id=envelope.run_id,
                approval_id=envelope.approval_id,
                created_at=now,
                delivered_at=self._clock(),
            )
        return DeliveryAttempt(
            attempt_id=self._attempt_id_factory(),
            tenant_id=envelope.tenant_id,
            event_type=envelope.event_type,
            channel=destination.channel,
            target=destination.target,
            status=DeliveryStatus.DEAD_LETTER,
            attempts=self._max_attempts,
            run_id=envelope.run_id,
            approval_id=envelope.approval_id,
            error=error,
            created_at=now,
        )

    def _classify(
        self,
        envelope: DeliveryEnvelope,
        destination: DeliveryDestination,
        *,
        critical: bool,
        now: datetime,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> DeliveryStatus | None:
        preference = self.preference(envelope.team_id, envelope.tenant_id, ctx=ctx)
        if preference is None or preference.tenant_id != envelope.tenant_id:
            return None
        if preference.events and envelope.event_type not in preference.events:
            return DeliveryStatus.SUPPRESSED
        if preference.channels and destination.channel not in preference.channels:
            return DeliveryStatus.SUPPRESSED
        if self._in_quiet_hours(preference, now) and not (
            critical and preference.critical_bypasses_quiet
        ):
            return DeliveryStatus.SUPPRESSED
        if preference.batch_window_s > 0:
            return DeliveryStatus.BATCHED
        return None

    @staticmethod
    def _in_quiet_hours(preference: NotificationPreference, now: datetime) -> bool:
        start, end = preference.quiet_hours_start, preference.quiet_hours_end
        if start is None or end is None:
            return False
        hour = now.hour
        if start <= end:
            return start <= hour < end
        return hour >= start or hour < end

    def attempts(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[DeliveryAttempt]:
        """Return the tenant's delivery audit (M51-07)."""
        return self._store.list_attempts(tenant_id, ctx=ctx)


class EscalationPolicy(BaseModel):
    """On-call rotation and response window for unanswered approvals (M51-05)."""

    model_config = ConfigDict(extra="forbid")

    targets: list[str] = Field(min_length=1)
    response_window_s: int = Field(default=900, gt=0)


class _Pending(BaseModel):
    approval_id: str
    tenant_id: str
    level: int
    target: str
    due_at: datetime


class EscalationService:
    """Escalates unanswered approvals to the next operator in the rotation."""

    def __init__(
        self,
        policy: EscalationPolicy,
        notifier: Callable[[str, str, str], None],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._policy = policy
        self._notifier = notifier
        self._clock = clock or (lambda: datetime.now(UTC))
        self._pending: dict[str, _Pending] = {}
        self._history: list[EscalationRecord] = []

    def register(self, approval_id: str, *, tenant_id: str = "default") -> EscalationRecord:
        """Register a pending approval; notify the first on-call operator."""
        now = self._clock()
        target = self._policy.targets[0]
        self._pending[approval_id] = _Pending(
            approval_id=approval_id,
            tenant_id=tenant_id,
            level=1,
            target=target,
            due_at=now + timedelta(seconds=self._policy.response_window_s),
        )
        self._notifier(approval_id, target, "approval requested")
        record = EscalationRecord(
            approval_id=approval_id, tenant_id=tenant_id, level=1, target=target, fired_at=now
        )
        self._history.append(record)
        return record

    def respond(self, approval_id: str) -> None:
        """Mark an approval responded so it is not escalated."""
        pending = self._pending.pop(approval_id, None)
        if pending is not None:
            self._history.append(
                EscalationRecord(
                    approval_id=approval_id,
                    tenant_id=pending.tenant_id,
                    level=pending.level,
                    target=pending.target,
                    fired_at=pending.due_at,
                    responded_at=self._clock(),
                )
            )

    def check(self) -> list[EscalationRecord]:
        """Escalate every approval whose response window elapsed."""
        now = self._clock()
        fired: list[EscalationRecord] = []
        for approval_id, pending in list(self._pending.items()):
            if pending.due_at > now:
                continue
            if pending.level >= len(self._policy.targets):
                del self._pending[approval_id]
                continue
            level = pending.level + 1
            target = self._policy.targets[level - 1]
            self._pending[approval_id] = pending.model_copy(
                update={
                    "level": level,
                    "target": target,
                    "due_at": now + timedelta(seconds=self._policy.response_window_s),
                }
            )
            self._notifier(approval_id, target, f"escalation level {level}")
            record = EscalationRecord(
                approval_id=approval_id,
                tenant_id=pending.tenant_id,
                level=level,
                target=target,
                fired_at=now,
            )
            self._history.append(record)
            fired.append(record)
        return fired

    def pending(self) -> list[_Pending]:
        """Return pending escalations."""
        return list(self._pending.values())

    def history(self) -> list[EscalationRecord]:
        """Return the escalation audit trail."""
        return list(self._history)
