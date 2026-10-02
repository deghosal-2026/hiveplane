"""Tenant purge and signed purge certificates (M57-07)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from hiveplane.artifacts.backend import LocalBlobBackend
from hiveplane.artifacts.service import ArtifactService
from hiveplane.artifacts.store import InMemoryArtifactStore, PostgresArtifactStore
from hiveplane.auth.models import AccessEvent, AccessResult, ApiKeyRecord, AuthMethod, LoginEvent
from hiveplane.auth.store import InMemoryAuthStore, PostgresAuthStore
from hiveplane.certification.binding import canonical_json
from hiveplane.certification.signing import generate_keypair
from hiveplane.core.run import Run, RunState
from hiveplane.cost.models import BudgetPeriod, BudgetScope, CostEvent, ThresholdAlert
from hiveplane.cost.store import InMemoryCostStore, PostgresCostStore
from hiveplane.delivery.models import (
    ApprovalDecisionRecord,
    DeliveryAttempt,
    DeliveryChannel,
    DeliveryEventType,
    DeliveryStatus,
    NotificationPreference,
)
from hiveplane.delivery.store import InMemoryDeliveryStore, PostgresDeliveryStore
from hiveplane.execution.store import InMemoryRunStore, JsonFileRunStore
from hiveplane.fleet.artifacts import RetentionPolicy
from hiveplane.fleet.cost import CostPeriodKind, CostType
from hiveplane.persistence.audit import InMemoryAuditLog
from hiveplane.persistence.run_store import PostgresRunStore
from hiveplane.reporting.errors import PurgeIncompleteError
from hiveplane.reporting.models import (
    AuditExport,
    AuditExportFormat,
    EvidenceFile,
    EvidencePack,
    IntegrityProof,
    PackSignature,
    ReportKind,
    ReportRun,
    ReportSchedule,
)
from hiveplane.reporting.purge import PurgeTarget, TenantPurgeService
from hiveplane.reporting.store import InMemoryReportingStore, PostgresReportingStore
from hiveplane.tenancy.context import context_for_run
from hiveplane.tenancy.models import Membership, Role, Team, Tenant
from hiveplane.tenancy.store import InMemoryTenantStore, PostgresTenantStore
from postgres import reset_database, seed_workload

_NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
_HEX = "a" * 64
_ACME = context_for_run("acme")
_BETA = context_for_run("beta")


def _run(run_id: str, tenant_id: str, workload_id: str = "agent-1") -> Run:
    return Run(
        id=run_id,
        workload_id=workload_id,
        caller="cli",
        state=RunState.COMPLETED,
        created_at=_NOW,
        updated_at=_NOW,
        tenant_id=tenant_id,
    )


def _cost(event_id: str, tenant_id: str) -> CostEvent:
    return CostEvent(
        event_id=event_id,
        tenant_id=tenant_id,
        team_id="team-a",
        cost_type=CostType.LLM,
        cost_usd=1.0,
        occurred_at=_NOW,
    )


def _period(period_id: str, tenant_id: str) -> BudgetPeriod:
    return BudgetPeriod(
        period_id=period_id,
        tenant_id=tenant_id,
        scope=BudgetScope.TENANT,
        scope_id=tenant_id,
        kind=CostPeriodKind.WEEK,
        period_key="2026-W39",
        limit_usd=100.0,
    )


def _alert(alert_id: str, tenant_id: str) -> ThresholdAlert:
    return ThresholdAlert(
        alert_id=alert_id,
        tenant_id=tenant_id,
        scope=BudgetScope.TENANT,
        scope_id=tenant_id,
        kind=CostPeriodKind.WEEK,
        period_key="2026-W39",
        threshold=80,
        spent_usd=90.0,
        limit_usd=100.0,
        fired_at=_NOW,
    )


def _key(key_id: str, tenant_id: str) -> ApiKeyRecord:
    return ApiKeyRecord(
        key_id=key_id,
        tenant_id=tenant_id,
        role=Role.ADMIN,
        hashed_key=f"hash-{key_id}",
        created_at=_NOW,
    )


def _login(tenant_id: str) -> LoginEvent:
    return LoginEvent(
        tenant_id=tenant_id,
        actor="alice",
        method=AuthMethod.SESSION,
        result=AccessResult.ALLOW,
        created_at=_NOW,
    )


def _access(tenant_id: str) -> AccessEvent:
    return AccessEvent(
        tenant_id=tenant_id,
        actor="alice",
        method=AuthMethod.SESSION,
        action="run.read",
        result=AccessResult.ALLOW,
        created_at=_NOW,
    )


def _attempt(attempt_id: str, tenant_id: str) -> DeliveryAttempt:
    return DeliveryAttempt(
        attempt_id=attempt_id,
        tenant_id=tenant_id,
        event_type=DeliveryEventType.COMPLETED,
        channel=DeliveryChannel.SLACK,
        target="#ops",
        status=DeliveryStatus.DELIVERED,
        created_at=_NOW,
    )


def _decision(approval_id: str, tenant_id: str) -> ApprovalDecisionRecord:
    return ApprovalDecisionRecord(
        approval_id=approval_id,
        operator_id="alice",
        decision="approve",
        tenant_id=tenant_id,
        decided_at=_NOW,
    )


def _preference(tenant_id: str) -> NotificationPreference:
    return NotificationPreference(team_id="team-a", tenant_id=tenant_id)


def _report(report_id: str, tenant_id: str) -> ReportRun:
    return ReportRun(
        report_id=report_id,
        tenant_id=tenant_id,
        kind=ReportKind.DIGEST,
        period_key="2026-W39",
        output_ref=f"reports/{report_id}",
        generated_at=_NOW,
    )


def _schedule(schedule_id: str, tenant_id: str) -> ReportSchedule:
    return ReportSchedule(
        schedule_id=schedule_id,
        tenant_id=tenant_id,
        cron="0 8 * * 1",
        period_kind=CostPeriodKind.WEEK,
        created_at=_NOW,
    )


def _export(export_id: str, tenant_id: str) -> AuditExport:
    return AuditExport(
        export_id=export_id,
        tenant_id=tenant_id,
        period_start=_NOW,
        period_end=_NOW,
        format=AuditExportFormat.JSON,
        record_count=0,
        integrity_proof=IntegrityProof(merkle_root=_HEX, leaf_count=0, chain_head=_HEX),
        content="[]",
        created_at=_NOW,
    )


def _evidence(pack_id: str, tenant_id: str) -> EvidencePack:
    return EvidencePack(
        pack_id=pack_id,
        tenant_id=tenant_id,
        period_start=_NOW,
        period_end=_NOW,
        files=[
            EvidenceFile(
                name="index.json", media_type="application/json", digest=_HEX, content="{}"
            )
        ],
        signature=PackSignature(key_id="reporting", digest=_HEX, signature="sig"),
        created_at=_NOW,
    )


def _policy(tenant_id: str, *, legal_hold: bool = False) -> RetentionPolicy:
    return RetentionPolicy(
        policy_id=f"retention:{tenant_id}:metering",
        tenant_id=tenant_id,
        data_class="metering",
        retain_days=30,
        legal_hold=legal_hold,
    )


class _Fixture:
    def __init__(self, tmp_path: Path) -> None:
        self.artifact_store = InMemoryArtifactStore()
        self.artifacts = ArtifactService(
            self.artifact_store, LocalBlobBackend(tmp_path), clock=lambda: _NOW
        )
        self.runs = InMemoryRunStore()
        self.cost = InMemoryCostStore()
        self.auth = InMemoryAuthStore()
        self.delivery = InMemoryDeliveryStore()
        self.reporting = InMemoryReportingStore()
        self.audit = InMemoryAuditLog(clock=lambda: _NOW)
        self.held: set[str] = set()
        self.key, self.public_key = generate_keypair()
        self.service = TenantPurgeService(
            self.reporting,
            [
                PurgeTarget("artifacts", self.artifacts.purge_tenant),
                PurgeTarget(
                    "runs",
                    lambda tenant: self.runs.purge_tenant(tenant, ctx=context_for_run(tenant)),
                ),
                PurgeTarget("cost", self.cost.purge_tenant),
                PurgeTarget(
                    "auth",
                    lambda tenant: self.auth.purge_tenant(
                        tenant, ctx=context_for_run(tenant)
                    ),
                ),
                PurgeTarget("delivery", self.delivery.purge_tenant),
                PurgeTarget("reports", self.reporting.purge_tenant_reports),
                PurgeTarget("audit", lambda _tenant: 0),
            ],
            legal_hold_check=lambda tenant: tenant in self.held,
            signing_key=self.key,
            clock=lambda: _NOW,
        )
        self.service.bind_audit(self.audit)

    def seed(self, tenant_id: str) -> None:
        self.runs.save_run(_run(f"run-{tenant_id}", tenant_id), ctx=context_for_run(tenant_id))
        scope = context_for_run(tenant_id)
        self.cost.save_event(_cost(f"cost-{tenant_id}", tenant_id), ctx=scope)
        self.cost.save_period(_period(f"period-{tenant_id}", tenant_id), ctx=scope)
        self.cost.save_alert(_alert(f"alert-{tenant_id}", tenant_id), ctx=scope)
        self.auth.save_key(_key(f"key-{tenant_id}", tenant_id), ctx=scope)
        self.auth.save_login(_login(tenant_id), ctx=scope)
        self.auth.save_access(_access(tenant_id), ctx=scope)
        self.delivery.save_attempt(_attempt(f"attempt-{tenant_id}", tenant_id), ctx=scope)
        self.delivery.save_decision(_decision(f"approval-{tenant_id}", tenant_id), ctx=scope)
        self.delivery.save_preference(_preference(tenant_id), ctx=scope)
        self.reporting.save_report(
            _report(f"report-{tenant_id}", tenant_id), ctx=context_for_run(tenant_id)
        )
        self.reporting.save_schedule(
            _schedule(f"schedule-{tenant_id}", tenant_id), ctx=context_for_run(tenant_id)
        )
        self.reporting.save_export(
            _export(f"export-{tenant_id}", tenant_id), ctx=context_for_run(tenant_id)
        )
        self.reporting.save_evidence(
            _evidence(f"pack-{tenant_id}", tenant_id), ctx=context_for_run(tenant_id)
        )
        self.artifacts.capture(
            tenant_id=tenant_id,
            run_id=f"run-{tenant_id}",
            filename="out.txt",
            data=b"payload",
            ctx=scope,
        )


_EXPECTED = {
    "artifacts": 1,
    "runs": 1,
    "cost": 3,
    "auth": 3,
    "delivery": 3,
    "reports": 4,
    "audit": 0,
}


def test_purge_removes_only_target_tenant_and_reports_counts(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    fixture.seed("acme")
    fixture.seed("beta")

    record = fixture.service.purge(_ACME, "acme", actor="operator")

    counts = {count.store: count.deleted for count in record.certificate.stores}
    assert counts == _EXPECTED
    assert record.certificate.total_deleted == sum(_EXPECTED.values())
    assert record.tenant_id == "acme"
    assert record.scope == "tenant"
    assert record.certificate.completed_at == _NOW
    assert record.certificate.verifier == "operator"

    assert fixture.runs.list_runs(ctx=_ACME) == []
    assert fixture.cost.list_events("acme", ctx=_ACME) == []
    assert fixture.cost.list_periods("acme", ctx=_ACME) == []
    assert fixture.cost.list_alerts("acme", ctx=_ACME) == []
    assert fixture.auth.list_keys("acme", ctx=_ACME) == []
    assert fixture.auth.list_logins("acme", ctx=_ACME) == []
    assert fixture.delivery.list_attempts("acme", ctx=_ACME) == []
    assert fixture.delivery.list_decisions("acme", ctx=_ACME) == []
    assert fixture.artifacts.list(tenant_id="acme", ctx=_ACME) == []
    assert fixture.reporting.list_reports(tenant_id="acme", ctx=_ACME) == []

    assert [run.id for run in fixture.runs.list_runs(ctx=_BETA)] == ["run-beta"]
    assert [
        event.event_id for event in fixture.cost.list_events("beta", ctx=_BETA)
    ] == ["cost-beta"]
    assert [key.key_id for key in fixture.auth.list_keys("beta", ctx=_BETA)] == ["key-beta"]
    assert [
        attempt.attempt_id
        for attempt in fixture.delivery.list_attempts("beta", ctx=_BETA)
    ] == ["attempt-beta"]
    assert [
        report.report_id for report in fixture.reporting.list_reports(tenant_id="beta", ctx=_BETA)
    ] == ["report-beta"]
    assert [
        artifact.artifact_id
        for artifact in fixture.artifacts.list(tenant_id="beta", ctx=_BETA)
    ] != []


def test_purge_removes_only_target_tenant_retention_policies(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    fixture.seed("acme")
    fixture.seed("beta")
    fixture.artifact_store.save_retention(_policy("acme", legal_hold=True), ctx=_ACME)
    fixture.artifact_store.save_retention(_policy("beta"), ctx=_BETA)

    record = fixture.service.purge(_ACME, "acme")

    counts = {count.store: count.deleted for count in record.certificate.stores}
    assert counts["artifacts"] == 2
    assert fixture.artifact_store.list_retention(tenant_id="acme") == []
    assert [
        policy.policy_id
        for policy in fixture.artifact_store.list_retention(tenant_id="beta", ctx=_BETA)
    ] == ["retention:beta:metering"]
    assert fixture.artifacts.list(tenant_id="acme", ctx=_ACME) == []


def test_cost_purge_removes_target_tenant_dead_letters() -> None:
    store = InMemoryCostStore()
    store.dead_letter(_cost("acme-dead", "acme"), "boom")
    store.dead_letter(_cost("beta-dead", "beta"), "boom")

    deleted = store.purge_tenant("acme")

    assert deleted == 1
    remaining = store.dead_letters()
    assert len(remaining) == 1
    event = remaining[0]["event"]
    assert isinstance(event, dict)
    assert event["tenant_id"] == "beta"


def test_purge_persists_certificate_and_audits(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    fixture.seed("acme")

    record = fixture.service.purge(_ACME, "acme", actor="operator")

    stored = fixture.reporting.get_purge(record.purge_id, tenant_id="acme", ctx=_ACME)
    assert stored is not None
    assert stored.purge_id == record.purge_id
    assert fixture.service.verify(record, fixture.public_key) is True

    tampered = record.model_copy(
        update={
            "certificate": record.certificate.model_copy(
                update={"total_deleted": record.certificate.total_deleted + 1}
            )
        }
    )
    assert fixture.service.verify(tampered, fixture.public_key) is False

    actions = [entry.action for entry in fixture.audit.records(ctx=_ACME)]
    assert actions == ["tenant.purged"]
    assert fixture.service.list(_ACME) == [record]


def test_verify_binds_record_wrapper_to_certificate(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    fixture.seed("acme")
    record = fixture.service.purge(_ACME, "acme")
    assert fixture.service.verify(record, fixture.public_key) is True

    for update in (
        {"tenant_id": "beta"},
        {"scope": "other"},
        {"completed_at": _NOW.replace(day=26)},
        {"purge_id": "purge-tampered"},
    ):
        tampered = record.model_copy(update=update)
        assert fixture.service.verify(tampered, fixture.public_key) is False


def test_verify_rejects_foreign_public_key(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    fixture.seed("acme")
    record = fixture.service.purge(_ACME, "acme")
    _, other_public = generate_keypair()

    assert fixture.service.verify(record, other_public) is False


def test_verify_rejects_unknown_signature_algorithm(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    fixture.seed("acme")
    record = fixture.service.purge(_ACME, "acme")
    certificate = record.certificate.model_copy(
        update={"signature": record.certificate.signature.model_copy(update={"algorithm": "rsa"})}
    )
    tampered = record.model_copy(update={"certificate": certificate})

    assert fixture.service.verify(tampered, fixture.public_key) is False


def test_service_generates_its_own_key_when_absent(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    fixture.seed("acme")
    service = TenantPurgeService(
        fixture.reporting,
        [PurgeTarget("audit", lambda _tenant: 0)],
        legal_hold_check=lambda _tenant: False,
        clock=lambda: _NOW,
    )

    record = service.purge(_ACME, "acme")

    assert service.verify(record, service.public_key()) is True
    assert service.public_key_pem().startswith("-----BEGIN PUBLIC KEY-----")


def test_legal_hold_blocks_purge_without_certificate(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    fixture.seed("acme")
    fixture.held.add("acme")

    with pytest.raises(PurgeIncompleteError):
        fixture.service.purge(_ACME, "acme")

    assert fixture.runs.list_runs(ctx=_ACME) != []
    assert fixture.cost.list_events("acme", ctx=_ACME) != []
    assert fixture.service.list(_ACME) == []
    assert fixture.audit.records(ctx=_ACME) == []


def test_audit_target_is_retained_and_recorded(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    fixture.seed("acme")
    fixture.audit.append("alice", "run.created", "run-acme", ctx=_ACME)

    record = fixture.service.purge(_ACME, "acme")

    counts = {count.store: count.deleted for count in record.certificate.stores}
    assert counts["audit"] == 0
    assert any(entry.action == "run.created" for entry in fixture.audit.records(ctx=_ACME))


def test_reporting_purge_never_deletes_purge_records(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    fixture.seed("acme")
    record = fixture.service.purge(_ACME, "acme")
    fixture.seed("acme")

    deleted = fixture.reporting.purge_tenant_reports("acme")

    assert deleted == 4
    assert fixture.reporting.list_reports(tenant_id="acme", ctx=_ACME) == []
    assert fixture.reporting.get_purge(record.purge_id, tenant_id="acme", ctx=_ACME) is not None


def test_run_store_purge_tenant_removes_whole_bundle(tmp_path: Path) -> None:
    store = JsonFileRunStore(tmp_path)
    store.save_run(_run("acme-run", "acme"), ctx=_ACME)
    store.save_run(_run("beta-run", "beta"), ctx=_BETA)

    deleted = store.purge_tenant("acme", ctx=_ACME)

    assert deleted == 1
    assert store.list_runs(ctx=_ACME) == []
    assert [run.id for run in store.list_runs(ctx=_BETA)] == ["beta-run"]
    assert not list(tmp_path.glob("acme__*.json"))


def test_tenant_store_purge_removes_tenant_teams_and_memberships() -> None:
    store = InMemoryTenantStore()
    store.save_tenant(_ACME, Tenant(tenant_id="acme", name="Acme", created_at=_NOW))
    store.save_tenant(_BETA, Tenant(tenant_id="beta", name="Beta", created_at=_NOW))
    store.save_team(
        _ACME,
        Team(team_id="team-a", tenant_id="acme", name="A", attribution_key="a", created_at=_NOW),
    )
    store.save_membership(
        _ACME,
        Membership(
            membership_id="m1",
            tenant_id="acme",
            operator_id="alice",
            role=Role.ADMIN,
            created_at=_NOW,
        ),
    )

    deleted = store.purge_tenant(_ACME, "acme")

    assert deleted == 3
    assert store.get_tenant(_ACME, "acme") is None
    assert store.list_teams(_ACME, "acme") == []
    assert store.list_memberships(_ACME, "acme") == []
    assert store.get_tenant(_BETA, "beta") is not None


def test_service_list_is_tenant_scoped(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    fixture.seed("acme")
    fixture.seed("beta")

    acme = fixture.service.purge(_ACME, "acme")
    fixture.service.purge(_BETA, "beta")

    assert fixture.service.list(_ACME) == [acme]


def test_postgres_store_purges(pg_engine: Engine) -> None:
    reset_database(pg_engine)
    seed_workload(pg_engine, tenant_id="acme")
    seed_workload(pg_engine, "agent-2", tenant_id="beta")
    runs = PostgresRunStore(pg_engine)
    cost = PostgresCostStore(pg_engine)
    auth = PostgresAuthStore(pg_engine)
    delivery = PostgresDeliveryStore(pg_engine)
    reporting = PostgresReportingStore(pg_engine)
    tenancy = PostgresTenantStore(pg_engine)
    artifacts = PostgresArtifactStore(pg_engine)

    runs.save_run(_run("acme-run", "acme"), ctx=_ACME)
    runs.save_run(_run("beta-run", "beta", workload_id="agent-2"), ctx=_BETA)
    cost.save_event(_cost("acme-cost", "acme"), ctx=_ACME)
    cost.save_period(_period("acme-period", "acme"), ctx=_ACME)
    cost.save_alert(_alert("acme-alert", "acme"), ctx=_ACME)
    cost.dead_letter(_cost("acme-dead", "acme"), "boom")
    cost.dead_letter(_cost("beta-dead", "beta"), "boom")
    artifacts.save_retention(_policy("acme", legal_hold=True), ctx=_ACME)
    artifacts.save_retention(_policy("beta"), ctx=_BETA)
    auth.save_key(_key("acme-key", "acme"), ctx=_ACME)
    auth.save_login(_login("acme"), ctx=_ACME)
    delivery.save_attempt(_attempt("acme-attempt", "acme"), ctx=_ACME)
    delivery.save_decision(_decision("acme-approval", "acme"), ctx=_ACME)
    delivery.save_preference(_preference("acme"), ctx=_ACME)
    reporting.save_report(_report("acme-report", "acme"), ctx=_ACME)
    reporting.save_schedule(_schedule("acme-schedule", "acme"), ctx=_ACME)
    reporting.save_export(_export("acme-export", "acme"), ctx=_ACME)
    reporting.save_evidence(_evidence("acme-pack", "acme"), ctx=_ACME)
    tenancy.save_tenant(_ACME, Tenant(tenant_id="acme", name="Acme", created_at=_NOW))
    tenancy.save_team(
        _ACME,
        Team(team_id="team-a", tenant_id="acme", name="A", attribution_key="a", created_at=_NOW),
    )
    tenancy.save_membership(
        _ACME,
        Membership(
            membership_id="m1",
            tenant_id="acme",
            operator_id="alice",
            role=Role.ADMIN,
            created_at=_NOW,
        ),
    )

    assert runs.purge_tenant("acme", ctx=_ACME) == 1
    assert cost.purge_tenant("acme") == 4
    assert auth.purge_tenant("acme", ctx=_ACME) == 2
    assert delivery.purge_tenant("acme") == 3
    assert reporting.purge_tenant_reports("acme") == 4
    assert tenancy.purge_tenant(_ACME, "acme") == 3
    assert artifacts.purge_tenant("acme") == 1

    assert runs.list_runs(ctx=_ACME) == []
    assert cost.list_events("acme", ctx=_ACME) == []
    assert auth.list_keys("acme", ctx=_ACME) == []
    assert delivery.list_attempts("acme", ctx=_ACME) == []
    assert reporting.list_reports(tenant_id="acme", ctx=_ACME) == []
    assert tenancy.get_tenant(_ACME, "acme") is None
    assert artifacts.list_retention(tenant_id="acme", ctx=_ACME) == []

    assert [run.id for run in runs.list_runs(ctx=_BETA)] == ["beta-run"]
    assert [str(entry["tenant_id"]) for entry in cost.dead_letters()] == ["beta"]
    assert [
        policy.policy_id for policy in artifacts.list_retention(tenant_id="beta", ctx=_BETA)
    ] == ["retention:beta:metering"]


def test_certificate_payload_is_domain_separated(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    fixture.seed("acme")
    record = fixture.service.purge(_ACME, "acme")

    document = record.certificate.model_dump(mode="json", exclude={"signature"})
    payload = b"hiveplane/reporting/purge/v1|" + canonical_json(document).encode("utf-8")
    import base64
    import hashlib

    assert record.certificate.signature.digest == hashlib.sha256(payload).hexdigest()
    signature = base64.b64decode(record.certificate.signature.signature, validate=True)
    fixture.public_key.verify(signature, payload)
