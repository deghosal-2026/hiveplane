"""Per-tenant data-retention enforcement (M57-05, D38)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from hiveplane.artifacts.service import RetentionService
from hiveplane.auth.store import AuthStore
from hiveplane.cost.store import CostStore
from hiveplane.delivery.store import DeliveryStore
from hiveplane.execution.store import RunStore
from hiveplane.fleet.artifacts import RetentionPolicy
from hiveplane.persistence.audit import AuditLog, AuditRecord
from hiveplane.reporting.models import (
    RetentionDataClass,
    RetentionPurgeResult,
    StorePurgeCount,
)
from hiveplane.reporting.store import ReportingStore
from hiveplane.tenancy.context import SYSTEM_CONTEXT, context_for_run


class RetentionEnforcer:
    """Applies per-tenant retention policies across every data store.

    The artifact :class:`RetentionService` owns the policy records (D36/D38);
    this enforcer maps each class policy onto the store that holds that class and
    audits every purge. The audit chain is pruned only as a leading prefix so it
    stays verifiable from its anchor.
    """

    def __init__(
        self,
        reporting_store: ReportingStore,
        retention_service: RetentionService,
        run_store: RunStore,
        audit_log: AuditLog,
        cost_store: CostStore,
        auth_store: AuthStore,
        delivery_store: DeliveryStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._reporting_store = reporting_store
        self._retention = retention_service
        self._run_store = run_store
        self._audit_log = audit_log
        self._cost_store = cost_store
        self._auth_store = auth_store
        self._delivery_store = delivery_store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._tenants: set[str] = set()

    def bind_audit(self, audit: AuditLog) -> None:
        """Bind the audit log after construction (wiring order)."""
        self._audit_log = audit

    def set_policy(
        self,
        tenant_id: str,
        data_class: RetentionDataClass,
        retain_days: int,
        legal_hold: bool = False,
        *,
        actor: str = "operator",
    ) -> RetentionPolicy:
        """Create or update the deterministic policy for a tenant and class."""
        self._tenants.add(tenant_id)
        policy = RetentionPolicy(
            policy_id=f"retention:{tenant_id}:{data_class.value}"[:64],
            tenant_id=tenant_id,
            data_class=data_class.value,
            retain_days=retain_days,
            legal_hold=legal_hold,
        )
        return self._retention.set_policy(
            policy, actor=actor, ctx=context_for_run(tenant_id)
        )

    def policies(self, tenant_id: str) -> list[RetentionPolicy]:
        """Return a tenant's configured retention policies."""
        return self._retention.policies(tenant_id, ctx=context_for_run(tenant_id))

    def purge_due(
        self, *, tenant_id: str | None = None, at: datetime | None = None
    ) -> RetentionPurgeResult:
        """Delete expired data for every configured, non-held policy."""
        now = at or self._clock()
        if tenant_id is not None:
            tenants = {tenant_id}
        else:
            # Enumerate from the persisted policy store so a scheduled sweep also
            # covers policies created before this process started (or elsewhere).
            tenants = set(self._tenants) | set(self._retention.policy_tenants())
        audit_policies = self._audit_policy_map(tenants)
        classes: list[StorePurgeCount] = []
        for scope in sorted(tenants):
            for policy in self._retention.policies(
                scope, ctx=context_for_run(scope)
            ):
                if policy.legal_hold:
                    continue
                try:
                    data_class = RetentionDataClass(policy.data_class)
                except ValueError:
                    continue
                cutoff = now - timedelta(days=policy.retain_days)
                classes.extend(self._purge_class(policy, data_class, cutoff, now, audit_policies))
        return RetentionPurgeResult(classes=classes, completed_at=now)

    def _purge_class(
        self,
        policy: RetentionPolicy,
        data_class: RetentionDataClass,
        cutoff: datetime,
        now: datetime,
        audit_policies: dict[str, RetentionPolicy],
    ) -> list[StorePurgeCount]:
        tenant_id = policy.tenant_id
        scope = context_for_run(tenant_id)
        deleted = 0
        if data_class is RetentionDataClass.RUNS:
            deleted = self._run_store.delete_terminal_before(
                cutoff=cutoff,
                tenant_id=tenant_id,
                ctx=scope,
                skip=lambda run_id: self._retention.run_has_artifacts(
                    tenant_id=tenant_id, run_id=run_id, ctx=scope
                ),
            )
        elif data_class is RetentionDataClass.LOGS:
            deleted = self._auth_store.delete_events_before(
                cutoff=cutoff, tenant_id=tenant_id, ctx=scope
            )
            deleted += self._delivery_store.delete_attempts_before(
                cutoff=cutoff, tenant_id=tenant_id, ctx=scope
            )
        elif data_class is RetentionDataClass.ARTIFACTS:
            deleted = len(
                self._retention.purge_due(tenant_id=tenant_id, at=now, ctx=scope).purged
            )
        elif data_class is RetentionDataClass.METERING:
            deleted = self._cost_store.delete_events_before(
                cutoff=cutoff, tenant_id=tenant_id, ctx=scope
            )
        elif data_class is RetentionDataClass.AUDIT:
            deleted = self._audit_log.prune(
                before=now, protect=self._audit_protect(audit_policies, now)
            )
        self._audit_log.append(
            "system",
            "retention.purged",
            f"{tenant_id}:{data_class.value}",
            detail=f"store={data_class.value} deleted={deleted}",
            ctx=SYSTEM_CONTEXT,
        )
        return [StorePurgeCount(store=data_class.value, deleted=deleted)]

    def _audit_policy_map(self, tenants: set[str]) -> dict[str, RetentionPolicy]:
        policies: dict[str, RetentionPolicy] = {}
        for scope in set(self._tenants) | tenants:
            for policy in self._retention.policies(
                scope, ctx=context_for_run(scope)
            ):
                if policy.data_class == RetentionDataClass.AUDIT.value:
                    policies[scope] = policy
        return policies

    @staticmethod
    def _audit_protect(
        audit_policies: dict[str, RetentionPolicy], now: datetime
    ) -> Callable[[AuditRecord], bool]:
        def protect(record: AuditRecord) -> bool:
            policy = audit_policies.get(record.tenant_id)
            if policy is None or policy.legal_hold:
                return True
            cutoff = now - timedelta(days=policy.retain_days)
            return record.created_at >= cutoff

        return protect
