"""Storage for delivery attempts, approval decisions, and used tokens (M51)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from sqlalchemy import Engine, delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from hiveplane.config import Settings, get_settings
from hiveplane.delivery.models import (
    ApprovalDecisionRecord,
    DeliveryAttempt,
    NotificationPreference,
)
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import (
    ApprovalDecisionRow,
    DeliveryAttemptRow,
    NotificationPreferenceRow,
    UsedApprovalTokenRow,
)
from hiveplane.tenancy.context import DEFAULT_CONTEXT, TenantContext


class DeliveryStore(Protocol):
    """Storage interface for the delivery/approval audit."""

    def save_attempt(
        self, attempt: DeliveryAttempt, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def list_attempts(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[DeliveryAttempt]: ...

    def delete_attempts_before(
        self, *, cutoff: datetime, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> int: ...

    def save_decision(
        self, decision: ApprovalDecisionRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def get_decision(
        self, approval_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ApprovalDecisionRecord | None: ...

    def list_decisions(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ApprovalDecisionRecord]: ...

    def claim_token(self, token_id: str) -> bool: ...

    def save_preference(
        self, preference: NotificationPreference, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def get_preference(
        self, tenant_id: str, team_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> NotificationPreference | None: ...

    def list_preferences(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[NotificationPreference]: ...

    def purge_tenant(self, tenant_id: str) -> int: ...

    def clear(self) -> None: ...


class InMemoryDeliveryStore:
    """A process-local delivery store."""

    def __init__(self) -> None:
        self._attempts: dict[str, DeliveryAttempt] = {}
        self._decisions: dict[str, ApprovalDecisionRecord] = {}
        self._tokens: set[str] = set()
        self._token_lock = threading.Lock()
        self._preferences: dict[tuple[str, str], NotificationPreference] = {}

    def save_attempt(
        self, attempt: DeliveryAttempt, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(attempt.tenant_id)
        self._attempts[attempt.attempt_id] = attempt.model_copy(deep=True)

    def list_attempts(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[DeliveryAttempt]:
        if not ctx.scopes(tenant_id):
            return []
        return [
            attempt.model_copy(deep=True)
            for _, attempt in sorted(self._attempts.items())
            if attempt.tenant_id == tenant_id
        ]

    def delete_attempts_before(
        self, *, cutoff: datetime, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> int:
        if not ctx.scopes(tenant_id):
            return 0
        stale = [
            attempt_id
            for attempt_id, attempt in self._attempts.items()
            if attempt.tenant_id == tenant_id and attempt.created_at < cutoff
        ]
        for attempt_id in stale:
            del self._attempts[attempt_id]
        return len(stale)

    def save_decision(
        self, decision: ApprovalDecisionRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(decision.tenant_id)
        self._decisions[decision.approval_id] = decision.model_copy(deep=True)

    def get_decision(
        self, approval_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ApprovalDecisionRecord | None:
        decision = self._decisions.get(approval_id)
        if decision is None or not ctx.scopes(decision.tenant_id):
            return None
        return decision.model_copy(deep=True)

    def list_decisions(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ApprovalDecisionRecord]:
        if not ctx.scopes(tenant_id):
            return []
        return [
            decision.model_copy(deep=True)
            for _, decision in sorted(self._decisions.items())
            if decision.tenant_id == tenant_id
        ]

    def claim_token(self, token_id: str) -> bool:
        """Atomically claim a single-use approval token; False if already used."""
        with self._token_lock:
            if token_id in self._tokens:
                return False
            self._tokens.add(token_id)
            return True

    def save_preference(
        self, preference: NotificationPreference, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(preference.tenant_id)
        key = (preference.tenant_id, preference.team_id)
        self._preferences[key] = preference.model_copy(deep=True)

    def get_preference(
        self, tenant_id: str, team_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> NotificationPreference | None:
        if not ctx.scopes(tenant_id):
            return None
        preference = self._preferences.get((tenant_id, team_id))
        return None if preference is None else preference.model_copy(deep=True)

    def list_preferences(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[NotificationPreference]:
        if not ctx.scopes(tenant_id):
            return []
        return [
            preference.model_copy(deep=True)
            for _, preference in sorted(self._preferences.items())
            if preference.tenant_id == tenant_id
        ]

    def purge_tenant(self, tenant_id: str) -> int:
        attempt_ids = [
            attempt_id
            for attempt_id, attempt in self._attempts.items()
            if attempt.tenant_id == tenant_id
        ]
        for attempt_id in attempt_ids:
            del self._attempts[attempt_id]
        decision_ids = [
            approval_id
            for approval_id, decision in self._decisions.items()
            if decision.tenant_id == tenant_id
        ]
        for approval_id in decision_ids:
            del self._decisions[approval_id]
        preference_keys = [
            key
            for key, preference in self._preferences.items()
            if preference.tenant_id == tenant_id
        ]
        for key in preference_keys:
            del self._preferences[key]
        return len(attempt_ids) + len(decision_ids) + len(preference_keys)

    def clear(self) -> None:
        self._attempts.clear()
        self._decisions.clear()
        self._tokens.clear()
        self._preferences.clear()


class PostgresDeliveryStore:
    """A durable delivery store backed by PostgreSQL (M51)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save_attempt(
        self, attempt: DeliveryAttempt, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(attempt.tenant_id)
        with self._session.begin() as session:
            session.merge(
                DeliveryAttemptRow(
                    attempt_id=attempt.attempt_id,
                    tenant_id=attempt.tenant_id,
                    event_type=attempt.event_type.value,
                    channel=attempt.channel.value,
                    target=attempt.target,
                    status=attempt.status.value,
                    attempts=attempt.attempts,
                    created_at=attempt.created_at,
                    payload=attempt.model_dump(mode="json"),
                )
            )

    def list_attempts(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[DeliveryAttempt]:
        if not ctx.scopes(tenant_id):
            return []
        with self._session() as session:
            rows = session.scalars(
                select(DeliveryAttemptRow)
                .where(DeliveryAttemptRow.tenant_id == tenant_id)
                .order_by(DeliveryAttemptRow.created_at)
            ).all()
        return [DeliveryAttempt.model_validate(row.payload) for row in rows]

    def delete_attempts_before(
        self, *, cutoff: datetime, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> int:
        if not ctx.scopes(tenant_id):
            return 0
        with self._session.begin() as session:
            result = cast("CursorResult[Any]", session.execute(
                delete(DeliveryAttemptRow).where(
                    DeliveryAttemptRow.tenant_id == tenant_id,
                    DeliveryAttemptRow.created_at < cutoff,
                )
            ))
            return int(result.rowcount or 0)

    def save_decision(
        self, decision: ApprovalDecisionRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(decision.tenant_id)
        with self._session.begin() as session:
            session.merge(
                ApprovalDecisionRow(
                    approval_id=decision.approval_id,
                    tenant_id=decision.tenant_id,
                    operator_id=decision.operator_id,
                    decision=decision.decision,
                    channel=decision.channel,
                    decided_at=decision.decided_at,
                    payload=decision.model_dump(mode="json"),
                )
            )

    def get_decision(
        self, approval_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ApprovalDecisionRecord | None:
        with self._session() as session:
            row = session.get(ApprovalDecisionRow, approval_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return ApprovalDecisionRecord.model_validate(row.payload)

    def list_decisions(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ApprovalDecisionRecord]:
        if not ctx.scopes(tenant_id):
            return []
        with self._session() as session:
            rows = session.scalars(
                select(ApprovalDecisionRow)
                .where(ApprovalDecisionRow.tenant_id == tenant_id)
                .order_by(ApprovalDecisionRow.decided_at)
            ).all()
        return [ApprovalDecisionRecord.model_validate(row.payload) for row in rows]

    def claim_token(self, token_id: str) -> bool:
        """Atomically claim a single-use token via a unique-key insert."""
        try:
            with self._session.begin() as session:
                session.add(
                    UsedApprovalTokenRow(token_id=token_id, used_at=datetime.now(UTC))
                )
        except IntegrityError:
            return False
        return True

    def save_preference(
        self, preference: NotificationPreference, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(preference.tenant_id)
        with self._session.begin() as session:
            session.merge(
                NotificationPreferenceRow(
                    team_id=preference.team_id,
                    tenant_id=preference.tenant_id,
                    payload=preference.model_dump(mode="json"),
                )
            )

    def get_preference(
        self, tenant_id: str, team_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> NotificationPreference | None:
        if not ctx.scopes(tenant_id):
            return None
        with self._session() as session:
            row = session.get(NotificationPreferenceRow, (tenant_id, team_id))
            if row is None:
                return None
            return NotificationPreference.model_validate(row.payload)

    def list_preferences(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[NotificationPreference]:
        if not ctx.scopes(tenant_id):
            return []
        with self._session() as session:
            rows = session.scalars(
                select(NotificationPreferenceRow)
                .where(NotificationPreferenceRow.tenant_id == tenant_id)
                .order_by(NotificationPreferenceRow.team_id)
            ).all()
        return [NotificationPreference.model_validate(row.payload) for row in rows]

    def purge_tenant(self, tenant_id: str) -> int:
        with self._session.begin() as session:
            total = 0
            for table in (
                DeliveryAttemptRow,
                ApprovalDecisionRow,
                NotificationPreferenceRow,
            ):
                result = cast(
                    "CursorResult[Any]",
                    session.execute(
                        delete(table).where(table.tenant_id == tenant_id)
                    ),
                )
                total += int(result.rowcount or 0)
            return total

    def clear(self) -> None:
        with self._session.begin() as session:
            session.execute(delete(ApprovalDecisionRow))
            session.execute(delete(DeliveryAttemptRow))
            session.execute(delete(NotificationPreferenceRow))
            session.execute(delete(UsedApprovalTokenRow))


def build_delivery_store(settings: Settings | None = None) -> DeliveryStore:
    """Build the configured delivery store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresDeliveryStore(create_engine_from_settings(resolved))
    return InMemoryDeliveryStore()
