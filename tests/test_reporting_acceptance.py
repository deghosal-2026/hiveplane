"""End-to-end M57 acceptance tests (#448): digest, audit export, evidence, retention/PII, purge.

Each test drives the wired application through ``create_app()`` / ``TestClient``
and proves one of the five M57 acceptance criteria plus cross-tenant denial.
"""

from __future__ import annotations

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
from hiveplane.drift.models import QuarantineRecord
from hiveplane.fleet.cost import CostType
from hiveplane.reporting.models import (
    AuditExport,
    EvidencePack,
    PurgeRecord,
    ReportKind,
    ReportRun,
)
from hiveplane.tenancy.context import context_for_run

_EMAIL = "alice@example.com"
_WIDE_START = "2000-01-01T00:00:00Z"
_WIDE_END = "2100-01-01T00:00:00Z"


def _seed_spend(
    app: FastAPI, tenant_id: str, workload: str, cost_usd: float, *, completed: bool = True
) -> None:
    app.state.cost_service.record(
        CostEvent(
            event_id=f"{tenant_id}-{workload}",
            tenant_id=tenant_id,
            team_id="team-a",
            workload_id=workload,
            cost_type=CostType.LLM,
            cost_usd=cost_usd,
            completed=completed,
            occurred_at=datetime.now(UTC),
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


def _seed_approval(
    app: FastAPI, tenant_id: str, approval_id: str, *, when: datetime | None = None
) -> None:
    app.state.approval_service._store.save(
        ApprovalRecord(
            approval_id=approval_id,
            run_id=f"run-{approval_id}",
            workload=f"w-{approval_id}",
            rule="rule",
            reason="escalated",
            requested_at=when or datetime.now(UTC),
            tenant_id=tenant_id,
        ),
        ctx=context_for_run(tenant_id),
    )


def _certification(record_id: str, when: datetime) -> CertificationRecord:
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


def test_acceptance_1_weekly_digest_content() -> None:
    """AC1: the digest reports accurate spend/ROI/drift/approval content."""
    app = create_app()
    _seed_spend(app, "acme", "w1", 12.0)
    _seed_spend(app, "acme", "w2", 8.0)
    _seed_drift(app, "acme", "q-acme")
    _seed_approval(app, "acme", "ap-acme")
    _seed_spend(app, "beta", "w-beta", 999.0)
    client = TestClient(app)

    response = client.get("/reports/digest", headers={"X-Hiveplane-Tenant": "acme"})

    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == "acme"
    assert body["spend"]["total_cost_usd"] == 20.0
    assert body["roi"]["total_spend_usd"] == 20.0
    assert body["roi"]["fleet_roi"] > 0
    assert {row["workload_id"] for row in body["roi"]["rows"]} == {"w1", "w2"}
    assert body["drift"]["quarantined"] == 1
    assert body["drift"]["active"] == 1
    assert body["approvals"]["total"] == 1

    markdown = client.get(
        "/reports/digest",
        headers={"X-Hiveplane-Tenant": "acme"},
        params={"format": "markdown"},
    )
    assert markdown.status_code == 200
    for section in ("## Spend", "## ROI", "## Drift", "## Approvals"):
        assert section in markdown.text


def test_acceptance_2_audit_export_round_trip_and_tamper() -> None:
    """AC2: the audit export round-trips, verifies, and a tampered copy fails."""
    app = create_app()
    acme = context_for_run("acme")
    app.state.audit_log.append("alice", "run.created", "run-1", ctx=acme)
    app.state.audit_log.append("alice", "run.completed", "run-1", ctx=acme)
    app.state.audit_log.append("bob", "run.created", "run-2", ctx=context_for_run("beta"))
    client = TestClient(app)

    response = client.get(
        "/audit/export",
        headers={"X-Hiveplane-Tenant": "acme"},
        params={"period_start": _WIDE_START, "period_end": _WIDE_END, "format": "csv"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["record_count"] == 2
    assert body["integrity_proof"]["leaf_count"] == 2
    assert "bob" not in body["content"]

    export = AuditExport.model_validate(body)
    assert app.state.audit_export_service.verify(export) is True

    fetched = client.get(
        f"/audit/exports/{export.export_id}", headers={"X-Hiveplane-Tenant": "acme"}
    )
    assert fetched.status_code == 200
    assert fetched.json() == body

    tampered = export.model_copy(update={"content": export.content.replace("alice", "mallory")})
    assert app.state.audit_export_service.verify(tampered) is False


def test_acceptance_2_audit_export_interleaved_tenant_window() -> None:
    """AC2: an interleaved (non-contiguous) tenant window still exports and verifies."""
    app = create_app()
    acme = context_for_run("acme")
    beta = context_for_run("beta")
    app.state.audit_log.append("alice", "run.created", "run-1", ctx=acme)
    app.state.audit_log.append("bob", "run.created", "run-2", ctx=beta)
    app.state.audit_log.append("alice", "run.completed", "run-1", ctx=acme)
    client = TestClient(app)

    response = client.get(
        "/audit/export",
        headers={"X-Hiveplane-Tenant": "acme"},
        params={"period_start": _WIDE_START, "period_end": _WIDE_END, "format": "json"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["record_count"] == 2
    assert "bob" not in body["content"]
    export = AuditExport.model_validate(body)
    assert app.state.audit_export_service.verify(export) is True


def test_acceptance_3_evidence_pack_complete_and_verifies() -> None:
    """AC3: the evidence pack carries approvals + attestations + spend and verifies."""
    app = create_app()
    now = datetime.now(UTC)
    _seed_approval(app, "acme", "ap-acme", when=now)
    app.state.cost_store.save_event(
        CostEvent(
            event_id="ev-acme",
            tenant_id="acme",
            team_id="team-a",
            workload_id="w1",
            cost_type=CostType.LLM,
            cost_usd=7.5,
            completed=True,
            occurred_at=now,
        ),
        ctx=context_for_run("acme"),
    )
    app.state.certification_store.add(
        _certification("rec-acme", now), ctx=context_for_run("acme")
    )
    _seed_approval(app, "beta", "ap-beta", when=now)
    client = TestClient(app)

    response = client.post(
        "/compliance/evidence-pack",
        headers={"X-Hiveplane-Tenant": "acme"},
        json={"period_start": now.isoformat(), "period_end": now.isoformat()},
    )

    assert response.status_code == 200
    pack = EvidencePack.model_validate(response.json())
    names = {item.name for item in pack.files}
    assert names == {"index.json", "approvals.json", "attestations.json", "spend.json"}
    approvals = next(item for item in pack.files if item.name == "approvals.json")
    attestations = next(item for item in pack.files if item.name == "attestations.json")
    spend = next(item for item in pack.files if item.name == "spend.json")
    assert "ap-acme" in approvals.content
    assert "ap-beta" not in approvals.content
    assert "rec-acme" in attestations.content
    assert '"total_cost_usd":7.5' in spend.content
    index = next(item for item in pack.files if item.name == "index.json")
    assert '"approvals":1' in index.content
    assert '"spend_events":1' in index.content

    key_response = client.get("/compliance/evidence-key", headers={"X-Hiveplane-Tenant": "acme"})
    public_key = serialization.load_pem_public_key(key_response.content)
    assert app.state.evidence_service.verify(pack, public_key) is True


def test_acceptance_4_retention_schedule_and_tenant_purge() -> None:
    """AC4: retention deletes only expired data; tenant purge leaves a signed record."""
    app = create_app()
    old = datetime.now(UTC) - timedelta(days=10)
    for tenant, marker in (("acme", "old"), ("beta", "beta")):
        app.state.cost_store.save_event(
            CostEvent(
                event_id=f"{tenant}-{marker}",
                tenant_id=tenant,
                team_id="team-a",
                workload_id="w1",
                cost_type=CostType.LLM,
                cost_usd=1.0,
                occurred_at=old,
            ),
            ctx=context_for_run(tenant),
        )
    app.state.cost_store.save_event(
        CostEvent(
            event_id="acme-fresh",
            tenant_id="acme",
            team_id="team-a",
            workload_id="w1",
            cost_type=CostType.LLM,
            cost_usd=1.0,
            occurred_at=datetime.now(UTC),
        ),
        ctx=context_for_run("acme"),
    )
    client = TestClient(app)
    client.put(
        "/retention/policies/metering",
        headers={"X-Hiveplane-Tenant": "acme"},
        json={"retain_days": 1},
    )

    enforced = client.post("/retention/enforce", headers={"X-Hiveplane-Tenant": "acme"})

    assert enforced.status_code == 200
    classes = {item["store"]: item["deleted"] for item in enforced.json()["classes"]}
    assert classes == {"metering": 1}
    assert [
        event.event_id
        for event in app.state.cost_store.list_events(
            "acme", ctx=context_for_run("acme")
        )
    ] == ["acme-fresh"]
    assert [
        event.event_id
        for event in app.state.cost_store.list_events(
            "beta", ctx=context_for_run("beta")
        )
    ] == ["beta-beta"]

    purged = client.post("/tenants/acme/purge", headers={"X-Hiveplane-Tenant": "acme"})

    assert purged.status_code == 200
    record = PurgeRecord.model_validate(purged.json())
    assert record.tenant_id == "acme"
    assert record.scope == "tenant"
    stores = {item.store: item.deleted for item in record.certificate.stores}
    assert stores["cost"] == 1
    assert stores["audit"] == 0

    public_key = app.state.tenant_purge_service.public_key()
    assert app.state.tenant_purge_service.verify(record, public_key) is True
    key_pem = client.get("/compliance/evidence-key", headers={"X-Hiveplane-Tenant": "acme"})
    shared_key = serialization.load_pem_public_key(key_pem.content)
    assert app.state.tenant_purge_service.verify(record, shared_key) is True

    assert app.state.cost_store.list_events("acme", ctx=context_for_run("acme")) == []
    assert [
        event.event_id
        for event in app.state.cost_store.list_events(
            "beta", ctx=context_for_run("beta")
        )
    ] == ["beta-beta"]
    listed = client.get("/tenants/acme/purge-records", headers={"X-Hiveplane-Tenant": "acme"})
    assert [item["purge_id"] for item in listed.json()] == [record.purge_id]


def test_acceptance_5_pii_scrubbed_before_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC5: with PII on, audit detail and artifact text are redacted; chain verifies."""
    monkeypatch.setenv("HIVEPLANE_REPORTING__PII_ENABLED", "true")
    from hiveplane.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    acme = context_for_run("acme")

    record = app.state.audit_log.append(
        "alice", "contact.created", "c-1", detail=f"email {_EMAIL}", ctx=acme
    )

    assert record.detail == "email [REDACTED:email]"
    assert app.state.audit_log.verify() is True
    stored = app.state.audit_log.records(ctx=acme)
    assert stored[0].detail == "email [REDACTED:email]"
    assert _EMAIL not in (stored[0].detail or "")

    artifact = app.state.artifact_service.capture(
        tenant_id="acme",
        run_id="run-1",
        filename="log.txt",
        data=f"user {_EMAIL}".encode(),
        ctx=acme,
    )
    _, data = app.state.artifact_service.data(
        artifact.artifact_id, tenant_id="acme", ctx=acme
    )
    assert data == b"user [REDACTED:email]"

    client = TestClient(app)
    exported = client.get(
        "/audit/export",
        headers={"X-Hiveplane-Tenant": "acme"},
        params={"period_start": _WIDE_START, "period_end": _WIDE_END, "format": "json"},
    )
    assert exported.status_code == 200
    assert _EMAIL not in exported.json()["content"]
    assert app.state.audit_export_service.verify(AuditExport.model_validate(exported.json()))


def test_cross_tenant_denial_for_every_new_endpoint() -> None:
    """A second tenant cannot read, export, or generate the first tenant's data."""
    app = create_app()
    now = datetime.now(UTC)
    _seed_spend(app, "acme", "w1", 5.0)
    _seed_approval(app, "acme", "ap-acme", when=now)
    app.state.audit_log.append("alice", "run.created", "run-1", ctx=context_for_run("acme"))
    app.state.reporting_store.save_report(
        ReportRun(
            report_id="rep-acme",
            tenant_id="acme",
            kind=ReportKind.DIGEST,
            period_key="2026-W39",
            output_ref="reports/rep-acme",
            generated_at=now,
        ),
        ctx=context_for_run("acme"),
    )
    client = TestClient(app)
    acme = {"X-Hiveplane-Tenant": "acme"}
    beta = {"X-Hiveplane-Tenant": "beta"}

    exported = client.get(
        "/audit/export",
        headers=acme,
        params={"period_start": _WIDE_START, "period_end": _WIDE_END, "format": "json"},
    )
    assert exported.status_code == 200
    evidence = client.post(
        "/compliance/evidence-pack",
        headers=acme,
        json={"period_start": now.isoformat(), "period_end": now.isoformat()},
    )
    assert evidence.status_code == 200
    pack_id = evidence.json()["pack_id"]
    export_id = exported.json()["export_id"]

    assert client.get("/reports", headers=beta).json() == []
    assert client.get("/reports/rep-acme", headers=beta).status_code == 404
    assert client.get("/reports/digest", headers=beta).json()["spend"]["total_cost_usd"] == 0.0
    assert client.post(
        "/reports/digest/send",
        headers=beta,
        json={"tenant_id": "acme", "period": "week"},
    ).status_code == 403
    assert client.get("/reports/schedules", headers=beta).json() == []
    assert client.get("/audit/exports", headers=beta).json() == []
    assert client.get(f"/audit/exports/{export_id}", headers=beta).status_code == 404
    assert client.get("/compliance/evidence-packs", headers=beta).json() == []
    assert client.get(f"/compliance/evidence-packs/{pack_id}", headers=beta).status_code == 404
    assert client.get("/retention/policies", headers=beta).json() == []
    assert client.post("/tenants/acme/purge", headers=beta).status_code == 403
    assert client.get("/tenants/acme/purge-records", headers=beta).status_code == 403

    beta_pack = client.post(
        "/compliance/evidence-pack",
        headers=beta,
        json={"period_start": now.isoformat(), "period_end": now.isoformat()},
    )
    assert beta_pack.status_code == 200
    approvals = next(
        item for item in beta_pack.json()["files"] if item["name"] == "approvals.json"
    )
    assert "ap-acme" not in approvals["content"]

    enforced = client.post("/retention/enforce", headers=beta)
    assert enforced.status_code == 200
    assert {item["store"] for item in enforced.json()["classes"]} == set()
    assert app.state.cost_store.list_events("acme", ctx=context_for_run("acme")) != []
    assert app.state.audit_log.records(ctx=context_for_run("acme"))
