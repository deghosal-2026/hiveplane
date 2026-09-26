"""Full-surface Postgres store tests for registry and approvals (#118, Postgres-gated)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine

from hiveplane.certification.models import (
    Attestation,
    CertificationStatus,
    Environment,
    EvalSummary,
    Signer,
    TargetContext,
)
from hiveplane.core.approval import ApprovalRecord, ApprovalStatus
from hiveplane.core.tools import ToolTrustLevel
from hiveplane.core.triggers import TriggerMatch, TriggerRule, TriggerType
from hiveplane.policy.store import PostgresApprovalStore
from hiveplane.registry.models import ToolRecord, TriggerRecord
from hiveplane.registry.store import PostgresRegistryStore
from postgres import reset_database, seed_workload

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_ENV = Environment(
    sandbox_image="img", runtime_adapter="raw-worker", control_plane_version="0.1.0"
)


def _attestation(attestation_id: str, workload: str = "agent-1") -> Attestation:
    return Attestation(
        attestation_id=attestation_id,
        workload_id=workload,
        manifest_version=1,
        benchmark_version="1.0.0",
        benchmark_run_id="br-1",
        corpus_id="corpus",
        corpus_version=1,
        model_identity="gpt-4o-2024-08-06",
        status=CertificationStatus.CERTIFIED,
        target_context=TargetContext.PRODUCTION,
        eval_summary=EvalSummary(
            pass_rate=0.95,
            critical_failures=0,
            p95_latency_ms=1000,
            tasks_passed=19,
            tasks_failed=1,
        ),
        timestamp=_NOW,
        environment=_ENV,
        signer=Signer(identity="cert@hiveplane", key_id="key-1", signature="sig"),
    )


def test_registry_tools_and_triggers_round_trip(pg_engine: Engine) -> None:
    store = PostgresRegistryStore(pg_engine)
    reset_database(pg_engine)
    seed_workload(pg_engine)
    store.save_tool(
        ToolRecord(
            tool_id="github.read",
            name="GitHub read",
            mcp_server="github",
            trust_level=ToolTrustLevel.READ_ONLY,
            registered_at=_NOW,
            registered_by="operator",
        )
    )
    assert store.get_tool("github.read") is not None
    assert [tool.tool_id for tool in store.list_tools()] == ["github.read"]
    assert store.get_tool("missing") is None

    trigger = TriggerRecord(
        trigger_id="agent-1-t1",
        workload="agent-1",
        rule=TriggerRule(
            type=TriggerType.WEBHOOK,
            url="https://example.test/hook",
            match=TriggerMatch(service="agent-1"),
        ),
        created_at=_NOW,
    )
    store.add_trigger(trigger)
    store.add_trigger(trigger)
    assert [item.trigger_id for item in store.list_triggers("agent-1")] == ["agent-1-t1"]
    assert store.delete_trigger("other", "agent-1-t1") is False
    assert store.delete_trigger("agent-1", "agent-1-t1") is True
    assert store.list_triggers("agent-1") == []


def test_registry_attestations_round_trip(pg_engine: Engine) -> None:
    store = PostgresRegistryStore(pg_engine)
    reset_database(pg_engine)
    seed_workload(pg_engine)
    store.add_attestation(_attestation("att-1"))

    fetched = store.get_attestation("att-1")
    assert fetched is not None
    assert fetched.workload_id == "agent-1"
    assert [item.attestation_id for item in store.list_attestations("agent-1")] == ["att-1"]
    assert store.get_attestation("missing") is None


def test_approval_list_filters(pg_engine: Engine) -> None:
    store = PostgresApprovalStore(pg_engine)
    reset_database(pg_engine)
    seed_workload(pg_engine)
    store.save(
        ApprovalRecord(
            approval_id="appr-1",
            run_id="run-1",
            workload="agent-1",
            rule="r",
            reason="x",
            requested_at=_NOW,
        )
    )
    store.save(
        ApprovalRecord(
            approval_id="appr-2",
            run_id="run-2",
            workload="agent-2",
            rule="r",
            reason="x",
            requested_at=_NOW,
            status=ApprovalStatus.APPROVED,
        )
    )
    assert [r.approval_id for r in store.list_approvals(status=ApprovalStatus.PENDING)] == [
        "appr-1"
    ]
    assert [r.approval_id for r in store.list_approvals(workload="agent-2")] == ["appr-2"]
    assert store.list_approvals(run_id="run-9") == []
    assert store.get("missing") is None
