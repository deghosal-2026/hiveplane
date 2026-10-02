"""Tenant isolation matrix across every fleet store (#149, M25-01, M58-01).

Each store proves the same three properties:
- a read outside the acting tenant looks like the record does not exist;
- a write that crosses a tenant boundary raises ``TenantScopeError``;
- the system context (internal/admin) bypasses scoping.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
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
from hiveplane.reporting.models import ReportKind, ReportRun
from hiveplane.reporting.store import InMemoryReportingStore
from hiveplane.tenancy import Role, TenantScopeError
from hiveplane.tenancy.context import SYSTEM_CONTEXT, TenantContext, context_for_run

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


def test_tools_are_platform_global() -> None:
    """Tools are shared platform definitions, visible to every tenant.

    A workload registered in a second tenant must be able to reference the tools
    the platform seeded (found by the v0.2.0 field test S16). Tool *ownership*
    still governs modification, but read visibility is global.
    """
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
    assert store.get_tool("tool-1", ctx=_B) is not None
    assert [entry.tool_id for entry in store.list_tools(ctx=_B)] == ["tool-1"]


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


def test_policy_pack_store_allows_the_same_name_in_two_tenants() -> None:
    store = InMemoryPolicyPackStore()
    store.save(_pack("tenant-a"), ctx=_A)
    store.save(_pack("tenant-b"), ctx=_B)

    pack_a = store.get("platform-pack", ctx=_A)
    pack_b = store.get("platform-pack", ctx=_B)
    assert pack_a is not None and pack_a.metadata.tenant_id == "tenant-a"
    assert pack_b is not None and pack_b.metadata.tenant_id == "tenant-b"


def _deny_destructive(tenant_id: str) -> PolicyPack:
    from hiveplane.core.decision import ActionClass, DecisionOutcome
    from hiveplane.policy.models import (
        PolicyPackMetadata,
        PolicyPackOverride,
        PolicyPackRule,
        PolicyPackRuleMatch,
    )

    return PolicyPack(
        metadata=PolicyPackMetadata(
            name="platform-pack", team="platform", version="v1", tenant_id=tenant_id
        ),
        spec=PolicyPackSpec(
            overrides=[
                PolicyPackOverride(
                    match=PolicyPackRuleMatch(environment=AdmissionContext.PRODUCTION),
                    rules=[
                        PolicyPackRule(
                            action=DecisionOutcome.DENY,
                            action_class=ActionClass.DESTRUCTIVE,
                        )
                    ],
                )
            ]
        ),
    )


def test_policy_engine_uses_the_runs_tenant() -> None:
    from hiveplane.core.decision import ActionClass, PolicyContext
    from hiveplane.core.tools import ToolRef, ToolsSpec, ToolTrustLevel
    from hiveplane.policy.engine import PolicyEngine

    store = InMemoryPolicyPackStore()
    store.save(_deny_destructive("tenant-a"), ctx=_A)
    engine = PolicyEngine(store, clock=lambda: _NOW)
    context_a = PolicyContext(
        run_id="r-a",
        workload="agent-1",
        team="platform",
        environment=AdmissionContext.PRODUCTION,
        action_class=ActionClass.DESTRUCTIVE,
        tool_id="t1",
        tool_trust=ToolTrustLevel.DESTRUCTIVE,
        certification_status=CertificationStatus.CERTIFIED,
        tools=ToolsSpec(
            allow=[ToolRef(tool_id="t1", trust_level=ToolTrustLevel.DESTRUCTIVE)]
        ),
        tenant_id="tenant-a",
    )
    context_b = context_a.model_copy(update={"tenant_id": "tenant-b"})

    assert engine.evaluate(context_a).rule == "pack.deny"
    assert engine.evaluate(context_b).rule != "pack.deny"



def test_audit_log_filters_by_tenant_but_keeps_one_chain() -> None:
    audit = InMemoryAuditLog(clock=lambda: _NOW)
    audit.append("alice", "transition", "r1", ctx=_A)
    audit.append("bob", "transition", "r2", ctx=_B)
    assert [record.subject for record in audit.records(ctx=_A)] == ["r1"]
    assert [record.subject for record in audit.records(ctx=_B)] == ["r2"]
    assert len(audit.records(ctx=SYSTEM_CONTEXT)) == 2
    assert audit.verify()


def _report_run(report_id: str, tenant_id: str) -> ReportRun:
    return ReportRun(
        report_id=report_id,
        tenant_id=tenant_id,
        kind=ReportKind.DIGEST,
        period_key="2026-W39",
        output_ref=f"reports/{report_id}",
        generated_at=_NOW,
    )


def test_reporting_store_is_scoped() -> None:
    store = InMemoryReportingStore()
    store.save_report(_report_run("rep-1", "tenant-a"), ctx=_A)
    assert store.get_report("rep-1", tenant_id="tenant-a", ctx=_A) is not None
    assert store.get_report("rep-1", tenant_id="tenant-b", ctx=_B) is None
    assert store.list_reports(tenant_id="tenant-b", ctx=_B) == []
    assert [record.report_id for record in store.list_reports(ctx=SYSTEM_CONTEXT)] == ["rep-1"]


def test_reporting_store_rejects_cross_tenant_write() -> None:
    store = InMemoryReportingStore()
    with pytest.raises(TenantScopeError):
        store.save_report(_report_run("rep-1", "tenant-a"), ctx=_B)


# --------------------------------------------------------------------------- #
# M58-01: data-plane store isolation (secrets, artifacts, cost, delivery,
# events, corpus, worker, certification, mcp).
# --------------------------------------------------------------------------- #
from hiveplane.artifacts.store import InMemoryArtifactStore  # noqa: E402
from hiveplane.corpus.models import CorpusRelease  # noqa: E402
from hiveplane.corpus.store import InMemoryCorpusReleaseStore  # noqa: E402
from hiveplane.cost.models import (  # noqa: E402
    BudgetPeriod as CostBudgetPeriod,
)
from hiveplane.cost.models import (  # noqa: E402
    BudgetScope,
    CostEvent,
    ThresholdAlert,
)
from hiveplane.cost.store import InMemoryCostStore  # noqa: E402
from hiveplane.delivery.models import (  # noqa: E402
    ApprovalDecisionRecord,
    DeliveryAttempt,
    DeliveryChannel,
    DeliveryEventType,
    NotificationPreference,
)
from hiveplane.delivery.models import (  # noqa: E402
    DeliveryStatus as DeliveryAttemptStatus,
)
from hiveplane.delivery.store import InMemoryDeliveryStore  # noqa: E402
from hiveplane.events.models import EventSubscription, FleetEventKind  # noqa: E402
from hiveplane.events.store import InMemoryEventSubscriptionStore  # noqa: E402
from hiveplane.fleet.artifacts import Artifact  # noqa: E402
from hiveplane.fleet.cost import CostPeriodKind, CostType  # noqa: E402
from hiveplane.fleet.workers import (  # noqa: E402
    Worker,
    WorkerHeartbeat,
    WorkerLease,
    WorkerState,
)
from hiveplane.mcp.models import (  # noqa: E402
    McpServerEndpoint,
    McpServerRecord,
    McpServerStatus,
    McpToolRecord,
    McpTransportKind,
    ToolStatus,
    ToolVersion,
)
from hiveplane.mcp.store import InMemoryMcpStore  # noqa: E402
from hiveplane.secrets.models import SecretRecord, SecretVersion  # noqa: E402
from hiveplane.secrets.store import InMemorySecretStore  # noqa: E402
from hiveplane.worker.models import WorkerToken  # noqa: E402
from hiveplane.worker.store import InMemoryWorkerStore  # noqa: E402


def _secret_record(tenant_id: str, secret_id: str = "sec-1") -> SecretRecord:
    return SecretRecord(
        secret_id=secret_id,
        tenant_id=tenant_id,
        name="db",
        current_version=1,
        created_at=_NOW,
        versions=[1],
    )


def _secret_version(tenant_id: str, secret_id: str = "sec-1") -> SecretVersion:
    return SecretVersion(
        secret_id=secret_id,
        tenant_id=tenant_id,
        name="db",
        version=1,
        ciphertext=b"ct",
        nonce=b"n",
        wrapped_key=b"wk",
        key_nonce=b"kn",
        key_id="k1",
        created_at=_NOW,
    )


def test_secret_store_is_scoped() -> None:
    store = InMemorySecretStore()
    store.save_secret(_secret_record("tenant-a"), ctx=_A)
    store.save_version(_secret_version("tenant-a"), ctx=_A)
    assert store.get_secret("tenant-a", "db", ctx=_A) is not None
    assert store.get_secret("tenant-a", "db", ctx=_B) is None
    assert store.list_secrets("tenant-a", ctx=_B) == []
    assert store.get_version("sec-1", 1, ctx=_B) is None
    assert store.list_versions("sec-1", ctx=_B) == []
    assert store.get_secret("tenant-a", "db", ctx=SYSTEM_CONTEXT) is not None


def test_secret_store_rejects_cross_tenant_write() -> None:
    store = InMemorySecretStore()
    with pytest.raises(TenantScopeError):
        store.save_secret(_secret_record("tenant-a"), ctx=_B)
    with pytest.raises(TenantScopeError):
        store.save_version(_secret_version("tenant-a"), ctx=_B)


def _artifact(tenant_id: str, artifact_id: str = "art-1") -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        tenant_id=tenant_id,
        run_id="r1",
        location="file:///tmp/a",
        size_bytes=1,
        content_hash="sha256:x",
        created_at=_NOW,
    )


def test_artifact_store_is_scoped() -> None:
    store = InMemoryArtifactStore()
    store.save_artifact(_artifact("tenant-a"), ctx=_A)
    assert store.get_artifact("art-1", tenant_id="tenant-a", ctx=_A) is not None
    assert store.get_artifact("art-1", tenant_id="tenant-a", ctx=_B) is None
    assert store.list_artifacts(tenant_id="tenant-a", ctx=_B) == []
    assert store.get_artifact("art-1", tenant_id="tenant-a", ctx=SYSTEM_CONTEXT) is not None


def test_artifact_store_rejects_cross_tenant_write() -> None:
    store = InMemoryArtifactStore()
    with pytest.raises(TenantScopeError):
        store.save_artifact(_artifact("tenant-a"), ctx=_B)


def _cost_event(tenant_id: str, event_id: str = "ev-1") -> CostEvent:
    return CostEvent(
        event_id=event_id,
        tenant_id=tenant_id,
        team_id="platform",
        workload_id="agent-1",
        cost_type=CostType.LLM,
        cost_usd=1.0,
        occurred_at=_NOW,
    )


def _cost_period(tenant_id: str, period_id: str = "p-1") -> CostBudgetPeriod:
    return CostBudgetPeriod(
        period_id=period_id,
        tenant_id=tenant_id,
        scope=BudgetScope.TENANT,
        scope_id="tenant-a",
        kind=CostPeriodKind.DAY,
        period_key="2026-09-25",
        limit_usd=10.0,
    )


def _cost_alert(tenant_id: str, alert_id: str = "al-1") -> ThresholdAlert:
    return ThresholdAlert(
        alert_id=alert_id,
        tenant_id=tenant_id,
        scope=BudgetScope.TENANT,
        scope_id="tenant-a",
        kind=CostPeriodKind.DAY,
        period_key="2026-09-25",
        threshold=50,
        spent_usd=5.0,
        limit_usd=10.0,
        fired_at=_NOW,
    )


def test_cost_store_is_scoped() -> None:
    store = InMemoryCostStore()
    store.save_event(_cost_event("tenant-a"), ctx=_A)
    store.save_period(_cost_period("tenant-a"), ctx=_A)
    store.save_alert(_cost_alert("tenant-a"), ctx=_A)
    assert store.list_events("tenant-a", ctx=_A) != []
    assert store.list_events("tenant-a", ctx=_B) == []
    assert store.get_period("p-1", ctx=_A) is not None
    assert store.get_period("p-1", ctx=_B) is None
    assert store.list_periods("tenant-a", ctx=_B) == []
    assert store.list_alerts("tenant-a", ctx=_B) == []
    assert len(store.list_events("tenant-a", ctx=SYSTEM_CONTEXT)) == 1


def test_cost_store_rejects_cross_tenant_write() -> None:
    store = InMemoryCostStore()
    with pytest.raises(TenantScopeError):
        store.save_event(_cost_event("tenant-a"), ctx=_B)
    with pytest.raises(TenantScopeError):
        store.save_period(_cost_period("tenant-a"), ctx=_B)
    with pytest.raises(TenantScopeError):
        store.save_alert(_cost_alert("tenant-a"), ctx=_B)


def test_cost_dead_letter_views_are_global() -> None:
    store = InMemoryCostStore()
    store.dead_letter(_cost_event("tenant-a"), "missing attribution")
    assert store.unattributed() == 1
    assert len(store.dead_letters()) == 1


def _delivery_attempt(tenant_id: str, attempt_id: str = "d-1") -> DeliveryAttempt:
    return DeliveryAttempt(
        attempt_id=attempt_id,
        tenant_id=tenant_id,
        event_type=DeliveryEventType.COMPLETED,
        channel=DeliveryChannel.SLACK,
        target="#ops",
        status=DeliveryAttemptStatus.DELIVERED,
        created_at=_NOW,
    )


def _decision(tenant_id: str, approval_id: str = "ap-1") -> ApprovalDecisionRecord:
    return ApprovalDecisionRecord(
        approval_id=approval_id,
        operator_id="alice",
        decision="approve",
        tenant_id=tenant_id,
        decided_at=_NOW,
    )


def _preference(tenant_id: str, team_id: str = "platform") -> NotificationPreference:
    return NotificationPreference(team_id=team_id, tenant_id=tenant_id)


def test_delivery_store_is_scoped() -> None:
    store = InMemoryDeliveryStore()
    store.save_attempt(_delivery_attempt("tenant-a"), ctx=_A)
    store.save_decision(_decision("tenant-a"), ctx=_A)
    store.save_preference(_preference("tenant-a"), ctx=_A)
    assert store.list_attempts("tenant-a", ctx=_A) != []
    assert store.list_attempts("tenant-a", ctx=_B) == []
    assert store.get_decision("ap-1", ctx=_A) is not None
    assert store.get_decision("ap-1", ctx=_B) is None
    assert store.list_decisions("tenant-a", ctx=_B) == []
    assert store.get_preference("tenant-a", "platform", ctx=_A) is not None
    assert store.get_preference("tenant-a", "platform", ctx=_B) is None
    assert store.list_preferences("tenant-a", ctx=_B) == []


def test_delivery_store_rejects_cross_tenant_write() -> None:
    store = InMemoryDeliveryStore()
    with pytest.raises(TenantScopeError):
        store.save_attempt(_delivery_attempt("tenant-a"), ctx=_B)
    with pytest.raises(TenantScopeError):
        store.save_decision(_decision("tenant-a"), ctx=_B)
    with pytest.raises(TenantScopeError):
        store.save_preference(_preference("tenant-a"), ctx=_B)


def _subscription(tenant_id: str, subscription_id: str = "sub-1") -> EventSubscription:
    return EventSubscription(
        subscription_id=subscription_id,
        tenant_id=tenant_id,
        url="https://example.com/hook",
        kinds=[FleetEventKind.RUN],
        created_at=_NOW,
    )


def test_event_store_is_scoped() -> None:
    store = InMemoryEventSubscriptionStore()
    store.add(_subscription("tenant-a"), ctx=_A)
    assert store.get("sub-1", tenant_id="tenant-a", ctx=_A) is not None
    assert store.get("sub-1", tenant_id="tenant-a", ctx=_B) is None
    assert store.list(tenant_id="tenant-a", ctx=_B) == []
    assert store.get("sub-1", tenant_id="tenant-a", ctx=SYSTEM_CONTEXT) is not None


def test_event_store_rejects_cross_tenant_write() -> None:
    store = InMemoryEventSubscriptionStore()
    with pytest.raises(TenantScopeError):
        store.add(_subscription("tenant-a"), ctx=_B)


def _corpus_release(tenant_id: str, corpus_id: str = "c1") -> CorpusRelease:
    return CorpusRelease(
        corpus_id=corpus_id,
        version=1,
        content_hash="sha256:x",
        corpus={},
        created_at=_NOW,
        tenant_id=tenant_id,
    )


def test_corpus_store_is_scoped() -> None:
    store = InMemoryCorpusReleaseStore()
    store.add(_corpus_release("tenant-a"), ctx=_A)
    assert store.get("c1", 1, tenant_id="tenant-a", ctx=_A) is not None
    assert store.get("c1", 1, tenant_id="tenant-a", ctx=_B) is None
    assert store.list(tenant_id="tenant-a", ctx=_B) == []
    assert store.get("c1", 1, tenant_id="tenant-a", ctx=SYSTEM_CONTEXT) is not None


def test_corpus_store_rejects_cross_tenant_write() -> None:
    store = InMemoryCorpusReleaseStore()
    with pytest.raises(TenantScopeError):
        store.add(_corpus_release("tenant-a"), ctx=_B)


def _worker(tenant_id: str, worker_id: str = "w1") -> Worker:
    return Worker(
        worker_id=worker_id,
        tenant_id=tenant_id,
        state=WorkerState.READY,
        version="1.0",
        registered_at=_NOW,
        last_seen_at=_NOW,
    )


def _lease(tenant_id: str, lease_id: str = "l1", worker_id: str = "w1") -> WorkerLease:
    return WorkerLease(
        lease_id=lease_id,
        tenant_id=tenant_id,
        run_id="r1",
        worker_id=worker_id,
        attempt=1,
        granted_at=_NOW,
        expires_at=_NOW + timedelta(minutes=1),
        fencing_token=1,
    )


def _heartbeat(tenant_id: str, worker_id: str = "w1") -> WorkerHeartbeat:
    return WorkerHeartbeat(
        heartbeat_id="hb-1",
        tenant_id=tenant_id,
        worker_id=worker_id,
        seen_at=_NOW,
        status=WorkerState.READY,
    )


def _token(tenant_id: str, token_id: str = "tok-1") -> WorkerToken:
    return WorkerToken(
        token_id=token_id,
        worker_id="w1",
        tenant_id=tenant_id,
        key_id="k1",
        issued_at=_NOW,
        expires_at=_NOW + timedelta(minutes=5),
    )


def test_worker_store_is_scoped() -> None:
    store = InMemoryWorkerStore()
    store.save_worker(_worker("tenant-a"), ctx=_A)
    store.save_lease(_lease("tenant-a"), ctx=_A)
    store.save_token(_token("tenant-a"), ctx=_A)
    store.save_heartbeat(_heartbeat("tenant-a"), ctx=_A)
    assert store.get_worker("w1", "tenant-a", ctx=_A) is not None
    assert store.get_worker("w1", "tenant-a", ctx=_B) is None
    assert store.list_workers("tenant-a", ctx=_B) == []
    assert store.get_lease("l1", ctx=_A) is not None
    assert store.get_lease("l1", ctx=_B) is None
    assert store.list_leases("tenant-a", ctx=_B) == []
    assert store.list_heartbeats("w1", ctx=_A) != []
    assert store.list_heartbeats("w1", ctx=_B) == []
    assert store.get_token("tok-1", ctx=_A) is not None
    assert store.get_token("tok-1", ctx=_B) is None
    assert store.list_tokens("tenant-a", ctx=_B) == []


def test_worker_store_rejects_cross_tenant_write() -> None:
    store = InMemoryWorkerStore()
    with pytest.raises(TenantScopeError):
        store.save_worker(_worker("tenant-a"), ctx=_B)
    with pytest.raises(TenantScopeError):
        store.save_lease(_lease("tenant-a"), ctx=_B)
    with pytest.raises(TenantScopeError):
        store.save_heartbeat(_heartbeat("tenant-a"), ctx=_B)
    with pytest.raises(TenantScopeError):
        store.save_token(_token("tenant-a"), ctx=_B)


def test_worker_delete_lease_is_scoped() -> None:
    store = InMemoryWorkerStore()
    store.save_lease(_lease("tenant-a"), ctx=_A)
    store.delete_lease("l1", ctx=_B)
    assert store.get_lease("l1", ctx=_A) is not None


def test_worker_global_views_require_system_context() -> None:
    store = InMemoryWorkerStore()
    store.save_worker(_worker("tenant-a"), ctx=_A)
    store.save_lease(_lease("tenant-a"), ctx=_A)
    with pytest.raises(TenantScopeError):
        store.list_all_workers(ctx=_A)
    with pytest.raises(TenantScopeError):
        store.list_all_leases(ctx=_A)
    assert len(store.list_all_workers(ctx=SYSTEM_CONTEXT)) == 1
    assert len(store.list_all_leases(ctx=SYSTEM_CONTEXT)) == 1


def _mcp_server(server_id: str = "srv-1") -> McpServerRecord:
    return McpServerRecord(
        server_id=server_id,
        endpoint=McpServerEndpoint(kind=McpTransportKind.HTTP, target="https://mcp"),
        fingerprint="fp",
        status=McpServerStatus.CONNECTED,
        connected_at=_NOW,
        last_seen=_NOW,
    )


def _mcp_tool(tool_id: str = "tool-1") -> McpToolRecord:
    return McpToolRecord(
        tool_id=tool_id,
        server_id="srv-1",
        server_fingerprint="fp",
        tool_name="echo",
        status=ToolStatus.DISCOVERED,
        discovered_at=_NOW,
    )


def _mcp_version(tool_id: str = "tool-1") -> ToolVersion:
    return ToolVersion(tool_id=tool_id, version=1, registered_at=_NOW)


def test_mcp_store_is_scoped() -> None:
    store = InMemoryMcpStore()
    store.save_server(_mcp_server(), ctx=_A)
    store.save_tool(_mcp_tool(), ctx=_A)
    store.save_version(_mcp_version(), ctx=_A)
    assert store.list_servers(ctx=_A) != []
    assert store.list_servers(ctx=_B) == []
    assert store.get_tool("tool-1", ctx=_A) is not None
    assert store.get_tool("tool-1", ctx=_B) is None
    assert store.list_tools(ctx=_B) == []
    assert store.list_versions("tool-1", ctx=_A) != []
    assert store.list_versions("tool-1", ctx=_B) == []
    assert store.list_servers(ctx=SYSTEM_CONTEXT) != []


def test_mcp_store_rejects_cross_tenant_write() -> None:
    store = InMemoryMcpStore()
    store.save_server(_mcp_server(), ctx=_A)
    store.save_tool(_mcp_tool(), ctx=_A)
    store.save_version(_mcp_version(), ctx=_A)
    with pytest.raises(TenantScopeError):
        store.save_server(_mcp_server(), ctx=_B)
    with pytest.raises(TenantScopeError):
        store.save_tool(_mcp_tool(), ctx=_B)
    with pytest.raises(TenantScopeError):
        store.save_version(_mcp_version(), ctx=_B)


def test_certification_store_denies_cross_tenant_record_id_reuse() -> None:
    store = InMemoryCertificationStore()
    store.add(_certification_record("rec-1", "agent-1"), ctx=_A)
    with pytest.raises(TenantScopeError):
        store.add(_certification_record("rec-1", "agent-1"), ctx=_B)
    assert store.get("rec-1", ctx=_A) is not None
    assert store.get("rec-1", ctx=_B) is None


# --------------------------------------------------------------------------- #
# M58-01: Postgres-backed stores enforce the same contract.
# --------------------------------------------------------------------------- #
from postgres import reset_database, seed_workload  # noqa: E402


def _pg_secret_store(engine: object) -> object:
    from hiveplane.secrets.store import PostgresSecretStore

    return PostgresSecretStore(engine)  # type: ignore[arg-type]


def test_postgres_secret_store_is_scoped(pg_engine: object) -> None:
    reset_database(pg_engine)  # type: ignore[arg-type]
    store = _pg_secret_store(pg_engine)
    store.save_secret(_secret_record("tenant-a"), ctx=_A)  # type: ignore[attr-defined]
    store.save_version(_secret_version("tenant-a"), ctx=_A)  # type: ignore[attr-defined]
    assert store.get_secret("tenant-a", "db", ctx=_A) is not None  # type: ignore[attr-defined]
    assert store.get_secret("tenant-a", "db", ctx=_B) is None  # type: ignore[attr-defined]
    assert store.list_secrets("tenant-a", ctx=_B) == []  # type: ignore[attr-defined]
    with pytest.raises(TenantScopeError):
        store.save_secret(_secret_record("tenant-a"), ctx=_B)  # type: ignore[attr-defined]


def test_postgres_cost_store_is_scoped(pg_engine: object) -> None:
    from hiveplane.cost.store import PostgresCostStore

    reset_database(pg_engine)  # type: ignore[arg-type]
    store = PostgresCostStore(pg_engine)  # type: ignore[arg-type]
    store.save_event(_cost_event("tenant-a"), ctx=_A)
    store.save_period(_cost_period("tenant-a"), ctx=_A)
    assert store.list_events("tenant-a", ctx=_B) == []
    assert store.get_period("p-1", ctx=_B) is None
    with pytest.raises(TenantScopeError):
        store.save_event(_cost_event("tenant-a"), ctx=_B)
    with pytest.raises(TenantScopeError):
        store.save_period(_cost_period("tenant-a"), ctx=_B)


def test_postgres_budget_store_is_scoped(pg_engine: object) -> None:
    from hiveplane.budget.store import PostgresBudgetStore

    reset_database(pg_engine)  # type: ignore[arg-type]
    store = PostgresBudgetStore(pg_engine)  # type: ignore[arg-type]
    store.add_run_spend("r1", 1.0, ctx=_A)
    store.add_day_spend("agent-1", "2026-09-25", 2.0, ctx=_A)
    store.add_team_spend("platform", "2026-09-25", 3.0, ctx=_A)
    assert store.run_spend("r1", ctx=_A) == 1.0
    assert store.run_spend("r1", ctx=_B) == 0.0
    assert store.day_spend("agent-1", "2026-09-25", ctx=_A) == 2.0
    assert store.day_spend("agent-1", "2026-09-25", ctx=_B) == 0.0
    assert store.team_spend("platform", "2026-09-25", ctx=_A) == 3.0
    assert store.team_spend("platform", "2026-09-25", ctx=_B) == 0.0


def test_postgres_budget_attribution_is_scoped(pg_engine: object) -> None:
    from hiveplane.budget.store import PostgresBudgetStore

    reset_database(pg_engine)  # type: ignore[arg-type]
    seed_workload(pg_engine, name="agent-1", tenant_id="tenant-a")  # type: ignore[arg-type]
    store = PostgresBudgetStore(pg_engine)  # type: ignore[arg-type]
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
    store.record_attribution(attribution, ctx=_A)
    assert store.list_attributions(ctx=_A) == [attribution]
    assert store.list_attributions(ctx=_B) == []
    with pytest.raises(TenantScopeError):
        store.record_attribution(attribution, ctx=_B)


def test_postgres_delivery_store_is_scoped(pg_engine: object) -> None:
    from hiveplane.delivery.store import PostgresDeliveryStore

    reset_database(pg_engine)  # type: ignore[arg-type]
    store = PostgresDeliveryStore(pg_engine)  # type: ignore[arg-type]
    store.save_attempt(_delivery_attempt("tenant-a"), ctx=_A)
    store.save_decision(_decision("tenant-a"), ctx=_A)
    assert store.list_attempts("tenant-a", ctx=_B) == []
    assert store.get_decision("ap-1", ctx=_B) is None
    with pytest.raises(TenantScopeError):
        store.save_attempt(_delivery_attempt("tenant-a"), ctx=_B)
    with pytest.raises(TenantScopeError):
        store.save_decision(_decision("tenant-a"), ctx=_B)


def test_postgres_event_store_is_scoped(pg_engine: object) -> None:
    from hiveplane.events.store import PostgresEventSubscriptionStore

    reset_database(pg_engine)  # type: ignore[arg-type]
    store = PostgresEventSubscriptionStore(pg_engine)  # type: ignore[arg-type]
    store.add(_subscription("tenant-a"), ctx=_A)
    assert store.list(tenant_id="tenant-a", ctx=_B) == []
    with pytest.raises(TenantScopeError):
        store.add(_subscription("tenant-a"), ctx=_B)


def test_postgres_corpus_store_is_scoped(pg_engine: object) -> None:
    from hiveplane.corpus.store import PostgresCorpusReleaseStore

    reset_database(pg_engine)  # type: ignore[arg-type]
    store = PostgresCorpusReleaseStore(pg_engine)  # type: ignore[arg-type]
    store.add(_corpus_release("tenant-a"), ctx=_A)
    assert store.list(tenant_id="tenant-a", ctx=_B) == []
    with pytest.raises(TenantScopeError):
        store.add(_corpus_release("tenant-a"), ctx=_B)


def test_postgres_mcp_store_is_scoped(pg_engine: object) -> None:
    from hiveplane.mcp.store import PostgresMcpStore

    reset_database(pg_engine)  # type: ignore[arg-type]
    store = PostgresMcpStore(pg_engine)  # type: ignore[arg-type]
    store.save_server(_mcp_server(), ctx=_A)
    store.save_tool(_mcp_tool(), ctx=_A)
    assert store.list_servers(ctx=_B) == []
    assert store.get_tool("tool-1", ctx=_B) is None
    with pytest.raises(TenantScopeError):
        store.save_server(_mcp_server(), ctx=_B)
    with pytest.raises(TenantScopeError):
        store.save_tool(_mcp_tool(), ctx=_B)


def test_postgres_worker_store_is_scoped(pg_engine: object) -> None:
    from hiveplane.worker.store import PostgresWorkerStore

    reset_database(pg_engine)  # type: ignore[arg-type]
    store = PostgresWorkerStore(pg_engine)  # type: ignore[arg-type]
    store.save_worker(_worker("tenant-a"), ctx=_A)
    store.save_token(_token("tenant-a"), ctx=_A)
    assert store.list_workers("tenant-a", ctx=_B) == []
    assert store.get_token("tok-1", ctx=_B) is None
    with pytest.raises(TenantScopeError):
        store.save_worker(_worker("tenant-a"), ctx=_B)
    with pytest.raises(TenantScopeError):
        store.list_all_workers(ctx=_A)


def test_postgres_certification_store_denies_record_id_reuse(pg_engine: object) -> None:
    from hiveplane.certification.store import PostgresCertificationStore

    reset_database(pg_engine)  # type: ignore[arg-type]
    seed_workload(pg_engine, name="agent-m58", tenant_id="tenant-a")  # type: ignore[arg-type]
    store = PostgresCertificationStore(pg_engine)  # type: ignore[arg-type]
    store.add(_certification_record("rec-1", "agent-m58"), ctx=_A)
    with pytest.raises(TenantScopeError):
        store.add(_certification_record("rec-1", "agent-1"), ctx=_B)
    assert store.get("rec-1", ctx=_A) is not None
    assert store.get("rec-1", ctx=_B) is None


# --------------------------------------------------------------------------- #
# M58-04: per-tenant API keys, membership roles, and suspension.
# --------------------------------------------------------------------------- #
def _key_service(
    store: object, *, key_id: str = "k-a", token: str = "tok-a"
) -> object:
    from hiveplane.auth.keys import ApiKeyService

    return ApiKeyService(
        store,  # type: ignore[arg-type]
        key_id_factory=lambda: key_id,
        token_factory=lambda: token,
    )


def test_auth_store_keys_are_tenant_scoped() -> None:
    from hiveplane.auth.store import InMemoryAuthStore

    store = InMemoryAuthStore()
    service = _key_service(store)
    issued = service.create("tenant-a", Role.ADMIN, ctx=_A)  # type: ignore[attr-defined]

    assert store.get_key("k-a", ctx=_A) is not None
    assert store.get_key("k-a", ctx=_B) is None
    assert store.get_key("k-a", ctx=SYSTEM_CONTEXT) is not None
    assert store.list_keys("tenant-a", ctx=_B) == []
    with pytest.raises(TenantScopeError):
        store.save_key(issued.record, ctx=_B)


def test_key_service_cannot_see_or_revoke_a_foreign_key() -> None:
    from hiveplane.auth.models import AuthenticationError
    from hiveplane.auth.store import InMemoryAuthStore

    store = InMemoryAuthStore()
    service = _key_service(store)
    service.create("tenant-a", Role.ADMIN, ctx=_A)  # type: ignore[attr-defined]

    assert service.list_keys("tenant-a", ctx=_B) == []  # type: ignore[attr-defined]
    assert service.usage("tenant-a", ctx=_B) == {}  # type: ignore[attr-defined]
    with pytest.raises(AuthenticationError):
        service.revoke("k-a", ctx=_B)  # type: ignore[attr-defined]
    assert store.get_key("k-a", ctx=_A) is not None
    assert store.get_key("k-a", ctx=_A).revoked_at is None  # type: ignore[union-attr]
    assert service.revoke("k-a", ctx=_A).revoked_at is not None  # type: ignore[attr-defined]


def _member_auth(*, auth_enabled: bool) -> object:
    from hiveplane.auth.service import AuthService
    from hiveplane.auth.store import InMemoryAuthStore
    from hiveplane.tenancy.models import Membership, Tenant
    from hiveplane.tenancy.store import InMemoryTenantStore

    tenant_store = InMemoryTenantStore()
    tenant_store.save_tenant(
        SYSTEM_CONTEXT, Tenant(tenant_id="tenant-a", name="A", created_at=_NOW)
    )
    tenant_store.save_membership(
        SYSTEM_CONTEXT,
        Membership(
            membership_id="m-1",
            tenant_id="tenant-a",
            operator_id="alice",
            role=Role.APPROVER,
            created_at=_NOW,
        ),
    )
    return AuthService(
        InMemoryAuthStore(),
        tenant_store=tenant_store,
        auth_enabled=auth_enabled,
        clock=lambda: _NOW,
    )


def test_login_resolves_membership_role_over_request_body() -> None:
    auth = _member_auth(auth_enabled=True)
    assert auth.login("alice", "tenant-a", Role.ADMIN).role is Role.APPROVER  # type: ignore[attr-defined]
    assert auth.login("bob", "tenant-a", Role.ADMIN).role is Role.VIEWER  # type: ignore[attr-defined]


def test_login_trusts_supplied_role_when_auth_disabled() -> None:
    auth = _member_auth(auth_enabled=False)
    assert auth.login("bob", "tenant-a", Role.ADMIN).role is Role.ADMIN  # type: ignore[attr-defined]


def test_login_without_tenant_store_defaults_viewer_when_auth_enabled() -> None:
    from hiveplane.auth.service import AuthService
    from hiveplane.auth.store import InMemoryAuthStore

    auth = AuthService(InMemoryAuthStore(), auth_enabled=True, clock=lambda: _NOW)
    assert auth.login("bob", "tenant-a", Role.ADMIN).role is Role.VIEWER


def test_suspended_tenant_key_is_refused() -> None:
    from hiveplane.auth.service import AuthService
    from hiveplane.auth.store import InMemoryAuthStore
    from hiveplane.tenancy.admin import TenantAdminService
    from hiveplane.tenancy.errors import TenantSuspendedError
    from hiveplane.tenancy.store import InMemoryTenantStore

    tenant_store = InMemoryTenantStore()
    admin = TenantAdminService(tenant_store)
    admin.create_tenant(SYSTEM_CONTEXT, tenant_id="tenant-a", name="A")
    store = InMemoryAuthStore()
    issued = _key_service(store).create(  # type: ignore[attr-defined]
        "tenant-a", Role.ADMIN, ctx=context_for_run("tenant-a")
    )
    auth = AuthService(store, require_active=admin.require_active)

    assert auth.authenticate_key(issued.token).tenant_id == "tenant-a"
    admin.suspend_tenant(SYSTEM_CONTEXT, "tenant-a")
    with pytest.raises(TenantSuspendedError):
        auth.authenticate_key(issued.token)


def test_authorize_uses_membership_role_over_identity_role() -> None:
    from hiveplane.auth.models import (
        AuthMethod,
        AuthorizationError,
        OperatorIdentity,
        Permission,
    )

    auth = _member_auth(auth_enabled=True)
    identity = OperatorIdentity(
        operator_id="alice",
        tenant_id="tenant-a",
        role=Role.ADMIN,
        method=AuthMethod.SESSION,
    )
    auth.authorize(identity, Permission.APPROVE)  # type: ignore[attr-defined]
    with pytest.raises(AuthorizationError):
        auth.authorize(identity, Permission.PROMOTE)  # type: ignore[attr-defined]


def test_membership_lookup_is_tenant_scoped() -> None:
    from hiveplane.tenancy.models import Membership, Tenant
    from hiveplane.tenancy.store import InMemoryTenantStore

    store = InMemoryTenantStore()
    store.save_tenant(SYSTEM_CONTEXT, Tenant(tenant_id="tenant-a", name="A", created_at=_NOW))
    store.save_membership(
        SYSTEM_CONTEXT,
        Membership(
            membership_id="m-1",
            tenant_id="tenant-a",
            operator_id="alice",
            role=Role.ADMIN,
            created_at=_NOW,
        ),
    )
    assert store.find_membership(_A, "tenant-a", "alice") is not None
    assert store.find_membership(_B, "tenant-a", "alice") is None
    assert store.find_membership(_A, "tenant-a", "bob") is None


def test_auth_store_audit_events_are_tenant_scoped() -> None:
    from hiveplane.auth.models import (
        AccessEvent,
        AccessResult,
        AuthMethod,
        LoginEvent,
    )
    from hiveplane.auth.store import InMemoryAuthStore

    store = InMemoryAuthStore()
    login = LoginEvent(
        tenant_id="tenant-a",
        actor="alice",
        method=AuthMethod.SESSION,
        result=AccessResult.ALLOW,
        created_at=_NOW,
    )
    access = AccessEvent(
        tenant_id="tenant-a",
        actor="alice",
        method=AuthMethod.SESSION,
        action="run.read",
        result=AccessResult.ALLOW,
        created_at=_NOW,
    )
    store.save_login(login, ctx=_A)
    store.save_access(access, ctx=_A)

    assert store.list_logins("tenant-a", ctx=_B) == []
    assert store.list_access("tenant-a", ctx=_B) == []
    with pytest.raises(TenantScopeError):
        store.save_login(login, ctx=_B)
    with pytest.raises(TenantScopeError):
        store.save_access(access, ctx=_B)


def test_postgres_auth_store_is_scoped(pg_engine: object) -> None:
    from hiveplane.auth.models import (
        AccessEvent,
        AccessResult,
        ApiKeyRecord,
        AuthMethod,
        LoginEvent,
    )
    from hiveplane.auth.store import PostgresAuthStore

    reset_database(pg_engine)  # type: ignore[arg-type]
    store = PostgresAuthStore(pg_engine)  # type: ignore[arg-type]
    record = ApiKeyRecord(
        key_id="k-a", tenant_id="tenant-a", role=Role.ADMIN, hashed_key="h-a", created_at=_NOW
    )
    login = LoginEvent(
        tenant_id="tenant-a",
        actor="alice",
        method=AuthMethod.SESSION,
        result=AccessResult.ALLOW,
        created_at=_NOW,
    )
    access = AccessEvent(
        tenant_id="tenant-a",
        actor="alice",
        method=AuthMethod.SESSION,
        action="run.read",
        result=AccessResult.ALLOW,
        created_at=_NOW,
    )
    store.save_key(record, ctx=_A)
    store.save_login(login, ctx=_A)
    store.save_access(access, ctx=_A)

    assert store.find_key_by_hash("h-a") is not None
    assert store.get_key("k-a", ctx=_A) is not None
    assert store.get_key("k-a", ctx=_B) is None
    assert store.list_keys("tenant-a", ctx=_B) == []
    assert store.list_logins("tenant-a", ctx=_B) == []
    assert store.list_access("tenant-a", ctx=_B) == []
    with pytest.raises(TenantScopeError):
        store.save_key(record, ctx=_B)
    with pytest.raises(TenantScopeError):
        store.save_login(login, ctx=_B)
    with pytest.raises(TenantScopeError):
        store.save_access(access, ctx=_B)
    with pytest.raises(TenantScopeError):
        store.delete_events_before(cutoff=_NOW, tenant_id="tenant-a", ctx=_B)
    with pytest.raises(TenantScopeError):
        store.purge_tenant("tenant-a", ctx=_B)


def test_postgres_find_membership_is_scoped(pg_engine: object) -> None:
    from hiveplane.tenancy.models import Membership, Tenant
    from hiveplane.tenancy.store import PostgresTenantStore

    reset_database(pg_engine)  # type: ignore[arg-type]
    store = PostgresTenantStore(pg_engine)  # type: ignore[arg-type]
    store.save_tenant(_A, Tenant(tenant_id="tenant-a", name="A", created_at=_NOW))
    store.save_membership(
        _A,
        Membership(
            membership_id="m-1",
            tenant_id="tenant-a",
            operator_id="alice",
            role=Role.ADMIN,
            created_at=_NOW,
        ),
    )

    assert store.find_membership(_A, "tenant-a", "alice") is not None
    assert store.find_membership(_B, "tenant-a", "alice") is None
    assert store.find_membership(_A, "tenant-a", "bob") is None


def test_registry_store_rejects_cross_tenant_name_collision(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    from hiveplane.registry.errors import WorkloadAlreadyExistsError

    store = InMemoryRegistryStore()
    store.save_workload(_workload_record("tenant-a", make_manifest), ctx=_A)

    with pytest.raises(WorkloadAlreadyExistsError):
        store.save_workload(_workload_record("tenant-b", make_manifest), ctx=_B)

    assert store.get_workload("agent-1", ctx=_A) is not None
    assert store.get_workload("agent-1", ctx=_B) is None
