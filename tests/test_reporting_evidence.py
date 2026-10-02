"""Unit tests for signed compliance evidence packs (M57-04)."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

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
from hiveplane.certification.signing import generate_keypair
from hiveplane.certification.store import InMemoryCertificationStore
from hiveplane.core.approval import ApprovalRecord
from hiveplane.cost.models import CostEvent
from hiveplane.cost.service import CostService
from hiveplane.cost.store import InMemoryCostStore
from hiveplane.fleet.cost import CostType
from hiveplane.persistence.audit import InMemoryAuditLog
from hiveplane.policy.approvals import ApprovalService
from hiveplane.policy.store import InMemoryApprovalStore
from hiveplane.reporting.errors import EvidencePackNotFoundError
from hiveplane.reporting.evidence import EvidencePackService, _signing_payload
from hiveplane.reporting.models import EvidencePack, PackSignature
from hiveplane.reporting.store import InMemoryReportingStore
from hiveplane.tenancy.context import context_for_run

_START = datetime(2026, 9, 21, 0, 0, tzinfo=UTC)
_END = datetime(2026, 9, 27, 23, 59, tzinfo=UTC)


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@dataclass
class _Rig:
    service: EvidencePackService
    reporting: InMemoryReportingStore
    approvals: ApprovalService
    certifications: InMemoryCertificationStore
    cost: CostService
    audit: InMemoryAuditLog
    clock: _Clock
    private_key: Ed25519PrivateKey
    public_key: Ed25519PublicKey


def _rig() -> _Rig:
    clock = _Clock(_END)
    reporting = InMemoryReportingStore()
    approvals = ApprovalService(InMemoryApprovalStore())
    certifications = InMemoryCertificationStore()
    cost_store = InMemoryCostStore()
    cost = CostService(cost_store)
    audit = InMemoryAuditLog(clock=clock)
    private, public = generate_keypair()
    service = EvidencePackService(
        reporting,
        approvals,
        certifications,
        cost_store,
        private_key=private,
        clock=clock,
    )
    service.bind_audit(audit)
    return _Rig(
        service, reporting, approvals, certifications, cost, audit, clock, private, public
    )


def _seed_approval(
    rig: _Rig, tenant_id: str, approval_id: str, when: datetime
) -> ApprovalRecord:
    record = ApprovalRecord(
        approval_id=approval_id,
        run_id=f"run-{approval_id}",
        workload="w1",
        rule="rule",
        reason="escalated",
        requested_at=when,
        decided_at=when + timedelta(minutes=5),
        decided_by="alice",
        decision_reason="ok",
        tenant_id=tenant_id,
    )
    rig.approvals._store.save(record, ctx=context_for_run(tenant_id))
    return record


def _certification_record(
    record_id: str, workload: str, when: datetime
) -> CertificationRecord:
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
        timestamp=when,
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
            timestamp=when,
            attestation_id=attestation.attestation_id,
        ),
        attestation=attestation,
        benchmark_result=benchmark,
    )


def _seed_certification(
    rig: _Rig, tenant_id: str, record_id: str, when: datetime
) -> CertificationRecord:
    record = _certification_record(record_id, "w1", when)
    rig.certifications.add(record, ctx=context_for_run(tenant_id))
    return record


def _seed_spend(
    rig: _Rig, tenant_id: str, event_id: str, cost_usd: float, when: datetime
) -> None:
    rig.cost.record(
        CostEvent(
            event_id=event_id,
            tenant_id=tenant_id,
            team_id="team-a",
            workload_id="w1",
            cost_type=CostType.LLM,
            cost_usd=cost_usd,
            completed=True,
            occurred_at=when,
        )
    )


def _files(pack: EvidencePack) -> dict[str, str]:
    return {item.name: item.content for item in pack.files}


def test_generate_builds_files_manifest_and_signature() -> None:
    rig = _rig()
    _seed_approval(rig, "acme", "ap-in", _START + timedelta(hours=1))
    _seed_approval(rig, "acme", "ap-out", _START - timedelta(hours=1))
    _seed_certification(rig, "acme", "rec-in", _START + timedelta(hours=2))
    _seed_certification(rig, "acme", "rec-out", _START - timedelta(hours=2))
    _seed_spend(rig, "acme", "ev-1", 12.5, _START + timedelta(hours=3))

    pack = rig.service.generate(
        context_for_run("acme"), period_start=_START, period_end=_END
    )

    assert pack.tenant_id == "acme"
    assert pack.signature.algorithm == "ed25519"
    assert pack.signature.key_id == "reporting"
    assert rig.service.verify(pack, rig.public_key) is True

    files = _files(pack)
    assert set(files) == {"index.json", "approvals.json", "attestations.json", "spend.json"}
    for item in pack.files:
        assert item.digest == hashlib.sha256(item.content.encode("utf-8")).hexdigest()

    approvals = json.loads(files["approvals.json"])
    assert [row["approval_id"] for row in approvals] == ["ap-in"]
    attestations = json.loads(files["attestations.json"])
    assert [row["record_id"] for row in attestations] == ["rec-in"]
    spend = json.loads(files["spend.json"])
    assert spend["total_cost_usd"] == 12.5

    index = json.loads(files["index.json"])
    assert index["period_kind"] == "week"
    assert index["counts"] == {"approvals": 1, "attestations": 1, "spend_events": 1}
    assert index["files"] == {
        "approvals.json": pack.files[1].digest,
        "attestations.json": pack.files[2].digest,
        "spend.json": pack.files[3].digest,
    }


def test_generate_persists_and_audits() -> None:
    rig = _rig()
    _seed_approval(rig, "acme", "ap-1", _START + timedelta(hours=1))

    pack = rig.service.generate(
        context_for_run("acme"), period_start=_START, period_end=_END
    )

    stored = rig.reporting.list_evidence(ctx=context_for_run("acme"))
    assert [item.pack_id for item in stored] == [pack.pack_id]
    assert rig.service.get(pack.pack_id, ctx=context_for_run("acme")) == pack
    entries = rig.audit.records(ctx=context_for_run("acme"))
    assert any(
        entry.action == "compliance.evidence_pack.generated" for entry in entries
    )


def test_generate_is_tenant_scoped_and_never_crosses_tenants() -> None:
    rig = _rig()
    _seed_approval(rig, "acme", "ap-acme", _START + timedelta(hours=1))
    _seed_approval(rig, "beta", "ap-beta", _START + timedelta(hours=1))
    _seed_certification(rig, "beta", "rec-beta", _START + timedelta(hours=1))
    _seed_spend(rig, "acme", "ev-acme", 5.0, _START + timedelta(hours=1))
    _seed_spend(rig, "beta", "ev-beta", 99.0, _START + timedelta(hours=1))

    pack = rig.service.generate(
        context_for_run("acme"), period_start=_START, period_end=_END
    )

    files = _files(pack)
    assert "ap-beta" not in files["approvals.json"]
    assert "rec-beta" not in files["attestations.json"]
    assert json.loads(files["spend.json"])["total_cost_usd"] == 5.0
    assert rig.service.list(context_for_run("beta")) == []
    with pytest.raises(EvidencePackNotFoundError):
        rig.service.get(pack.pack_id, ctx=context_for_run("beta"))


def test_verify_detects_tampered_file_content() -> None:
    rig = _rig()
    _seed_approval(rig, "acme", "ap-1", _START + timedelta(hours=1))
    pack = rig.service.generate(
        context_for_run("acme"), period_start=_START, period_end=_END
    )

    tampered_file = pack.files[1].model_copy(update={"content": "[]"})
    tampered = pack.model_copy(
        update={"files": [pack.files[0], tampered_file, pack.files[2], pack.files[3]]}
    )

    assert rig.service.verify(tampered, rig.public_key) is False


def test_verify_detects_tampered_signature() -> None:
    rig = _rig()
    pack = rig.service.generate(
        context_for_run("acme"), period_start=_START, period_end=_END
    )

    forged = pack.signature.model_copy(
        update={"signature": base64.b64encode(b"\x00" * 64).decode("ascii")}
    )
    tampered = pack.model_copy(update={"signature": forged})

    assert rig.service.verify(tampered, rig.public_key) is False


def test_verify_rejects_other_public_key() -> None:
    rig = _rig()
    pack = rig.service.generate(
        context_for_run("acme"), period_start=_START, period_end=_END
    )
    _, other_public = generate_keypair()

    assert rig.service.verify(pack, other_public) is False


def test_verify_rejects_malformed_signature() -> None:
    rig = _rig()
    pack = rig.service.generate(
        context_for_run("acme"), period_start=_START, period_end=_END
    )
    forged = pack.signature.model_copy(update={"signature": "not-base64!!"})
    tampered = pack.model_copy(update={"signature": forged})

    assert rig.service.verify(tampered, rig.public_key) is False


def test_pack_verifies_offline_from_serialized_form() -> None:
    rig = _rig()
    _seed_approval(rig, "acme", "ap-1", _START + timedelta(hours=1))
    pack = rig.service.generate(
        context_for_run("acme"), period_start=_START, period_end=_END
    )

    reloaded = EvidencePack.model_validate(pack.model_dump(mode="json"))
    assert rig.service.verify(reloaded, rig.public_key) is True


def test_public_key_pem_round_trips() -> None:
    rig = _rig()
    pem = rig.service.public_key_pem()
    assert pem.startswith("-----BEGIN PUBLIC KEY-----")
    parsed = serialization.load_pem_public_key(pem.encode("ascii"))
    assert isinstance(parsed, Ed25519PublicKey)
    assert parsed.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ) == rig.public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def test_generate_ephemeral_key_when_none_provided() -> None:
    clock = _Clock(_END)
    service = EvidencePackService(
        InMemoryReportingStore(),
        ApprovalService(InMemoryApprovalStore()),
        InMemoryCertificationStore(),
        InMemoryCostStore(),
        clock=clock,
    )
    pack = service.generate(
        context_for_run("acme"), period_start=_START, period_end=_END
    )

    assert service.verify(pack, service.public_key()) is True


def test_get_missing_pack_raises() -> None:
    rig = _rig()
    with pytest.raises(EvidencePackNotFoundError):
        rig.service.get("missing", ctx=context_for_run("acme"))


def test_period_kind_derives_day_and_month() -> None:
    rig = _rig()

    day_pack = rig.service.generate(
        context_for_run("acme"),
        period_start=_START,
        period_end=_START + timedelta(hours=12),
    )
    day_index = json.loads(_files(day_pack)["index.json"])
    assert day_index["period_kind"] == "day"

    month_pack = rig.service.generate(
        context_for_run("acme"),
        period_start=_START,
        period_end=_START + timedelta(days=30),
    )
    month_index = json.loads(_files(month_pack)["index.json"])
    assert month_index["period_kind"] == "month"


def _resign(pack: EvidencePack, private_key: Ed25519PrivateKey) -> EvidencePack:
    payload = _signing_payload(pack)
    signed = private_key.sign(payload)
    signature = PackSignature(
        key_id="reporting",
        algorithm="ed25519",
        digest=hashlib.sha256(payload).hexdigest(),
        signature=base64.b64encode(signed).decode("ascii"),
    )
    return pack.model_copy(update={"signature": signature})


def test_verify_rejects_non_ed25519_algorithm() -> None:
    rig = _rig()
    pack = rig.service.generate(
        context_for_run("acme"), period_start=_START, period_end=_END
    )
    forged = pack.signature.model_copy(update={"algorithm": "rsa"})
    tampered = pack.model_copy(update={"signature": forged})

    assert rig.service.verify(tampered, rig.public_key) is False


def test_verify_rejects_validly_signed_but_inconsistent_file_digest() -> None:
    rig = _rig()
    pack = rig.service.generate(
        context_for_run("acme"), period_start=_START, period_end=_END
    )
    approvals = pack.files[1].model_copy(update={"content": '[{"forged":true}]'})
    reordered = pack.model_copy(
        update={"files": [pack.files[0], approvals, pack.files[2], pack.files[3]]}
    )

    assert rig.service.verify(_resign(reordered, rig.private_key), rig.public_key) is False


def test_verify_rejects_pack_missing_index() -> None:
    rig = _rig()
    pack = rig.service.generate(
        context_for_run("acme"), period_start=_START, period_end=_END
    )
    without_index = pack.model_copy(update={"files": pack.files[1:]})

    assert rig.service.verify(_resign(without_index, rig.private_key), rig.public_key) is False


def test_verify_rejects_malformed_index() -> None:
    rig = _rig()
    pack = rig.service.generate(
        context_for_run("acme"), period_start=_START, period_end=_END
    )
    malformed = pack.files[0].model_copy(
        update={
            "content": "not json",
            "digest": hashlib.sha256(b"not json").hexdigest(),
        }
    )
    tampered = pack.model_copy(
        update={"files": [malformed, pack.files[1], pack.files[2], pack.files[3]]}
    )

    assert rig.service.verify(_resign(tampered, rig.private_key), rig.public_key) is False


def test_verify_rejects_non_object_index() -> None:
    rig = _rig()
    pack = rig.service.generate(
        context_for_run("acme"), period_start=_START, period_end=_END
    )
    non_object = pack.files[0].model_copy(
        update={"content": "[]", "digest": hashlib.sha256(b"[]").hexdigest()}
    )
    tampered = pack.model_copy(
        update={"files": [non_object, pack.files[1], pack.files[2], pack.files[3]]}
    )

    assert rig.service.verify(_resign(tampered, rig.private_key), rig.public_key) is False


def test_spend_covers_exact_window_and_is_tenant_scoped() -> None:
    rig = _rig()
    window_start = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    window_end = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)
    _seed_spend(rig, "acme", "ev-inside", 3.0, window_start + timedelta(hours=1))
    _seed_spend(rig, "acme", "ev-before", 100.0, window_start - timedelta(minutes=1))
    _seed_spend(rig, "acme", "ev-after", 200.0, window_end + timedelta(minutes=1))
    _seed_spend(rig, "beta", "ev-other", 999.0, window_start + timedelta(hours=2))

    pack = rig.service.generate(
        context_for_run("acme"), period_start=window_start, period_end=window_end
    )
    spend = json.loads(_files(pack)["spend.json"])

    assert spend["period_start"] == window_start.isoformat()
    assert spend["period_end"] == window_end.isoformat()
    assert [event["event_id"] for event in spend["events"]] == ["ev-inside"]
    assert datetime.fromisoformat(
        spend["events"][0]["occurred_at"]
    ) == window_start + timedelta(hours=1)
    assert spend["event_count"] == 1
    assert spend["total_cost_usd"] == 3.0
    assert "ev-other" not in _files(pack)["spend.json"]
    assert rig.service.verify(pack, rig.public_key) is True
