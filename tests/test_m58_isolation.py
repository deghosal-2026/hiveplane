"""Adversarial tenant-isolation suite (M58-07, M58-08).

Release-gate scenario: *"Per-tenant budget/policy/key isolation verified"* and
*"Cross-tenant read/write attempts return 403/404 consistently"* (v0.2.0 Final
Release Gate). This suite attacks each boundary the way a hostile tenant would
and asserts it holds: a read outside the acting tenant looks nonexistent, a
write that crosses the boundary raises ``TenantScopeError``, a suspended tenant
cannot act, and budget/policy/key state is invisible to every other tenant.

The exhaustive per-store matrix lives in ``test_tenant_scoping_isolation``; this
module adds the cross-cutting adversarial layer and the determinism guarantee.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from hiveplane.api.app import create_app
from hiveplane.auth.models import AuthenticationError
from hiveplane.budget.models import CostAttribution
from hiveplane.budget.store import InMemoryBudgetStore
from hiveplane.certification.store import InMemoryCertificationStore
from hiveplane.core.approval import ApprovalRecord, ApprovalStatus
from hiveplane.core.run import AdmissionContext
from hiveplane.cost.store import InMemoryCostStore
from hiveplane.delivery.store import InMemoryDeliveryStore
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.policy.packs import InMemoryPolicyPackStore
from hiveplane.policy.store import InMemoryApprovalStore
from hiveplane.reporting.store import InMemoryReportingStore
from hiveplane.secrets.store import InMemorySecretStore
from hiveplane.tenancy import Role, TenantScopeError
from hiveplane.tenancy.context import SYSTEM_CONTEXT, TenantContext
from hiveplane.tenancy.errors import TenantSuspendedError
from hiveplane.worker.store import InMemoryWorkerStore
from test_tenant_scoping_isolation import (
    _certification_record,
    _cost_event,
    _delivery_attempt,
    _pack,
    _report_run,
    _run,
    _secret_record,
    _worker,
)

ManifestFactory = Callable[..., Any]

_CTX_A = TenantContext(tenant_id="tenant-a", role=Role.ADMIN)
_CTX_B = TenantContext(tenant_id="tenant-b", role=Role.ADMIN)
_NOW = datetime(2026, 9, 25, tzinfo=UTC)

#: Each case returns ``(write, visible)``:
#: - ``write(ctx)`` persists a record owned by tenant-a using ``ctx``;
#: - ``visible(ctx)`` reports whether tenant-a's record is readable under ``ctx``.
_Visible = Callable[[TenantContext], bool]
_BoundaryCase = Callable[[], tuple[Callable[[TenantContext], None], _Visible]]


def _runs() -> tuple[Callable[[TenantContext], None], Callable[[TenantContext], bool]]:
    store = InMemoryRunStore()
    return (
        lambda c: store.save_run(_run("r1", "tenant-a"), ctx=c),
        lambda c: store.get_run("r1", ctx=c) is not None,
    )


def _certifications() -> tuple[Callable[[TenantContext], None], Callable[[TenantContext], bool]]:
    store = InMemoryCertificationStore()
    return (
        lambda c: store.add(_certification_record("rec-1", "agent-a"), ctx=c),
        lambda c: store.get("rec-1", ctx=c) is not None,
    )


def _budgets() -> tuple[Callable[[TenantContext], None], Callable[[TenantContext], bool]]:
    store = InMemoryBudgetStore()

    def write(c: TenantContext) -> None:
        store.record_attribution(
            CostAttribution(
                run_id="r1",
                workload="agent-a",
                team="platform",
                input_tokens=1,
                output_tokens=1,
                tool_calls=0,
                cost_usd=0.1,
                timestamp=_NOW,
                tenant_id="tenant-a",
            ),
            ctx=c,
        )

    return write, lambda c: store.list_attributions(ctx=c) != []


def _approvals() -> tuple[Callable[[TenantContext], None], Callable[[TenantContext], bool]]:
    store = InMemoryApprovalStore()
    record = ApprovalRecord(
        approval_id="ap-1",
        run_id="r1",
        workload="agent-a",
        rule="approvals.required",
        reason="approval required",
        requested_at=_NOW,
        status=ApprovalStatus.PENDING,
        tenant_id="tenant-a",
    )
    return (
        lambda c: store.save(record, ctx=c),
        lambda c: store.get("ap-1", ctx=c) is not None,
    )


def _policy_packs() -> tuple[Callable[[TenantContext], None], Callable[[TenantContext], bool]]:
    store = InMemoryPolicyPackStore()
    return (
        lambda c: store.save(_pack("tenant-a"), ctx=c),
        lambda c: store.get("platform-pack", ctx=c) is not None,
    )


def _reports() -> tuple[Callable[[TenantContext], None], Callable[[TenantContext], bool]]:
    store = InMemoryReportingStore()
    return (
        lambda c: store.save_report(_report_run("rep-1", "tenant-a"), ctx=c),
        lambda c: store.get_report("rep-1", tenant_id="tenant-a", ctx=c) is not None,
    )


def _secrets() -> tuple[Callable[[TenantContext], None], Callable[[TenantContext], bool]]:
    store = InMemorySecretStore()
    return (
        lambda c: store.save_secret(_secret_record("tenant-a"), ctx=c),
        lambda c: store.get_secret("tenant-a", "db", ctx=c) is not None,
    )


def _costs() -> tuple[Callable[[TenantContext], None], Callable[[TenantContext], bool]]:
    store = InMemoryCostStore()
    return (
        lambda c: store.save_event(_cost_event("tenant-a"), ctx=c),
        lambda c: store.list_events("tenant-a", ctx=c) != [],
    )


def _deliveries() -> tuple[Callable[[TenantContext], None], Callable[[TenantContext], bool]]:
    store = InMemoryDeliveryStore()
    return (
        lambda c: store.save_attempt(_delivery_attempt("tenant-a"), ctx=c),
        lambda c: store.list_attempts("tenant-a", ctx=c) != [],
    )


def _workers() -> tuple[Callable[[TenantContext], None], Callable[[TenantContext], bool]]:
    store = InMemoryWorkerStore()
    return (
        lambda c: store.save_worker(_worker("tenant-a"), ctx=c),
        lambda c: store.get_worker("w1", "tenant-a", ctx=c) is not None,
    )


_CASES: dict[str, _BoundaryCase] = {
    "runs": _runs,
    "certifications": _certifications,
    "budgets": _budgets,
    "approvals": _approvals,
    "policy_packs": _policy_packs,
    "reports": _reports,
    "secrets": _secrets,
    "costs": _costs,
    "deliveries": _deliveries,
    "workers": _workers,
}


@pytest.mark.parametrize("name", sorted(_CASES))
def test_store_boundary_hides_reads_and_denies_foreign_writes(name: str) -> None:
    write, visible = _CASES[name]()
    write(_CTX_A)
    assert visible(_CTX_A) is True
    assert visible(_CTX_B) is False
    with pytest.raises(TenantScopeError):
        write(_CTX_B)


def test_store_matrix_is_deterministic() -> None:
    def snapshot() -> dict[str, tuple[bool, bool]]:
        result: dict[str, tuple[bool, bool]] = {}
        for case_name, factory in _CASES.items():
            write, visible = factory()
            write(_CTX_A)
            result[case_name] = (visible(_CTX_A), visible(_CTX_B))
        return result

    assert snapshot() == snapshot()


def test_no_existence_leak_between_foreign_and_absent() -> None:
    app = create_app()
    app.state.secret_service.put("tenant-a", "db", "s3cr3t", ctx=_CTX_A)
    client = _client(app)

    foreign = client.get("/secrets/db", headers=_as("tenant-b"))
    absent = client.get("/secrets/nope", headers=_as("tenant-b"))
    assert foreign.status_code == absent.status_code == 404


def test_api_keys_are_isolated_per_tenant() -> None:
    from hiveplane.auth.service import AuthService
    from hiveplane.auth.store import InMemoryAuthStore

    service = AuthService(InMemoryAuthStore())
    key_a = service.keys.create("tenant-a", Role.ADMIN, ctx=SYSTEM_CONTEXT)
    key_b = service.keys.create("tenant-b", Role.ADMIN, ctx=SYSTEM_CONTEXT)

    assert {record.key_id for record in service.keys.list_keys("tenant-a", ctx=_CTX_A)} == {
        key_a.key_id
    }
    with pytest.raises(AuthenticationError):
        service.keys.revoke(key_b.key_id, ctx=_CTX_A)


def test_policy_packs_are_isolated_per_tenant() -> None:
    app = create_app()
    app.state.policy_pack_registry.publish(_pack("tenant-a"), ctx=_CTX_A)
    app.state.policy_pack_registry.publish(_pack("tenant-b"), ctx=_CTX_B)

    assert [p.metadata.tenant_id for p in app.state.policy_pack_store.list_packs(ctx=_CTX_A)] == [
        "tenant-a"
    ]
    assert [p.metadata.tenant_id for p in app.state.policy_pack_store.list_packs(ctx=_CTX_B)] == [
        "tenant-b"
    ]


def test_suspended_tenant_cannot_submit_or_authenticate(make_manifest: ManifestFactory) -> None:
    from hiveplane.auth.service import AuthService
    from hiveplane.auth.store import InMemoryAuthStore

    app = create_app()
    admin = app.state.tenant_admin_service
    admin.create_tenant(SYSTEM_CONTEXT, tenant_id="tenant-a", name="A")
    app.state.registry_service.create(make_manifest(name="agent-a"), ctx=_CTX_A)
    admin.suspend_tenant(SYSTEM_CONTEXT, "tenant-a")

    with pytest.raises(TenantSuspendedError):
        app.state.run_service.submit(
            workload="agent-a",
            caller="alice",
            context=AdmissionContext.SANDBOX,
            ctx=_CTX_A,
        )

    service = AuthService(InMemoryAuthStore(), require_active=admin.require_active)
    key = service.keys.create("tenant-a", Role.ADMIN, ctx=SYSTEM_CONTEXT)
    with pytest.raises(TenantSuspendedError):
        service.authenticate_key(key.token)


def _as(tenant: str) -> dict[str, str]:
    from hiveplane.api.deps import TENANT_HEADER

    return {TENANT_HEADER: tenant}


def _client(app: Any) -> Any:
    from fastapi.testclient import TestClient

    return TestClient(app)


def test_run_submission_reads_the_registry_under_the_acting_tenant(
    make_manifest: ManifestFactory,
) -> None:
    from hiveplane.registry.errors import WorkloadNotFoundError

    app = create_app()
    app.state.tenant_admin_service.create_tenant(SYSTEM_CONTEXT, tenant_id="tenant-a", name="A")
    app.state.tenant_admin_service.create_tenant(SYSTEM_CONTEXT, tenant_id="tenant-b", name="B")
    app.state.registry_service.create(make_manifest(name="agent-a"), ctx=_CTX_A)

    with pytest.raises(WorkloadNotFoundError):
        app.state.run_service.submit(
            workload="agent-a",
            caller="bob",
            context=AdmissionContext.SANDBOX,
            ctx=_CTX_B,
        )

    try:
        app.state.run_service.submit(
            workload="agent-a",
            caller="alice",
            context=AdmissionContext.SANDBOX,
            ctx=_CTX_A,
        )
    except WorkloadNotFoundError as exc:  # pragma: no cover - regression guard
        raise AssertionError("tenant-a could not see its own workload") from exc
    except Exception:
        pass
