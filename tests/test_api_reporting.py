"""API tests for the reporting reads surface (M57-01)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
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
from hiveplane.core.approval import ApprovalRecord
from hiveplane.cost.models import CostEvent
from hiveplane.delivery.models import (
    DeliveryChannel,
    DeliveryDestination,
    NotificationPreference,
)
from hiveplane.drift.models import QuarantineRecord
from hiveplane.fleet.cost import CostPeriodKind, CostType
from hiveplane.learning.models import EvalSample
from hiveplane.reporting.models import (
    AuditExport,
    EvidencePack,
    ReportKind,
    ReportRun,
    ReportSchedule,
)
from hiveplane.tenancy.context import context_for_run

_NOW = datetime(2026, 9, 27, tzinfo=UTC)
_WIDE_START = "2000-01-01T00:00:00Z"
_WIDE_END = "2100-01-01T00:00:00Z"


def _report(report_id: str, tenant_id: str) -> ReportRun:
    return ReportRun(
        report_id=report_id,
        tenant_id=tenant_id,
        kind=ReportKind.DIGEST,
        period_key="2026-W39",
        output_ref=f"reports/{report_id}",
        generated_at=_NOW,
    )


def test_reports_are_tenant_scoped() -> None:
    app = create_app()
    app.state.reporting_store.save_report(_report("rep-acme", "acme"), ctx=context_for_run("acme"))
    client = TestClient(app)

    acme = client.get("/reports", headers={"X-Hiveplane-Tenant": "acme"})
    assert acme.status_code == 200
    assert [record["report_id"] for record in acme.json()] == ["rep-acme"]

    beta = client.get("/reports", headers={"X-Hiveplane-Tenant": "beta"})
    assert beta.status_code == 200
    assert beta.json() == []

    filtered = client.get(
        "/reports", headers={"X-Hiveplane-Tenant": "acme"}, params={"kind": "digest"}
    )
    assert [record["report_id"] for record in filtered.json()] == ["rep-acme"]
    assert (
        client.get(
            "/reports", headers={"X-Hiveplane-Tenant": "acme"}, params={"kind": "purge"}
        ).json()
        == []
    )


def test_get_report_is_tenant_scoped_and_404s() -> None:
    app = create_app()
    app.state.reporting_store.save_report(_report("rep-acme", "acme"), ctx=context_for_run("acme"))
    client = TestClient(app)

    found = client.get("/reports/rep-acme", headers={"X-Hiveplane-Tenant": "acme"})
    assert found.status_code == 200
    assert found.json()["report_id"] == "rep-acme"

    assert (
        client.get("/reports/rep-acme", headers={"X-Hiveplane-Tenant": "beta"}).status_code == 404
    )
    assert client.get("/reports/missing", headers={"X-Hiveplane-Tenant": "acme"}).status_code == 404


def _seed_spend(app: FastAPI, tenant_id: str, workload: str, cost_usd: float) -> None:
    now = datetime.now(UTC)
    app.state.cost_service.record(
        CostEvent(
            event_id=f"{tenant_id}-{workload}",
            tenant_id=tenant_id,
            team_id="team-a",
            workload_id=workload,
            cost_type=CostType.LLM,
            cost_usd=cost_usd,
            completed=True,
            occurred_at=now,
        )
    )


def _seed_drift(app: FastAPI, tenant_id: str, quarantine_id: str) -> None:
    app.state.drift_store.add_quarantine(
        QuarantineRecord(
            quarantine_id=quarantine_id,
            workload=f"w-{quarantine_id}",
            reason="drift",
            timestamp=datetime.now(UTC),
            tenant_id=tenant_id,
        ),
        ctx=context_for_run(tenant_id),
    )


def _seed_approval(app: FastAPI, tenant_id: str, approval_id: str) -> None:
    app.state.approval_service._store.save(
        ApprovalRecord(
            approval_id=approval_id,
            run_id=f"run-{approval_id}",
            workload=f"w-{approval_id}",
            rule="rule",
            reason="escalated",
            requested_at=datetime.now(UTC),
            tenant_id=tenant_id,
        ),
        ctx=context_for_run(tenant_id),
    )


def test_digest_endpoint_is_tenant_scoped() -> None:
    app = create_app()
    _seed_spend(app, "acme", "w1", 12.0)
    _seed_spend(app, "beta", "w2", 99.0)
    _seed_drift(app, "beta", "q-beta")
    _seed_approval(app, "beta", "ap-beta")
    client = TestClient(app)

    acme = client.get("/reports/digest", headers={"X-Hiveplane-Tenant": "acme"})
    assert acme.status_code == 200
    body = acme.json()
    assert body["tenant_id"] == "acme"
    assert body["spend"]["total_cost_usd"] == 12.0
    assert body["drift"]["quarantined"] == 0
    assert body["drift"]["active"] == 0
    assert body["approvals"]["total"] == 0

    beta = client.get("/reports/digest", headers={"X-Hiveplane-Tenant": "beta"})
    assert beta.status_code == 200
    beta_body = beta.json()
    assert beta_body["tenant_id"] == "beta"
    assert beta_body["spend"]["total_cost_usd"] == 99.0
    assert beta_body["drift"]["quarantined"] == 1
    assert beta_body["drift"]["active"] == 1
    assert beta_body["approvals"]["total"] == 1

    # GET is a pure read: no report run persisted, no digest audit entry appended.
    assert (
        app.state.reporting_store.list_reports(tenant_id="acme", ctx=context_for_run("acme"))
        == []
    )
    assert not any(
        record.action == "report.digest.generated"
        for record in app.state.audit_log.records(ctx=context_for_run("acme"))
    )
    assert not any(
        record.action == "report.digest.generated"
        for record in app.state.audit_log.records(ctx=context_for_run("beta"))
    )


def test_digest_endpoint_renders_markdown() -> None:
    app = create_app()
    _seed_spend(app, "acme", "w1", 12.0)
    client = TestClient(app)

    response = client.get(
        "/reports/digest",
        headers={"X-Hiveplane-Tenant": "acme"},
        params={"period": "week", "format": "markdown"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "## Spend" in response.text
    assert "## ROI" in response.text
    assert "## Drift" in response.text
    assert "## Approvals" in response.text


def test_digest_send_endpoint_delivers_to_team_destinations() -> None:
    app = create_app()
    app.state.delivery_service.set_preference(
        NotificationPreference(
            team_id="team-a",
            tenant_id="acme",
            destinations=[DeliveryDestination(channel=DeliveryChannel.SLACK, target="#ops")],
        ),
        ctx=context_for_run("acme"),
    )
    client = TestClient(app)

    response = client.post(
        "/reports/digest/send",
        headers={"X-Hiveplane-Tenant": "acme"},
        json={"tenant_id": "acme", "team_id": "team-a", "period": "week"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["status"] == "delivered"
    assert body[0]["channel"] == "slack"
    assert body[0]["tenant_id"] == "acme"


def test_schedule_endpoints_round_trip() -> None:
    app = create_app()
    client = TestClient(app)

    created = client.post(
        "/reports/schedules",
        headers={"X-Hiveplane-Tenant": "acme"},
        json={
            "team_id": "team-a",
            "cron": "0 8 * * 1",
            "period_kind": "week",
            "channels": ["slack"],
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["tenant_id"] == "acme"
    assert body["team_id"] == "team-a"
    assert body["next_run"] is not None

    listed = client.get("/reports/schedules", headers={"X-Hiveplane-Tenant": "acme"})
    assert [item["schedule_id"] for item in listed.json()] == [body["schedule_id"]]

    other = client.get("/reports/schedules", headers={"X-Hiveplane-Tenant": "beta"})
    assert other.json() == []


def test_schedule_endpoint_rejects_invalid_cron() -> None:
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/reports/schedules",
        headers={"X-Hiveplane-Tenant": "acme"},
        json={"team_id": "team-a", "cron": "not a cron", "channels": ["slack"]},
    )

    assert response.status_code == 422
    assert "cron" in response.json()["detail"].lower()


def _schedule(tenant_id: str, schedule_id: str, next_run: datetime) -> ReportSchedule:
    return ReportSchedule(
        schedule_id=schedule_id,
        tenant_id=tenant_id,
        team_id="team-a",
        cron="0 8 * * *",
        period_kind=CostPeriodKind.WEEK,
        active=True,
        next_run=next_run,
        created_at=next_run - timedelta(days=1),
    )


def test_run_due_endpoint_delivers_matured_schedule_and_advances() -> None:
    app = create_app()
    matured = datetime.now(UTC) - timedelta(hours=1)
    app.state.delivery_service.set_preference(
        NotificationPreference(
            team_id="team-a",
            tenant_id="acme",
            destinations=[
                DeliveryDestination(channel=DeliveryChannel.SLACK, target="#ops")
            ],
        ),
        ctx=context_for_run("acme"),
    )
    app.state.reporting_store.save_schedule(
        _schedule("acme", "sched-acme", matured), ctx=context_for_run("acme")
    )
    client = TestClient(app)

    response = client.post(
        "/reports/schedules/run-due", headers={"X-Hiveplane-Tenant": "acme"}
    )

    assert response.status_code == 200
    body = response.json()
    assert [attempt["tenant_id"] for attempt in body] == ["acme"]
    assert body[0]["status"] == "delivered"
    reports = app.state.reporting_store.list_reports(
        tenant_id="acme", ctx=context_for_run("acme")
    )
    assert len(reports) == 1
    stored = app.state.reporting_store.list_schedules(ctx=context_for_run("acme"))
    assert stored[0].next_run is not None
    assert stored[0].next_run > datetime.now(UTC)
    actions = [
        record.action for record in app.state.audit_log.records(ctx=context_for_run("acme"))
    ]
    assert "report.digest.delivered" in actions


def test_run_due_endpoint_is_tenant_scoped() -> None:
    app = create_app()
    matured = datetime.now(UTC) - timedelta(hours=1)
    for tenant, target in (("acme", "#acme"), ("beta", "#beta")):
        app.state.delivery_service.set_preference(
            NotificationPreference(
                team_id="team-a",
                tenant_id=tenant,
                destinations=[
                    DeliveryDestination(channel=DeliveryChannel.SLACK, target=target)
                ],
            ),
            ctx=context_for_run(tenant),
        )
        app.state.reporting_store.save_schedule(
            _schedule(tenant, f"sched-{tenant}", matured),
            ctx=context_for_run(tenant),
        )
    client = TestClient(app)

    response = client.post(
        "/reports/schedules/run-due", headers={"X-Hiveplane-Tenant": "acme"}
    )

    assert response.status_code == 200
    assert [attempt["tenant_id"] for attempt in response.json()] == ["acme"]
    beta = app.state.reporting_store.list_schedules(ctx=context_for_run("beta"))
    assert beta[0].next_run == matured


def test_digest_send_does_not_route_to_other_tenant_shared_team_id() -> None:
    app = create_app()
    app.state.delivery_service.set_preference(
        NotificationPreference(
            team_id="team-a",
            tenant_id="beta",
            destinations=[DeliveryDestination(channel=DeliveryChannel.SLACK, target="#beta")],
        ),
        ctx=context_for_run("beta"),
    )
    client = TestClient(app)

    response = client.post(
        "/reports/digest/send",
        headers={"X-Hiveplane-Tenant": "acme"},
        json={"tenant_id": "acme", "team_id": "team-a", "period": "week"},
    )

    assert response.status_code == 200
    assert response.json() == []


def test_audit_export_round_trips_and_verifies() -> None:
    app = create_app()
    acme = context_for_run("acme")
    app.state.audit_log.append("alice", "run.created", "run-1", ctx=acme)
    app.state.audit_log.append("alice", "run.completed", "run-1", ctx=acme)
    app.state.audit_log.append("bob", "run.created", "run-2", ctx=context_for_run("beta"))
    client = TestClient(app)

    response = client.get(
        "/audit/export",
        headers={"X-Hiveplane-Tenant": "acme"},
        params={
            "period_start": _WIDE_START,
            "period_end": _WIDE_END,
            "format": "csv",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == "acme"
    assert body["format"] == "csv"
    assert body["record_count"] == 2
    assert body["integrity_proof"]["leaf_count"] == 2
    assert "bob" not in body["content"]

    export = AuditExport.model_validate(body)
    assert app.state.audit_export_service.verify(export) is True

    entries = app.state.audit_log.records(ctx=acme)
    assert any(entry.action == "audit.exported" for entry in entries)


def test_audit_export_json_verifies_and_is_tenant_scoped() -> None:
    app = create_app()
    acme = context_for_run("acme")
    app.state.audit_log.append("alice", "run.created", "run-1", ctx=acme)
    client = TestClient(app)

    created = client.get(
        "/audit/export",
        headers={"X-Hiveplane-Tenant": "acme"},
        params={"period_start": _WIDE_START, "period_end": _WIDE_END, "format": "json"},
    )
    assert created.status_code == 200
    export = AuditExport.model_validate(created.json())
    assert export.format.value == "json"
    assert app.state.audit_export_service.verify(export) is True

    listed = client.get("/audit/exports", headers={"X-Hiveplane-Tenant": "acme"})
    assert [item["export_id"] for item in listed.json()] == [export.export_id]
    assert client.get("/audit/exports", headers={"X-Hiveplane-Tenant": "beta"}).json() == []

    fetched = client.get(
        f"/audit/exports/{export.export_id}", headers={"X-Hiveplane-Tenant": "acme"}
    )
    assert fetched.status_code == 200
    assert fetched.json()["export_id"] == export.export_id

    assert (
        client.get(
            f"/audit/exports/{export.export_id}", headers={"X-Hiveplane-Tenant": "beta"}
        ).status_code
        == 404
    )
    assert (
        client.get("/audit/exports/missing", headers={"X-Hiveplane-Tenant": "acme"}).status_code
        == 404
    )


def _evidence_certification(record_id: str, when: datetime) -> CertificationRecord:
    attestation = Attestation(
        attestation_id=f"att-{record_id}",
        workload_id="w1",
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
        timestamp=when,
        environment=Environment(
            sandbox_image="img", runtime_adapter="raw-worker", control_plane_version="0.2.0"
        ),
        signer=Signer(identity="hiveplane", key_id="k1", signature="sig"),
    )
    benchmark = BenchmarkResult(
        benchmark_run_id=f"br-{record_id}",
        workload_id="w1",
        manifest_version=1,
        corpus_id="corpus-1",
        corpus_version=1,
        model_identity="fake/model",
        environment=attestation.environment,
        started_at=when,
        finished_at=when,
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
            workload_id="w1",
            manifest_version=1,
            status=CertificationStatus.CERTIFIED,
            target_context=TargetContext.PRODUCTION,
            benchmark_run_id=f"br-{record_id}",
            thresholds=Thresholds(
                min_pass_rate=0.8,
                max_critical_failures=0,
                max_p95_latency_ms=30000,
            ),
            timestamp=when,
            attestation_id=attestation.attestation_id,
        ),
        attestation=attestation,
        benchmark_result=benchmark,
    )


def _seed_evidence_inputs(app: FastAPI, tenant_id: str, when: datetime) -> None:
    _seed_approval(app, tenant_id, f"ap-{tenant_id}")
    app.state.cost_service.record(
        CostEvent(
            event_id=f"ev-{tenant_id}",
            tenant_id=tenant_id,
            team_id="team-a",
            workload_id="w1",
            cost_type=CostType.LLM,
            cost_usd=7.5,
            completed=True,
            occurred_at=when,
        )
    )
    app.state.certification_store.add(
        _evidence_certification(f"rec-{tenant_id}", when),
        ctx=context_for_run(tenant_id),
    )


def test_evidence_pack_endpoint_round_trips_and_verifies_offline() -> None:
    app = create_app()
    now = datetime.now(UTC)
    _seed_evidence_inputs(app, "acme", now)
    _seed_evidence_inputs(app, "beta", now)
    client = TestClient(app)

    response = client.post(
        "/compliance/evidence-pack",
        headers={"X-Hiveplane-Tenant": "acme"},
        json={"period_start": now.isoformat(), "period_end": now.isoformat()},
    )

    assert response.status_code == 200
    body = response.json()
    pack = EvidencePack.model_validate(body)
    assert pack.tenant_id == "acme"
    assert pack.signature.algorithm == "ed25519"
    assert {item.name for item in pack.files} == {
        "index.json",
        "approvals.json",
        "attestations.json",
        "spend.json",
    }
    approvals = next(item for item in pack.files if item.name == "approvals.json")
    assert "ap-beta" not in approvals.content
    spend = next(item for item in pack.files if item.name == "spend.json")
    assert '"total_cost_usd":7.5' in spend.content

    key_response = client.get("/compliance/evidence-key", headers={"X-Hiveplane-Tenant": "acme"})
    assert key_response.status_code == 200
    public_key = serialization.load_pem_public_key(key_response.content)
    assert app.state.evidence_service.verify(pack, public_key) is True

    listed = client.get("/compliance/evidence-packs", headers={"X-Hiveplane-Tenant": "acme"})
    assert [item["pack_id"] for item in listed.json()] == [pack.pack_id]
    assert client.get(
        "/compliance/evidence-packs", headers={"X-Hiveplane-Tenant": "beta"}
    ).json() == []

    fetched = client.get(
        f"/compliance/evidence-packs/{pack.pack_id}",
        headers={"X-Hiveplane-Tenant": "acme"},
    )
    assert fetched.status_code == 200
    assert fetched.json()["pack_id"] == pack.pack_id

    assert (
        client.get(
            f"/compliance/evidence-packs/{pack.pack_id}",
            headers={"X-Hiveplane-Tenant": "beta"},
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/compliance/evidence-packs/missing", headers={"X-Hiveplane-Tenant": "acme"}
        ).status_code
        == 404
    )

    entries = app.state.audit_log.records(ctx=context_for_run("acme"))
    assert any(entry.action == "compliance.evidence_pack.generated" for entry in entries)
    assert not any(
        entry.action == "compliance.evidence_pack.generated"
        for entry in app.state.audit_log.records(ctx=context_for_run("beta"))
    )


def test_retention_policy_endpoints_round_trip_and_are_tenant_scoped() -> None:
    app = create_app()
    client = TestClient(app)

    saved = client.put(
        "/retention/policies/runs",
        headers={"X-Hiveplane-Tenant": "acme"},
        json={"retain_days": 30, "legal_hold": False},
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["policy_id"] == "retention:acme:runs"
    assert body["tenant_id"] == "acme"
    assert body["data_class"] == "runs"
    assert body["retain_days"] == 30

    listed = client.get("/retention/policies", headers={"X-Hiveplane-Tenant": "acme"})
    assert [item["policy_id"] for item in listed.json()] == ["retention:acme:runs"]
    assert (
        client.get("/retention/policies", headers={"X-Hiveplane-Tenant": "beta"}).json()
        == []
    )


def test_retention_policy_rejects_invalid_data_class() -> None:
    app = create_app()
    client = TestClient(app)

    response = client.put(
        "/retention/policies/bogus",
        headers={"X-Hiveplane-Tenant": "acme"},
        json={"retain_days": 30},
    )

    assert response.status_code == 422


def test_retention_enforce_deletes_expired_class_data() -> None:
    app = create_app()
    old = datetime.now(UTC) - timedelta(days=10)
    app.state.cost_store.save_event(
        CostEvent(
            event_id="acme-old",
            tenant_id="acme",
            team_id="team-a",
            workload_id="w1",
            cost_type=CostType.LLM,
            cost_usd=1.0,
            occurred_at=old,
        ),
        ctx=context_for_run("acme"),
    )
    app.state.cost_store.save_event(
        CostEvent(
            event_id="beta-old",
            tenant_id="beta",
            team_id="team-a",
            workload_id="w1",
            cost_type=CostType.LLM,
            cost_usd=1.0,
            occurred_at=old,
        ),
        ctx=context_for_run("beta"),
    )
    client = TestClient(app)
    client.put(
        "/retention/policies/metering",
        headers={"X-Hiveplane-Tenant": "acme"},
        json={"retain_days": 1},
    )

    response = client.post("/retention/enforce", headers={"X-Hiveplane-Tenant": "acme"})

    assert response.status_code == 200
    classes = {item["store"]: item["deleted"] for item in response.json()["classes"]}
    assert classes == {"metering": 1}
    assert app.state.cost_store.list_events("acme", ctx=context_for_run("acme")) == []
    assert [
        event.event_id
        for event in app.state.cost_store.list_events(
            "beta", ctx=context_for_run("beta")
        )
    ] == [
        "beta-old"
    ]


def _seed_cost(app: FastAPI, tenant_id: str, event_id: str) -> None:
    app.state.cost_store.save_event(
        CostEvent(
            event_id=event_id,
            tenant_id=tenant_id,
            team_id="team-a",
            workload_id="w1",
            cost_type=CostType.LLM,
            cost_usd=1.0,
            occurred_at=datetime.now(UTC),
        ),
        ctx=context_for_run(tenant_id),
    )


def test_purge_endpoint_purges_only_target_tenant_and_verifies() -> None:
    app = create_app()
    _seed_cost(app, "acme", "acme-cost")
    _seed_cost(app, "beta", "beta-cost")
    client = TestClient(app)

    response = client.post("/tenants/acme/purge", headers={"X-Hiveplane-Tenant": "acme"})

    assert response.status_code == 200
    record = response.json()
    assert record["tenant_id"] == "acme"
    assert record["scope"] == "tenant"
    stores = {item["store"]: item["deleted"] for item in record["certificate"]["stores"]}
    assert stores["cost"] == 1
    assert stores["audit"] == 0
    assert record["certificate"]["total_deleted"] == 1

    assert app.state.cost_store.list_events("acme", ctx=context_for_run("acme")) == []
    assert [
        event.event_id
        for event in app.state.cost_store.list_events(
            "beta", ctx=context_for_run("beta")
        )
    ] == [
        "beta-cost"
    ]

    listed = client.get(
        "/tenants/acme/purge-records", headers={"X-Hiveplane-Tenant": "acme"}
    )
    assert [item["purge_id"] for item in listed.json()] == [record["purge_id"]]

    entries = app.state.audit_log.records(ctx=context_for_run("acme"))
    assert any(entry.action == "tenant.purged" for entry in entries)


class _TickerProbe:
    def __init__(self) -> None:
        self.calls = 0

    def run_all_due(self, *, at: object = None, ctx: object = None) -> list[object]:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("boom")
        return []


def test_digest_ticker_runs_when_enabled_and_survives_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hiveplane.api.app as app_module

    monkeypatch.setenv("HIVEPLANE_REPORTING__ENABLED", "true")
    monkeypatch.setattr(app_module, "_DIGEST_TICK_SECONDS", 0.01)
    app = create_app()
    probe = _TickerProbe()
    app.state.digest_scheduler = probe

    with TestClient(app):
        deadline = time.monotonic() + 2.0
        while probe.calls < 2 and time.monotonic() < deadline:
            time.sleep(0.01)

    assert probe.calls >= 2


def test_purge_certificate_marks_run_adjacent_stores_retained() -> None:
    app = create_app()
    _seed_cost(app, "acme", "acme-cost")
    app.state.eval_store.add_sample(
        EvalSample(
            sample_id="sample-1",
            run_id="run-1",
            workload_id="agent-1",
            rubric_version=1,
            sampled_at=datetime.now(UTC),
            tenant_id="acme",
        ),
        ctx=context_for_run("acme"),
    )
    client = TestClient(app)

    response = client.post("/tenants/acme/purge", headers={"X-Hiveplane-Tenant": "acme"})

    assert response.status_code == 200
    stores = {
        item["store"]: item["deleted"]
        for item in response.json()["certificate"]["stores"]
    }
    for retained in ("eval", "feedback", "progressive", "reconcile"):
        assert stores[retained] == 0
    assert app.state.eval_store.list_samples(ctx=context_for_run("acme")) != []


def test_purge_endpoint_is_tenant_scoped() -> None:
    app = create_app()
    _seed_cost(app, "beta", "beta-cost")
    client = TestClient(app)

    response = client.post("/tenants/beta/purge", headers={"X-Hiveplane-Tenant": "acme"})

    assert response.status_code == 403
    assert [
        event.event_id
        for event in app.state.cost_store.list_events(
            "beta", ctx=context_for_run("beta")
        )
    ] == [
        "beta-cost"
    ]


def test_purge_endpoint_blocked_by_legal_hold() -> None:
    app = create_app()
    _seed_cost(app, "acme", "acme-cost")
    client = TestClient(app)
    client.put(
        "/retention/policies/metering",
        headers={"X-Hiveplane-Tenant": "acme"},
        json={"retain_days": 1, "legal_hold": True},
    )

    response = client.post("/tenants/acme/purge", headers={"X-Hiveplane-Tenant": "acme"})

    assert response.status_code == 409
    assert [
        event.event_id
        for event in app.state.cost_store.list_events(
            "acme", ctx=context_for_run("acme")
        )
    ] == [
        "acme-cost"
    ]
    assert (
        client.get(
            "/tenants/acme/purge-records", headers={"X-Hiveplane-Tenant": "acme"}
        ).json()
        == []
    )

