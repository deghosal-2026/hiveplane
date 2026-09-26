"""Tenant isolation matrix across every fleet store (#149, M25-01).

Each store proves the same three properties:
- a read outside the acting tenant looks like the record does not exist;
- a write that crosses a tenant boundary raises ``TenantScopeError``;
- the system context (internal/admin) bypasses scoping.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hiveplane.budget.models import CostAttribution
from hiveplane.budget.store import InMemoryBudgetStore
from hiveplane.certification.models import (
    Attestation,
    BenchmarkAggregate,
    BenchmarkResult,
    Certification,
    CertificationRecord,
    CertificationStatus,
    Environment,
    EvalSummary,
    Signer,
    TargetContext,
    Thresholds,
)
from hiveplane.certification.store import InMemoryCertificationStore
from hiveplane.core.approval import ApprovalRecord, ApprovalStatus
from hiveplane.core.event import EventType, RunEvent
from hiveplane.core.fanout import FanOutType
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.errors import RunNotFoundError
from hiveplane.execution.models import (
    AdmissionOutcome,
    AdmissionResult,
    DeliveryRecord,
    DeliveryStatus,
)
from hiveplane.execution.store import InMemoryRunStore, JsonFileRunStore
from hiveplane.persistence.audit import InMemoryAuditLog
from hiveplane.policy.models import PolicyPack, PolicyPackSpec
from hiveplane.policy.packs import InMemoryPolicyPackStore
from hiveplane.policy.store import InMemoryApprovalStore
from hiveplane.registry.models import WorkloadRecord
from hiveplane.registry.store import InMemoryRegistryStore
from hiveplane.tenancy import Role, TenantScopeError
from hiveplane.tenancy.context import SYSTEM_CONTEXT, TenantContext

_NOW = datetime(2026, 9, 25, tzinfo=UTC)
_A = TenantContext(tenant_id="tenant-a", role=Role.ADMIN)
_B = TenantContext(tenant_id="tenant-b", role=Role.ADMIN)


def _run(run_id: str, tenant_id: str) -> Run:
    return Run(
        id=run_id,
        workload_id="agent-1",
        caller="alice",
        state=RunState.QUEUED,
        created_at=_NOW,
        updated_at=_NOW,
        tenant_id=tenant_id,
    )


def test_run_store_hides_foreign_runs() -> None:
    store = InMemoryRunStore()
    store.save_run(_run("r1", "tenant-a"), ctx=_A)
    assert store.get_run("r1", ctx=_A) is not None
    assert store.get_run("r1", ctx=_B) is None
    assert store.list_runs(ctx=_B) == []
    assert [run.id for run in store.list_runs(ctx=SYSTEM_CONTEXT)] == ["r1"]


def test_run_store_rejects_cross_tenant_write() -> None:
    store = InMemoryRunStore()
    with pytest.raises(TenantScopeError):
        store.save_run(_run("r1", "tenant-a"), ctx=_B)


def test_run_store_same_run_id_in_two_tenants() -> None:
    store = InMemoryRunStore()
    store.save_run(_run("r1", "tenant-a"), ctx=_A)
    store.save_run(_run("r1", "tenant-b"), ctx=_B)
    assert store.get_run("r1", ctx=_A) is not None
    assert store.get_run("r1", ctx=_B) is not None
    assert len(store.list_runs(ctx=SYSTEM_CONTEXT)) == 2


def test_run_children_inherit_parent_tenant(tmp_path: Path) -> None:
    store = JsonFileRunStore(tmp_path)
    store.save_run(_run("r1", "tenant-a"), ctx=_A)
    event = RunEvent(
        run_id="r1", sequence=0, type=EventType.STATE_CHANGE, actor="api", timestamp=_NOW
    )
    store.add_event(event, ctx=_A)
    assert len(store.list_events("r1", ctx=_A)) == 1
    with pytest.raises(RunNotFoundError):
        store.list_events("r1", ctx=_B)
    reloaded = JsonFileRunStore(tmp_path)
    assert reloaded.get_run("r1", ctx=_A) is not None
    assert reloaded.get_run("r1", ctx=_B) is None


def test_admission_and_usage_are_scoped() -> None:
    store = InMemoryRunStore()
    store.save_run(_run("r1", "tenant-a"), ctx=_A)
    result = AdmissionResult(
        run_id="r1",
        workload="agent-1",
        context=AdmissionContext.SANDBOX,
        outcome=AdmissionOutcome.ADMITTED,
    )
    store.save_admission(result, ctx=_A)
    assert store.get_admission("r1", ctx=_A) is not None
    with pytest.raises(RunNotFoundError):
        store.get_admission("r1", ctx=_B)
    report = UsageReport(
        run_id="r1", input_tokens=1, output_tokens=1, tool_calls=0, cost_usd=0.1, timestamp=_NOW
    )
    store.add_usage(report, ctx=_A)
    assert len(store.list_usage("r1", ctx=_A)) == 1
    with pytest.raises(RunNotFoundError):
        store.list_usage("r1", ctx=_B)


def test_deliveries_are_scoped() -> None:
    store = InMemoryRunStore()
    store.save_run(_run("r1", "tenant-a"), ctx=_A)
    record = DeliveryRecord(
        run_id="r1",
        destination_type=FanOutType.WEBHOOK,
        target="https://example.com",
        status=DeliveryStatus.DELIVERED,
        attempts=1,
        timestamp=_NOW,
    )
    store.add_delivery(record, ctx=_A)
    assert len(store.list_deliveries("r1", ctx=_A)) == 1
    with pytest.raises(RunNotFoundError):
        store.list_deliveries("r1", ctx=_B)


def _workload_record(
    tenant_id: str, make_manifest: Callable[..., AgentWorkload]
) -> WorkloadRecord:
    manifest = make_manifest(name="agent-1")
    return WorkloadRecord(
        name=manifest.name,
        manifest=manifest,
        current_version=1,
        certification_status=CertificationStatus.CERTIFIED,
        owner=manifest.owner,
        team=manifest.team,
        runtime=manifest.spec.runtime.adapter,
        created_at=_NOW,
        updated_at=_NOW,
        tenant_id=tenant_id,
    )


def test_registry_store_hides_foreign_workloads(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    store = InMemoryRegistryStore()
    store.save_workload(_workload_record("tenant-a", make_manifest), ctx=_A)
    assert store.get_workload("agent-1", ctx=_A) is not None
    assert store.get_workload("agent-1", ctx=_B) is None
    assert store.list_workloads(ctx=_B) == []


def test_registry_store_rejects_cross_tenant_write(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    store = InMemoryRegistryStore()
    with pytest.raises(TenantScopeError):
        store.save_workload(_workload_record("tenant-a", make_manifest), ctx=_B)


def test_registry_children_inherit_workload_tenant(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    from hiveplane.certification.models import Attestation as RegAttestation

    store = InMemoryRegistryStore()
    store.save_workload(_workload_record("tenant-a", make_manifest), ctx=_A)
    attestation = RegAttestation(
        attestation_id="att-1",
        workload_id="agent-1",
        manifest_version=1,
        benchmark_version="v1",
        benchmark_run_id="br-1",
        corpus_id="corpus-1",
        corpus_version=1,
        model_identity="fake/model",
        status=CertificationStatus.CERTIFIED,
        target_context=TargetContext.PRODUCTION,
        eval_summary=EvalSummary(
            pass_rate=1.0,
            critical_failures=0,
            p95_latency_ms=10,
            tasks_passed=1,
            tasks_failed=0,
        ),
        timestamp=_NOW,
        environment=Environment(
            sandbox_image="img", runtime_adapter="raw-worker", control_plane_version="0.2.0"
        ),
        signer=Signer(identity="hiveplane", key_id="k1", signature="sig"),
    )
    with pytest.raises(TenantScopeError):
        store.add_attestation(attestation, ctx=_B)
    store.add_attestation(attestation, ctx=_A)
    assert store.get_attestation("att-1", ctx=_A) is not None
    assert store.get_attestation("att-1", ctx=_B) is None
    assert store.list_attestations("agent-1", ctx=_B) == []


def test_tools_are_scoped_to_registering_tenant() -> None:
    from hiveplane.core.tools import ToolTrustLevel
    from hiveplane.registry.models import ToolRecord

    store = InMemoryRegistryStore()
    tool = ToolRecord(
        tool_id="tool-1",
        name="echo",
        mcp_server="mcp-fabric",
        trust_level=ToolTrustLevel.READ_ONLY,
        registered_at=_NOW,
        registered_by="alice",
    )
    store.save_tool(tool, ctx=_A)
    assert store.get_tool("tool-1", ctx=_A) is not None
    assert store.get_tool("tool-1", ctx=_B) is None
    assert store.list_tools(ctx=_B) == []


def _certification_record(record_id: str, workload: str) -> CertificationRecord:
    attestation = Attestation(
        attestation_id=f"att-{record_id}",
        workload_id=workload,
        manifest_version=1,
        benchmark_version="v1",
        benchmark_run_id=f"br-{record_id}",
        corpus_id="corpus-1",
        corpus_version=1,
        model_identity="fake/model",
        status=CertificationStatus.CERTIFIED,
        target_context=TargetContext.PRODUCTION,
        eval_summary=EvalSummary(
            pass_rate=1.0,
            critical_failures=0,
            p95_latency_ms=10,
            tasks_passed=1,
            tasks_failed=0,
        ),
        timestamp=_NOW,
        environment=Environment(
            sandbox_image="img", runtime_adapter="raw-worker", control_plane_version="0.2.0"
        ),
        signer=Signer(identity="hiveplane", key_id="k1", signature="sig"),
    )
    benchmark = BenchmarkResult(
        benchmark_run_id=f"br-{record_id}",
        workload_id=workload,
        manifest_version=1,
        corpus_id="corpus-1",
        corpus_version=1,
        model_identity="fake/model",
        environment=attestation.environment,
        started_at=_NOW,
        finished_at=_NOW,
        aggregate=BenchmarkAggregate(
            total=1,
            passed=1,
            failed=0,
            pass_rate=1.0,
            critical_failures=0,
            p50_latency_ms=10,
            p95_latency_ms=10,
            total_tokens=1,
        ),
    )
    return CertificationRecord(
        record_id=record_id,
        certification=Certification(
            certification_id=f"cert-{record_id}",
            workload_id=workload,
            manifest_version=1,
            status=CertificationStatus.CERTIFIED,
            target_context=TargetContext.PRODUCTION,
            benchmark_run_id=f"br-{record_id}",
            thresholds=Thresholds(
                min_pass_rate=0.8,
                max_critical_failures=0,
                max_p95_latency_ms=30000,
            ),
            timestamp=_NOW,
            attestation_id=attestation.attestation_id,
        ),
        attestation=attestation,
        benchmark_result=benchmark,
    )


def test_certification_store_is_scoped() -> None:
    store = InMemoryCertificationStore()
    record = _certification_record("rec-1", "agent-1")
    store.add(record, ctx=_A)
    assert store.get("rec-1", ctx=_A) is not None
    assert store.get("rec-1", ctx=_B) is None
    assert store.list(ctx=_B) == []
    assert len(store.list(ctx=SYSTEM_CONTEXT)) == 1


def test_budget_store_is_scoped() -> None:
    store = InMemoryBudgetStore()
    store.add_run_spend("r1", 1.0, ctx=_A)
    store.add_day_spend("agent-1", "2026-09-25", 2.0, ctx=_A)
    store.add_team_spend("platform", "2026-09-25", 3.0, ctx=_A)
    assert store.run_spend("r1", ctx=_A) == 1.0
    assert store.run_spend("r1", ctx=_B) == 0.0
    assert store.day_spend("agent-1", "2026-09-25", ctx=_B) == 0.0
    assert store.team_spend("platform", "2026-09-25", ctx=_B) == 0.0


def test_budget_attribution_rejects_cross_tenant_write() -> None:
    store = InMemoryBudgetStore()
    attribution = CostAttribution(
        run_id="r1",
        workload="agent-1",
        team="platform",
        input_tokens=1,
        output_tokens=1,
        tool_calls=0,
        cost_usd=0.1,
        timestamp=_NOW,
        tenant_id="tenant-a",
    )
    with pytest.raises(TenantScopeError):
        store.record_attribution(attribution, ctx=_B)
    store.record_attribution(attribution, ctx=_A)
    assert store.list_attributions(ctx=_A) == [attribution]
    assert store.list_attributions(ctx=_B) == []


def test_approval_store_is_scoped() -> None:
    store = InMemoryApprovalStore()
    record = ApprovalRecord(
        approval_id="ap-1",
        run_id="r1",
        workload="agent-1",
        rule="approvals.required",
        reason="approval required",
        requested_at=_NOW,
        status=ApprovalStatus.PENDING,
        tenant_id="tenant-a",
    )
    store.save(record, ctx=_A)
    assert store.get("ap-1", ctx=_A) is not None
    assert store.get("ap-1", ctx=_B) is None
    assert store.list_approvals(ctx=_B) == []


def test_approval_store_rejects_cross_tenant_write() -> None:
    store = InMemoryApprovalStore()
    record = ApprovalRecord(
        approval_id="ap-1",
        run_id="r1",
        workload="agent-1",
        rule="approvals.required",
        reason="approval required",
        requested_at=_NOW,
        tenant_id="tenant-a",
    )
    with pytest.raises(TenantScopeError):
        store.save(record, ctx=_B)


def _pack(tenant_id: str) -> PolicyPack:
    from hiveplane.policy.models import PolicyPackMetadata

    return PolicyPack(
        metadata=PolicyPackMetadata(
            name="platform-pack", team="platform", version="v1", tenant_id=tenant_id
        ),
        spec=PolicyPackSpec(),
    )


def test_policy_pack_store_is_scoped() -> None:
    store = InMemoryPolicyPackStore()
    store.save(_pack("tenant-a"), ctx=_A)
    assert store.get("platform-pack", ctx=_A) is not None
    assert store.get("platform-pack", ctx=_B) is None
    assert store.list_packs(ctx=_B) == []
    assert store.for_team("platform", ctx=_B) == []


def test_policy_pack_store_rejects_cross_tenant_write() -> None:
    store = InMemoryPolicyPackStore()
    with pytest.raises(TenantScopeError):
        store.save(_pack("tenant-a"), ctx=_B)


def test_audit_log_filters_by_tenant_but_keeps_one_chain() -> None:
    audit = InMemoryAuditLog(clock=lambda: _NOW)
    audit.append("alice", "transition", "r1", ctx=_A)
    audit.append("bob", "transition", "r2", ctx=_B)
    assert [record.subject for record in audit.records(ctx=_A)] == ["r1"]
    assert [record.subject for record in audit.records(ctx=_B)] == ["r2"]
    assert len(audit.records(ctx=SYSTEM_CONTEXT)) == 2
    assert audit.verify()
