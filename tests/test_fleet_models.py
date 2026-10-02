"""Model validation tests for the v0.2.0 fleet-control primitives (M25-02..M25-09)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from hiveplane.core.decision import DecisionOutcome
from hiveplane.fleet import (
    AdmissionRule,
    Artifact,
    CostPeriod,
    CostPeriodKind,
    CostType,
    DedupConfig,
    DesiredSpec,
    DriftRecord,
    DriftResolution,
    EventFilter,
    GateKind,
    HandoffMapping,
    InjectionTarget,
    LintStatus,
    MeteringEvent,
    Pipeline,
    PipelineBudget,
    PipelineEdge,
    PipelineNode,
    PipelineNodeKind,
    PipelineRun,
    PipelineState,
    PolicyDecisionRecord,
    PolicyPackVersion,
    RateLimit,
    ReconcileState,
    ReconcileStatus,
    RetentionPolicy,
    Secret,
    SecretRef,
    SecretScope,
    SpecKind,
    SpecSource,
    StepGate,
    TaskTemplate,
    Trigger,
    TriggerDlqEntry,
    TriggerEvent,
    TriggerOutcome,
    TriggerRun,
    TriggerRunStatus,
    TriggerSource,
    TriggerTargetKind,
    Worker,
    WorkerHeartbeat,
    WorkerLease,
    WorkerState,
)
from hiveplane.policy.models import PolicyPackSpec

_NOW = datetime(2026, 9, 26, tzinfo=UTC)


def _trigger(**overrides: object) -> Trigger:
    fields: dict[str, object] = {
        "trigger_id": "trig-1",
        "source": TriggerSource.WEBHOOK,
        "target_kind": TriggerTargetKind.WORKLOAD,
        "target_ref": "agent-1",
        "event_filter": EventFilter(service="agent-1"),
        "task_template": TaskTemplate(mapping={"input": "payload.input"}),
        "dedup_config": DedupConfig(key_template="{{ event.id }}"),
        "created_at": _NOW,
    }
    fields.update(overrides)
    return Trigger(**fields)  # type: ignore[arg-type]


def test_trigger_defaults_to_the_default_tenant() -> None:
    assert _trigger().tenant_id == "default"


def test_trigger_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        _trigger(nope=1)


def test_cron_trigger_requires_schedule() -> None:
    with pytest.raises(ValidationError):
        _trigger(source=TriggerSource.CRON)
    assert _trigger(source=TriggerSource.CRON, schedule="0 * * * *").schedule


def test_non_cron_trigger_rejects_schedule() -> None:
    with pytest.raises(ValidationError):
        _trigger(schedule="0 * * * *")


def test_trigger_admission_rule_is_enum() -> None:
    assert _trigger(admission_rule="deny").admission_rule is AdmissionRule.DENY
    assert _trigger(rate_limit=RateLimit(max_events=1, window_seconds=60)).rate_limit


def test_trigger_event_and_run_models() -> None:
    event = TriggerEvent(
        event_id="evt-1",
        trigger_id="trig-1",
        source=TriggerSource.GITHUB,
        received_at=_NOW,
        outcome=TriggerOutcome.DEDUPLICATED,
    )
    assert event.outcome is TriggerOutcome.DEDUPLICATED
    run = TriggerRun(
        trigger_id="trig-1",
        event_id="evt-1",
        run_id="run-1",
        fired_at=_NOW,
        status=TriggerRunStatus.SUBMITTED,
    )
    assert run.run_id == "run-1"
    dlq = TriggerDlqEntry(
        entry_id="dlq-1",
        trigger_id="trig-1",
        event_id="evt-1",
        failure_reason="transport down",
        created_at=_NOW,
    )
    assert dlq.attempts == 0


def _pipeline(**overrides: object) -> Pipeline:
    fields: dict[str, object] = {
        "pipeline_id": "pipe-1",
        "name": "review",
        "version": 1,
        "nodes": [
            PipelineNode(node_id="a", kind=PipelineNodeKind.WORKLOAD, ref="agent-1"),
            PipelineNode(node_id="b", kind=PipelineNodeKind.WORKLOAD, ref="agent-2"),
            PipelineNode(
                node_id="gate",
                kind=PipelineNodeKind.GATE,
                gate=StepGate(kind=GateKind.PRODUCTION),
                depends_on=["b"],
            ),
        ],
        "edges": [
            PipelineEdge(from_node="a", to_node="b"),
        ],
        "budget": PipelineBudget(per_run_usd=1.0, total_usd=10.0),
        "created_at": _NOW,
    }
    fields.update(overrides)
    return Pipeline(**fields)  # type: ignore[arg-type]


def test_pipeline_accepts_a_valid_dag() -> None:
    assert len(_pipeline().nodes) == 3


def test_pipeline_rejects_duplicate_node_ids() -> None:
    with pytest.raises(ValidationError, match="unique"):
        _pipeline(
            nodes=[
                PipelineNode(node_id="a", kind=PipelineNodeKind.WORKLOAD, ref="agent-1"),
                PipelineNode(node_id="a", kind=PipelineNodeKind.WORKLOAD, ref="agent-2"),
            ]
        )


def test_pipeline_rejects_unknown_edge_endpoint() -> None:
    with pytest.raises(ValidationError, match="unknown node"):
        _pipeline(edges=[PipelineEdge(from_node="a", to_node="ghost")])


def test_pipeline_rejects_cycles() -> None:
    with pytest.raises(ValidationError, match="DAG"):
        _pipeline(
            nodes=[
                PipelineNode(node_id="a", kind=PipelineNodeKind.WORKLOAD, ref="x"),
                PipelineNode(
                    node_id="b", kind=PipelineNodeKind.WORKLOAD, ref="y", depends_on=["a"]
                ),
            ],
            edges=[
                PipelineEdge(from_node="a", to_node="b"),
                PipelineEdge(from_node="b", to_node="a"),
            ],
        )


def test_workload_node_requires_ref_and_gate_node_requires_gate() -> None:
    with pytest.raises(ValidationError, match="requires 'ref'"):
        PipelineNode(node_id="a", kind=PipelineNodeKind.WORKLOAD)
    with pytest.raises(ValidationError, match="requires a 'gate'"):
        PipelineNode(node_id="g", kind=PipelineNodeKind.GATE)


def test_handoff_endpoints_must_match_edge() -> None:
    with pytest.raises(ValidationError, match="handoff endpoints"):
        _pipeline(
            edges=[
                PipelineEdge(
                    from_node="a",
                    to_node="b",
                    handoff=HandoffMapping(from_node="b", to_node="a"),
                )
            ]
        )


def test_pipeline_run_links_parent_and_child() -> None:
    run = PipelineRun(
        pipeline_run_id="prun-1",
        pipeline_id="pipe-1",
        version=1,
        node_id="a",
        parent_run_id="run-1",
        child_run_id="run-2",
        state=PipelineState.RUNNING,
        started_at=_NOW,
    )
    assert run.state is PipelineState.RUNNING


def test_policy_pack_version_rejects_self_inheritance() -> None:
    pack = PolicyPackVersion(
        pack_version_id="pack-1",
        name="platform",
        version=1,
        lint_status=LintStatus.CLEAN,
        content_hash="a" * 64,
        spec=PolicyPackSpec(),
        created_at=_NOW,
    )
    assert pack.version == 1
    with pytest.raises(ValidationError, match="inherit from itself"):
        PolicyPackVersion(
            pack_version_id="pack-1",
            name="platform",
            version=2,
            parent_pack_id="pack-1",
            lint_status=LintStatus.CLEAN,
            content_hash="a" * 64,
            spec=PolicyPackSpec(),
            created_at=_NOW,
        )


def test_policy_decision_record_carries_reason_and_rule() -> None:
    decision = PolicyDecisionRecord(
        decision_id="dec-1",
        run_id="run-1",
        workload="agent-1",
        outcome=DecisionOutcome.DENY,
        reason="destructive tool",
        originating_rule_id="no-destructive",
        evaluated_at=_NOW,
    )
    assert decision.outcome is DecisionOutcome.DENY
    assert decision.originating_rule_id == "no-destructive"


def test_secret_requires_ciphertext_and_scope_ref() -> None:
    secret = Secret(
        secret_id="sec-1",
        name="api-key",
        scope=SecretScope.TEAM,
        scope_ref="platform",
        ciphertext="enc:...",
        key_id="kms-1",
        created_at=_NOW,
    )
    assert secret.scope is SecretScope.TEAM
    with pytest.raises(ValidationError, match="scope_ref"):
        Secret(
            secret_id="sec-2",
            name="api-key",
            scope=SecretScope.WORKLOAD,
            ciphertext="enc:...",
            key_id="kms-1",
            created_at=_NOW,
        )
    with pytest.raises(ValidationError):
        Secret(
            secret_id="sec-3",
            name="api-key",
            scope=SecretScope.TENANT,
            ciphertext="",
            key_id="kms-1",
            created_at=_NOW,
        )


def test_secret_ref_records_injection_target() -> None:
    ref = SecretRef(
        ref_id="ref-1",
        secret_id="sec-1",
        run_id="run-1",
        injection_target=InjectionTarget.ENV,
        target_name="API_KEY",
    )
    assert ref.injection_target is InjectionTarget.ENV


def test_worker_lease_expiry_must_follow_grant() -> None:
    lease = WorkerLease(
        lease_id="lease-1",
        run_id="run-1",
        worker_id="worker-1",
        attempt=1,
        granted_at=_NOW,
        expires_at=datetime(2026, 9, 26, 1, tzinfo=UTC),
        fencing_token=7,
    )
    assert lease.fencing_token == 7
    with pytest.raises(ValidationError, match="expires_at"):
        WorkerLease(
            lease_id="lease-2",
            run_id="run-1",
            worker_id="worker-1",
            attempt=1,
            granted_at=_NOW,
            expires_at=_NOW,
        )


def test_worker_and_heartbeat() -> None:
    worker = Worker(
        worker_id="worker-1",
        state=WorkerState.READY,
        version="0.2.0",
        registered_at=_NOW,
        last_seen_at=_NOW,
    )
    assert worker.state is WorkerState.READY
    beat = WorkerHeartbeat(
        heartbeat_id="hb-1", worker_id="worker-1", seen_at=_NOW, status=WorkerState.BUSY
    )
    assert beat.status is WorkerState.BUSY


def test_artifact_location_must_be_path_or_known_uri() -> None:
    artifact = Artifact(
        artifact_id="art-1",
        run_id="run-1",
        location="s3://bucket/key",
        size_bytes=10,
        content_hash="b" * 64,
        created_at=_NOW,
    )
    assert artifact.size_bytes == 10
    with pytest.raises(ValidationError, match="local path"):
        Artifact(
            artifact_id="art-2",
            run_id="run-1",
            location="gopher://bucket/key",
            size_bytes=1,
            content_hash="b" * 64,
            created_at=_NOW,
        )


def test_retention_policy() -> None:
    policy = RetentionPolicy(policy_id="rp-1", data_class="artifact", retain_days=30)
    assert policy.retain_days == 30


def test_metering_event_and_cost_period() -> None:
    event = MeteringEvent(
        event_id="me-1",
        team_id="platform",
        workload_id="agent-1",
        run_id="run-1",
        cost_type=CostType.LLM,
        cost_usd=0.5,
        occurred_at=_NOW,
    )
    assert event.cost_type is CostType.LLM
    period = CostPeriod(
        team_id="platform",
        workload_id="agent-1",
        period=CostPeriodKind.DAY,
        period_start=date(2026, 9, 26),
        total_cost_usd=0.5,
    )
    assert period.period is CostPeriodKind.DAY


def test_metering_event_requires_non_negative_cost() -> None:
    with pytest.raises(ValidationError):
        MeteringEvent(
            event_id="me-2",
            team_id="platform",
            workload_id="agent-1",
            cost_type=CostType.TOOL,
            cost_usd=-1.0,
            occurred_at=_NOW,
        )


def test_desired_spec_and_reconcile_state() -> None:
    spec = DesiredSpec(
        spec_id="ds-1",
        source_id="git-main",
        source=SpecSource.GIT,
        kind=SpecKind.WORKLOAD,
        name="agent-1",
        revision="abc123",
        content_hash="c" * 64,
        updated_at=_NOW,
    )
    assert spec.kind is SpecKind.WORKLOAD
    state = ReconcileState(
        source_id="git-main",
        source=SpecSource.GIT,
        last_revision="abc123",
        last_observed_hash="c" * 64,
        status=ReconcileStatus.IN_SYNC,
    )
    assert state.status is ReconcileStatus.IN_SYNC


def test_drift_record_defaults_to_unresolved() -> None:
    drift = DriftRecord(
        drift_id="dr-1",
        object_ref="agent-1",
        field="certification.production_threshold",
        desired_value=0.9,
        observed_value=0.8,
        detected_at=_NOW,
    )
    assert drift.resolution is DriftResolution.UNRESOLVED
