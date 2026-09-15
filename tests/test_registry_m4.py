"""Tests for M4: versioning, admission, attestations, tools, and re-cert."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from hiveplane.certification.models import (
    Attestation,
    CertificationEvent,
    CertificationStatus,
)
from hiveplane.certification.signing import generate_keypair, sign_attestation
from hiveplane.core.tools import ToolTrustLevel
from hiveplane.core.triggers import TriggerRule, TriggerType
from hiveplane.core.workload import AgentWorkload
from hiveplane.registry.errors import (
    AdmissionRefusedError,
    AttestationAlreadyExistsError,
    AttestationNotFoundError,
    AttestationVerificationError,
    DestructiveToolRequiresApprovalError,
    ReCertificationRequiredError,
    ToolAlreadyExistsError,
    UnknownToolError,
    VersionNotFoundError,
    WorkloadNotFoundError,
)
from hiveplane.registry.models import AdmissionContext, ToolRecord, TriggerRecord
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore

ManifestFactory = Callable[..., AgentWorkload]
NOW = datetime(2026, 9, 12, tzinfo=UTC)


@pytest.fixture
def keypair() -> tuple[Any, Any]:
    return generate_keypair()


@pytest.fixture
def service(keypair: tuple[Any, Any]) -> RegistryService:
    _, public_key = keypair
    return RegistryService(
        InMemoryRegistryStore(),
        clock=lambda: NOW,
        attestation_public_key=public_key,
    )


def _attestation(workload: str, private_key: Any, **overrides: Any) -> Attestation:
    payload: dict[str, Any] = {
        "attestation_id": "att-1",
        "workload_id": workload,
        "manifest_version": 1,
        "benchmark_version": "1.0.0",
        "benchmark_run_id": "br-1",
        "corpus_id": "corpus",
        "corpus_version": 1,
        "model_identity": "gpt-4o-2024-08-06",
        "status": "certified",
        "target_context": "production",
        "eval_summary": {
            "pass_rate": 0.95,
            "critical_failures": 0,
            "p95_latency_ms": 1000,
            "tasks_passed": 19,
            "tasks_failed": 1,
        },
        "timestamp": "2026-09-12T10:00:00Z",
        "environment": {
            "sandbox_image": "img",
            "runtime_adapter": "raw-worker",
            "control_plane_version": "0.1.0",
        },
        "signer": {"identity": "cert@hiveplane", "key_id": "key-1", "signature": "pending"},
    }
    payload.update(overrides)
    return sign_attestation(Attestation.model_validate(payload), private_key)


def _tool(
    tool_id: str,
    *,
    trust: ToolTrustLevel = ToolTrustLevel.READ_ONLY,
    server: str = "srv",
) -> ToolRecord:
    return ToolRecord(
        tool_id=tool_id,
        name=tool_id,
        mcp_server=server,
        trust_level=trust,
        registered_at=NOW,
        registered_by="admin",
    )


# --------------------------------------------------------------------------- #
# Versioning and diff
# --------------------------------------------------------------------------- #
def test_versions_are_append_only(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    service.create(make_manifest("repo-agent"))
    service.update("repo-agent", make_manifest("repo-agent", owner="one"))
    service.update("repo-agent", make_manifest("repo-agent", owner="two"))

    versions = service.versions("repo-agent")
    assert [version.version for version in versions] == [1, 2, 3]
    assert versions[0].manifest.owner == "platform-team"
    assert versions[0].status.value == "superseded"


def test_version_diff_reports_changed_fields(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    service.create(make_manifest("repo-agent"))
    service.update("repo-agent", make_manifest("repo-agent", owner="new-team"))

    diff = service.version_diff("repo-agent", 1, 2)

    assert diff.changed_fields == ["metadata"]
    assert diff.re_certification_required is False


def test_version_diff_flags_cert_relevant_change(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    service.create(make_manifest("repo-agent"))
    service.update(
        "repo-agent",
        make_manifest(
            "repo-agent",
            model={
                "strategy": "fixed",
                "identity": {"provider": "openai", "family": "gpt-4o", "version": "2024-08-06"},
            },
        ),
    )

    diff = service.version_diff("repo-agent", 1, 2)

    assert diff.changed_fields == ["spec.model"]
    assert diff.re_certification_required is True


def test_version_diff_missing_version_raises(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    service.create(make_manifest("repo-agent"))

    with pytest.raises(VersionNotFoundError):
        service.version_diff("repo-agent", 1, 9)


def test_changed_fields_cover_all_non_cert_blocks(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    service.create(make_manifest("repo-agent"))
    service.update(
        "repo-agent",
        make_manifest(
            "repo-agent",
            budget={"per_run_usd": 1.0, "per_day_usd": 10.0, "per_team_usd": 100.0},
            approvals={"required_for": ["destructive"], "contact": "#x"},
            triggers=[{"type": "cron", "schedule": "0 0 * * *"}],
            fan_out={"on_completed": [{"type": "slack", "channel": "#r"}]},
            output_shaping={"max_bytes": 1024},
            health={"failure_rate_threshold": 0.2},
            observability={"contract": "verbose", "trace_sampling": 0.5},
        ),
    )

    diff = service.version_diff("repo-agent", 1, 2)

    assert {
        "spec.budget",
        "spec.approvals",
        "spec.triggers",
        "spec.fan_out",
        "spec.output_shaping",
        "spec.health",
        "spec.observability",
    }.issubset(set(diff.changed_fields))
    assert diff.re_certification_required is False


def test_update_dry_run_returns_summary(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    service.create(make_manifest("repo-agent"))

    summary = service.update("repo-agent", make_manifest("repo-agent"), dry_run=True)

    assert summary.valid is True


def test_promote_unknown_version_raises(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    service.create(make_manifest("repo-agent"))

    with pytest.raises(VersionNotFoundError):
        service.promote("repo-agent", 5)


def test_delete_missing_trigger_raises(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    service.create(make_manifest("repo-agent"))

    with pytest.raises(WorkloadNotFoundError):
        service.delete_trigger("repo-agent", "missing")


def test_get_unknown_tool_raises(service: RegistryService) -> None:
    with pytest.raises(UnknownToolError):
        service.get_tool("missing")


def test_list_attestations_rejects_tampered(
    service: RegistryService, make_manifest: ManifestFactory, keypair: tuple[Any, Any]
) -> None:
    private_key, _ = keypair
    service.create(make_manifest("repo-agent"))
    tampered = _attestation("repo-agent", private_key).model_copy(
        update={"model_identity": "evil"}
    )
    service._store.add_attestation(tampered)

    with pytest.raises(AttestationVerificationError):
        service.list_attestations("repo-agent")


def test_expired_attestation_blocks_production(
    service: RegistryService, make_manifest: ManifestFactory, keypair: tuple[Any, Any]
) -> None:
    private_key, _ = keypair
    service.create(
        make_manifest(
            "repo-agent",
            certification={
                "benchmark_corpus": "corpora/x/v1",
                "staging_threshold": 0.8,
                "production_threshold": 0.9,
                "status": "uncertified",
                "expires_at": "2020-01-01T00:00:00Z",
            },
        )
    )
    service.store_attestation(_attestation("repo-agent", private_key))
    service.record_certification_event("repo-agent", CertificationEvent.STAGING_PASS)
    service.record_certification_event("repo-agent", CertificationEvent.PRODUCTION_PASS)

    assert service.check_admission("repo-agent", AdmissionContext.PRODUCTION).admitted is False


# --------------------------------------------------------------------------- #
# Admission and certification lifecycle
# --------------------------------------------------------------------------- #
def test_sandbox_admits_uncertified(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    service.create(make_manifest("repo-agent"))

    decision = service.check_admission("repo-agent", AdmissionContext.SANDBOX)

    assert decision.admitted is True


def test_staging_refuses_uncertified(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    service.create(make_manifest("repo-agent"))

    decision = service.check_admission("repo-agent", AdmissionContext.STAGING)

    assert decision.admitted is False
    assert decision.required_status is CertificationStatus.PROVISIONAL


def test_staging_admits_provisional(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    service.create(make_manifest("repo-agent"))
    service.record_certification_event("repo-agent", CertificationEvent.STAGING_PASS)

    assert service.check_admission("repo-agent", AdmissionContext.STAGING).admitted is True


def test_production_refused_without_attestation(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    service.create(
        make_manifest(
            "repo-agent",
            certification={
                "benchmark_corpus": "corpora/x/v1",
                "staging_threshold": 0.8,
                "production_threshold": 0.9,
                "status": "uncertified",
                "expires_at": "2999-01-01T00:00:00Z",
            },
        )
    )
    service.record_certification_event("repo-agent", CertificationEvent.STAGING_PASS)
    service.record_certification_event("repo-agent", CertificationEvent.PRODUCTION_PASS)

    decision = service.check_admission("repo-agent", AdmissionContext.PRODUCTION)

    assert decision.admitted is False


def test_production_admitted_with_valid_attestation(
    service: RegistryService, make_manifest: ManifestFactory, keypair: tuple[Any, Any]
) -> None:
    private_key, _ = keypair
    service.create(
        make_manifest(
            "repo-agent",
            certification={
                "benchmark_corpus": "corpora/x/v1",
                "staging_threshold": 0.8,
                "production_threshold": 0.9,
                "status": "uncertified",
                "expires_at": "2999-01-01T00:00:00Z",
            },
        )
    )
    service.store_attestation(_attestation("repo-agent", private_key))
    service.record_certification_event("repo-agent", CertificationEvent.STAGING_PASS)
    service.record_certification_event("repo-agent", CertificationEvent.PRODUCTION_PASS)

    assert service.check_admission("repo-agent", AdmissionContext.PRODUCTION).admitted is True


def test_require_admission_names_required_status(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    service.create(make_manifest("repo-agent"))

    with pytest.raises(AdmissionRefusedError, match="certified"):
        service.require_admission("repo-agent", AdmissionContext.PRODUCTION)


# --------------------------------------------------------------------------- #
# Attestations
# --------------------------------------------------------------------------- #
def test_store_and_get_attestation(
    service: RegistryService, make_manifest: ManifestFactory, keypair: tuple[Any, Any]
) -> None:
    private_key, _ = keypair
    service.create(make_manifest("repo-agent"))
    attestation = _attestation("repo-agent", private_key)

    service.store_attestation(attestation)

    assert service.get_attestation("att-1") == attestation
    assert service.list_attestations("repo-agent") == [attestation]


def test_attestations_are_immutable(
    service: RegistryService, make_manifest: ManifestFactory, keypair: tuple[Any, Any]
) -> None:
    private_key, _ = keypair
    service.create(make_manifest("repo-agent"))
    service.store_attestation(_attestation("repo-agent", private_key))

    with pytest.raises(AttestationAlreadyExistsError):
        service.store_attestation(_attestation("repo-agent", private_key))


def test_tampered_attestation_fails_on_read(
    service: RegistryService, make_manifest: ManifestFactory, keypair: tuple[Any, Any]
) -> None:
    private_key, _ = keypair
    service.create(make_manifest("repo-agent"))
    attestation = _attestation("repo-agent", private_key)
    tampered = attestation.model_copy(update={"model_identity": "evil"})
    service._store.add_attestation(tampered)

    with pytest.raises(AttestationVerificationError):
        service.get_attestation("att-1")


def test_get_missing_attestation_raises(service: RegistryService) -> None:
    with pytest.raises(AttestationNotFoundError):
        service.get_attestation("missing")


# --------------------------------------------------------------------------- #
# Tools and triggers
# --------------------------------------------------------------------------- #
def test_register_and_list_tools(service: RegistryService) -> None:
    service.register_tool(_tool("t1"))
    service.register_tool(_tool("t2", trust=ToolTrustLevel.DESTRUCTIVE, server="other"))

    assert {tool.tool_id for tool in service.list_tools()} == {"t1", "t2"}
    destructive = service.list_tools(trust_level=ToolTrustLevel.DESTRUCTIVE)
    assert [tool.tool_id for tool in destructive] == ["t2"]
    assert [tool.tool_id for tool in service.list_tools(mcp_server="srv")] == ["t1"]


def test_duplicate_tool_is_rejected(service: RegistryService) -> None:
    service.register_tool(_tool("t1"))

    with pytest.raises(ToolAlreadyExistsError):
        service.register_tool(_tool("t1"))


def test_unknown_tool_rejected_at_registration(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    manifest = make_manifest(
        "repo-agent",
        tools={"allow": [{"tool_id": "missing", "trust_level": "read_only"}]},
    )

    with pytest.raises(UnknownToolError):
        service.create(manifest)


def test_destructive_tool_requires_approval(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    service.register_tool(_tool("danger", trust=ToolTrustLevel.DESTRUCTIVE))
    manifest = make_manifest(
        "repo-agent",
        tools={"allow": [{"tool_id": "danger", "trust_level": "destructive"}]},
    )

    with pytest.raises(DestructiveToolRequiresApprovalError):
        service.create(manifest)


def test_workload_with_registered_tools_registers(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    service.register_tool(_tool("danger", trust=ToolTrustLevel.DESTRUCTIVE))
    manifest = make_manifest(
        "repo-agent",
        tools={
            "allow": [
                {"tool_id": "danger", "trust_level": "destructive", "require_approval": True}
            ]
        },
    )

    assert service.create(manifest).name == "repo-agent"


def test_triggers_are_stored_from_manifest(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    manifest = make_manifest("repo-agent", triggers=[{"type": "cron", "schedule": "0 * * * *"}])
    service.create(manifest)

    triggers = service.list_triggers("repo-agent")
    assert [trigger.trigger_id for trigger in triggers] == ["repo-agent-t1"]


def test_add_and_delete_trigger(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    service.create(make_manifest("repo-agent"))
    rule = TriggerRule(type=TriggerType.CRON, schedule="0 0 * * *")

    added = service.add_trigger("repo-agent", rule)
    assert isinstance(added, TriggerRecord)
    assert len(service.list_triggers("repo-agent")) == 1

    service.delete_trigger("repo-agent", added.trigger_id)
    assert service.list_triggers("repo-agent") == []


# --------------------------------------------------------------------------- #
# Re-certification and promotion
# --------------------------------------------------------------------------- #
def test_cert_relevant_change_blocks_promotion(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    service.create(make_manifest("repo-agent"))
    service.update(
        "repo-agent",
        make_manifest(
            "repo-agent",
            model={
                "strategy": "fixed",
                "identity": {"provider": "openai", "family": "gpt-4o", "version": "2024-08-06"},
            },
        ),
    )

    record = service.get("repo-agent")
    assert record.needs_re_certification is True
    with pytest.raises(ReCertificationRequiredError, match=r"spec\.model"):
        service.promote("repo-agent", 2)


def test_non_cert_relevant_change_allows_promotion(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    service.create(make_manifest("repo-agent"))
    service.update("repo-agent", make_manifest("repo-agent", owner="new-team"))

    promoted = service.promote("repo-agent", 2)

    assert promoted.needs_re_certification is False
    assert promoted.owner == "new-team"


def test_promotion_allowed_after_re_certification(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    service.create(make_manifest("repo-agent"))
    service.update(
        "repo-agent",
        make_manifest(
            "repo-agent",
            model={
                "strategy": "fixed",
                "identity": {"provider": "openai", "family": "gpt-4o", "version": "2024-08-06"},
            },
        ),
    )
    service.record_certification_event("repo-agent", CertificationEvent.STAGING_PASS)
    service.record_certification_event("repo-agent", CertificationEvent.PRODUCTION_PASS)

    promoted = service.promote("repo-agent", 2)

    assert promoted.needs_re_certification is False


def test_re_cert_flag_blocks_production_admission(
    service: RegistryService, make_manifest: ManifestFactory, keypair: tuple[Any, Any]
) -> None:
    private_key, _ = keypair
    service.create(
        make_manifest(
            "repo-agent",
            certification={
                "benchmark_corpus": "corpora/x/v1",
                "staging_threshold": 0.8,
                "production_threshold": 0.9,
                "status": "uncertified",
                "expires_at": "2999-01-01T00:00:00Z",
            },
        )
    )
    service.store_attestation(_attestation("repo-agent", private_key))
    service.record_certification_event("repo-agent", CertificationEvent.STAGING_PASS)
    service.record_certification_event("repo-agent", CertificationEvent.PRODUCTION_PASS)
    service.update(
        "repo-agent",
        make_manifest(
            "repo-agent",
            model={
                "strategy": "fixed",
                "identity": {"provider": "openai", "family": "gpt-4o", "version": "2024-08-06"},
            },
            certification={
                "benchmark_corpus": "corpora/x/v1",
                "staging_threshold": 0.8,
                "production_threshold": 0.9,
                "status": "uncertified",
                "expires_at": "2999-01-01T00:00:00Z",
            },
        ),
    )

    decision = service.check_admission("repo-agent", AdmissionContext.PRODUCTION)

    assert decision.admitted is False
