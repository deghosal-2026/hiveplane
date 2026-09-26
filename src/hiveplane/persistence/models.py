"""ORM models for the HivePlane system of record (M18, D7).

Typed columns carry the fields queried for fleet/budget/audit views; a JSONB
``payload`` on each row stores the exact pydantic domain record so round-trips
are lossless. ``run_admissions`` is an implementation addition needed by
``RunStore`` (admissions are absent from the design's entity list).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from hiveplane.persistence.base import Base

_PAYLOAD = JSONB


class _TenantScoped:
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class _Attributed:
    team_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    attribution_key: Mapped[str | None] = mapped_column(String(253), nullable=True)


class WorkloadRow(_TenantScoped, Base):
    """Current manifest and certification status for a workload."""

    __tablename__ = "workloads"
    __table_args__ = (UniqueConstraint("name", "tenant_id", name="uq_workloads_name_tenant"),)

    name: Mapped[str] = mapped_column(String(253), primary_key=True)
    owner: Mapped[str] = mapped_column(String(253))
    team: Mapped[str | None] = mapped_column(String(253), nullable=True)
    certification_status: Mapped[str] = mapped_column(String(32), index=True)
    current_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class WorkloadVersionRow(_TenantScoped, Base):
    """Append-only manifest version history."""

    __tablename__ = "workload_versions"
    __table_args__ = (
        UniqueConstraint(
            "workload", "tenant_id", "version", name="uq_workload_versions"
        ),
        ForeignKeyConstraint(
            ["workload", "tenant_id"],
            ["workloads.name", "workloads.tenant_id"],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workload: Mapped[str] = mapped_column(String(253))
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class RunRow(_Attributed, _TenantScoped, Base):
    """A single run and its current state."""

    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_workload_state_created", "workload_id", "state", "created_at"),
        UniqueConstraint("id", "tenant_id", name="uq_runs_id_tenant"),
        ForeignKeyConstraint(
            ["workload_id", "tenant_id"],
            ["workloads.name", "workloads.tenant_id"],
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workload_id: Mapped[str] = mapped_column(String(253), index=True)
    caller: Mapped[str] = mapped_column(String(253))
    state: Mapped[str] = mapped_column(String(32), index=True)
    context: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_identity: Mapped[str | None] = mapped_column(String(253), nullable=True)
    sandbox: Mapped[bool] = mapped_column(Boolean, default=False)
    sandbox_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class RunEventRow(_TenantScoped, Base):
    """Append-only run transition log."""

    __tablename__ = "run_events"
    __table_args__ = (
        Index("ix_run_events_run_timestamp", "run_id", "timestamp"),
        ForeignKeyConstraint(
            ["run_id", "tenant_id"], ["runs.id", "runs.tenant_id"], ondelete="CASCADE"
        ),
    )

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    actor: Mapped[str] = mapped_column(String(253))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class UsageEventRow(_Attributed, _TenantScoped, Base):
    """Token/tool usage for budget and cost attribution."""

    __tablename__ = "usage_events"
    __table_args__ = (
        Index("ix_usage_events_run_timestamp", "run_id", "timestamp"),
        Index("ix_usage_events_model_timestamp", "model_identity", "timestamp"),
        ForeignKeyConstraint(
            ["run_id", "tenant_id"], ["runs.id", "runs.tenant_id"], ondelete="CASCADE"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64))
    model_identity: Mapped[str | None] = mapped_column(String(253), nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class RunAdmissionRow(_TenantScoped, Base):
    """The admission decision recorded for a run."""

    __tablename__ = "run_admissions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "tenant_id"], ["runs.id", "runs.tenant_id"], ondelete="CASCADE"
        ),
    )

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    outcome: Mapped[str] = mapped_column(String(32))
    context: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class AuditRow(_TenantScoped, Base):
    """Tamper-evident operator/policy audit records (chained hash)."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_subject", "subject"),
        Index("ix_audit_log_created", "created_at"),
    )

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(253))
    action: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(253))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class ApprovalRow(_TenantScoped, Base):
    """Pending and resolved approval requests."""

    __tablename__ = "approvals"

    approval_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class CertificationRow(_TenantScoped, Base):
    """Certification pipeline records."""

    __tablename__ = "certifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workload", "tenant_id"],
            ["workloads.name", "workloads.tenant_id"],
        ),
    )

    certification_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workload: Mapped[str] = mapped_column(String(253), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class AttestationRow(_TenantScoped, Base):
    """Immutable signed attestations."""

    __tablename__ = "attestations"
    __table_args__ = (
        Index("ix_attestations_workload_created", "workload", "created_at"),
        ForeignKeyConstraint(
            ["workload", "tenant_id"],
            ["workloads.name", "workloads.tenant_id"],
        ),
    )

    attestation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workload: Mapped[str] = mapped_column(String(253))
    model_identity: Mapped[str] = mapped_column(String(253))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class AttestationLogRow(Base):
    """Append-only, hash-chained certification transparency log (M35-01).

    Global (not tenant-scoped): the log is the canonical public record and a
    single chain across all tenants.
    """

    __tablename__ = "attestation_log"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    attestation_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    prev_hash: Mapped[str] = mapped_column(String(64))
    entry_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class SigningKeyRow(Base):
    """Distributed public verification keys with rotation state (M35-06).

    Global (not tenant-scoped): the key registry is control-plane material.
    """

    __tablename__ = "signing_keys"

    key_id: Mapped[str] = mapped_column(String(253), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class RunFeedbackRow(_TenantScoped, Base):
    """Operator feedback on terminal production runs (M36-01)."""

    __tablename__ = "run_feedback"
    __table_args__ = (
        Index("ix_run_feedback_workload_created", "workload_id", "created_at"),
    )

    feedback_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    workload_id: Mapped[str] = mapped_column(String(253), index=True)
    verdict: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class CandidateRow(_TenantScoped, Base):
    """Proposed corpus cases from production feedback (M36-02)."""

    __tablename__ = "corpus_candidates"
    __table_args__ = (
        Index("ix_corpus_candidates_workload_status", "workload_id", "status"),
    )

    candidate_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_run_id: Mapped[str] = mapped_column(String(64), index=True)
    workload_id: Mapped[str] = mapped_column(String(253), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class CandidateReviewRow(_TenantScoped, Base):
    """Human review decisions on corpus candidates (M36-03)."""

    __tablename__ = "candidate_reviews"
    __table_args__ = (
        Index("ix_candidate_reviews_candidate", "candidate_id", "reviewed_at"),
    )

    review_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(64), index=True)
    decision: Mapped[str] = mapped_column(String(32), index=True)
    reviewer: Mapped[str] = mapped_column(String(253))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class CorpusVersionRow(_TenantScoped, Base):
    """Immutable cuts of a corpus version (M36-04)."""

    __tablename__ = "corpus_versions"
    __table_args__ = (
        Index("ix_corpus_versions_corpus_version", "corpus_id", "version"),
    )

    corpus_version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    corpus_id: Mapped[str] = mapped_column(String(253), index=True)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class EvalSampleRow(_TenantScoped, Base):
    """Production runs selected for online evaluation (M36-05)."""

    __tablename__ = "eval_samples"
    __table_args__ = (
        Index("ix_eval_samples_workload_sampled", "workload_id", "sampled_at"),
    )

    sample_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    workload_id: Mapped[str] = mapped_column(String(253), index=True)
    rubric_version: Mapped[int] = mapped_column(Integer)
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class JudgeScoreRow(_TenantScoped, Base):
    """Recorded judge scores for eval samples (M36-06)."""

    __tablename__ = "judge_scores"
    __table_args__ = (
        Index("ix_judge_scores_sample", "sample_id", "created_at"),
    )

    score_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sample_id: Mapped[str] = mapped_column(String(64), index=True)
    rubric_version: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class RubricRow(Base):
    """Versioned, immutable judge rubrics (global control-plane material)."""

    __tablename__ = "rubrics"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_rubrics_name_version"),)

    rubric_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(253), index=True)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class ToolRow(_TenantScoped, Base):
    """MCP tool registry entries."""

    __tablename__ = "tools"

    tool_id: Mapped[str] = mapped_column(String(253), primary_key=True)
    mcp_server: Mapped[str] = mapped_column(String(253), index=True)
    trust_level: Mapped[str] = mapped_column(String(32))
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class TriggerRuleRow(_TenantScoped, Base):
    """Trigger rules per workload."""

    __tablename__ = "trigger_rules"
    __table_args__ = (
        Index("ix_trigger_rules_workload_type", "workload", "type"),
        ForeignKeyConstraint(
            ["workload", "tenant_id"],
            ["workloads.name", "workloads.tenant_id"],
        ),
    )

    trigger_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workload: Mapped[str] = mapped_column(String(253))
    type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class DriftScheduleRow(_TenantScoped, Base):
    """Re-certification schedules."""

    __tablename__ = "drift_schedules"
    __table_args__ = (
        Index("ix_drift_schedules_next_run", "next_re_cert_run"),
        ForeignKeyConstraint(
            ["workload", "tenant_id"],
            ["workloads.name", "workloads.tenant_id"],
        ),
    )

    schedule_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workload: Mapped[str] = mapped_column(String(253), index=True)
    next_re_cert_run: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class QuarantineRow(_TenantScoped, Base):
    """Persisted drift/operator quarantines with reason and history (M34-05)."""

    __tablename__ = "quarantines"
    __table_args__ = (
        Index("ix_quarantines_workload_status", "workload", "status"),
        ForeignKeyConstraint(
            ["workload", "tenant_id"],
            ["workloads.name", "workloads.tenant_id"],
        ),
    )

    quarantine_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workload: Mapped[str] = mapped_column(String(253), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    actor: Mapped[str] = mapped_column(String(253))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class DriftAssessmentRow(_TenantScoped, Base):
    """Recorded drift assessments used for trend/false-positive controls (M34-02)."""

    __tablename__ = "drift_assessments"
    __table_args__ = (
        Index("ix_drift_assessments_workload_created", "workload", "created_at"),
        ForeignKeyConstraint(
            ["workload", "tenant_id"],
            ["workloads.name", "workloads.tenant_id"],
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workload: Mapped[str] = mapped_column(String(253), index=True)
    verdict: Mapped[str] = mapped_column(String(32), index=True)
    exceeded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class SecurityEventRow(_TenantScoped, Base):
    """Append-only defense telemetry: injection, egress, taint, escalation (M39-06)."""

    __tablename__ = "security_events"
    __table_args__ = (
        Index("ix_security_events_workload_created", "workload_id", "created_at"),
        Index("ix_security_events_run", "run_id"),
    )

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    workload_id: Mapped[str | None] = mapped_column(String(253), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class FanOutDeliveryRow(_TenantScoped, Base):
    """Result fan-out delivery attempts."""

    __tablename__ = "fan_out_deliveries"
    __table_args__ = (
        Index("ix_fan_out_deliveries_run_status", "run_id", "status"),
        ForeignKeyConstraint(
            ["run_id", "tenant_id"], ["runs.id", "runs.tenant_id"], ondelete="CASCADE"
        ),
    )

    delivery_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64))
    destination_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class HealthSignalRow(_TenantScoped, Base):
    """Agent health signals."""

    __tablename__ = "health_signals"
    __table_args__ = (
        Index("ix_health_signals_workload_updated", "workload", "last_updated"),
        ForeignKeyConstraint(
            ["workload", "tenant_id"],
            ["workloads.name", "workloads.tenant_id"],
        ),
    )

    signal_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workload: Mapped[str] = mapped_column(String(253))
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class CostAttributionRow(_Attributed, _TenantScoped, Base):
    """Cost showback records."""

    __tablename__ = "cost_attributions"
    __table_args__ = (
        Index("ix_cost_attributions_team_period", "team", "period", "workload"),
        ForeignKeyConstraint(
            ["workload", "tenant_id"],
            ["workloads.name", "workloads.tenant_id"],
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team: Mapped[str | None] = mapped_column(String(253), nullable=True)
    workload: Mapped[str] = mapped_column(String(253))
    period: Mapped[str] = mapped_column(String(32))
    total_spend_usd: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class BudgetRunSpendRow(_Attributed, _TenantScoped, Base):
    """Durable per-run spend total for budget enforcement (#128)."""

    __tablename__ = "budget_run_spend"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    amount_usd: Mapped[float] = mapped_column(Float)


class BudgetDaySpendRow(_Attributed, _TenantScoped, Base):
    """Durable per-workload, per-day spend total for budget enforcement (#128)."""

    __tablename__ = "budget_day_spend"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workload: Mapped[str] = mapped_column(String(253), primary_key=True)
    day: Mapped[str] = mapped_column(String(32), primary_key=True)
    amount_usd: Mapped[float] = mapped_column(Float)


class BudgetTeamSpendRow(_Attributed, _TenantScoped, Base):
    """Durable per-team, per-day spend total for budget enforcement (#128)."""

    __tablename__ = "budget_team_spend"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    team: Mapped[str] = mapped_column(String(253), primary_key=True)
    day: Mapped[str] = mapped_column(String(32), primary_key=True)
    amount_usd: Mapped[float] = mapped_column(Float)


class TenantRow(Base):
    """Isolation-boundary tenant records (#149)."""

    __tablename__ = "tenants"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_tenants_tenant_name"),)

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(253))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class TeamRow(Base):
    """Attribution/policy teams inside a tenant (#149)."""

    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_teams_tenant_name"),
        UniqueConstraint("tenant_id", "attribution_key", name="uq_teams_tenant_attribution"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(253))
    attribution_key: Mapped[str] = mapped_column(String(253))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class MembershipRow(Base):
    """Operator-to-role bindings inside a tenant (#149)."""

    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "operator_id", "team_id", name="uq_memberships_tenant_operator_team"
        ),
        Index(
            "uq_memberships_tenant_operator_no_team",
            "tenant_id",
            "operator_id",
            unique=True,
            postgresql_where=text("team_id IS NULL"),
        ),
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "team_id"],
            ["teams.tenant_id", "teams.team_id"],
            ondelete="CASCADE",
        ),
    )

    membership_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    team_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operator_id: Mapped[str] = mapped_column(String(253))
    role: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


# --- Fleet control primitives (M25-02..M25-09, D21) -------------------------


class TriggerRow(_TenantScoped, Base):
    """Declared triggers for workloads and pipelines (M25-02)."""

    __tablename__ = "triggers"
    __table_args__ = (UniqueConstraint("trigger_id", "tenant_id", name="uq_triggers_id_tenant"),)

    trigger_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    target_kind: Mapped[str] = mapped_column(String(32))
    target_ref: Mapped[str] = mapped_column(String(253), index=True)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=0)
    admission_rule: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class TriggerEventRow(_TenantScoped, Base):
    """Append-only received trigger events (M25-02)."""

    __tablename__ = "trigger_events"
    __table_args__ = (
        Index("ix_trigger_events_trigger_received", "trigger_id", "received_at"),
        ForeignKeyConstraint(
            ["trigger_id", "tenant_id"],
            ["triggers.trigger_id", "triggers.tenant_id"],
            ondelete="CASCADE",
        ),
    )

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    trigger_id: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(32))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dedup_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class TriggerRunRow(_TenantScoped, Base):
    """Trigger-to-run linkage and decision (M25-02)."""

    __tablename__ = "trigger_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["trigger_id", "tenant_id"],
            ["triggers.trigger_id", "triggers.tenant_id"],
            ondelete="CASCADE",
        ),
    )

    trigger_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), index=True)
    reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class TriggerDlqRow(_TenantScoped, Base):
    """Dead-lettered trigger events awaiting replay (M25-02)."""

    __tablename__ = "trigger_dlq"
    __table_args__ = (
        ForeignKeyConstraint(
            ["trigger_id", "tenant_id"],
            ["triggers.trigger_id", "triggers.tenant_id"],
            ondelete="CASCADE",
        ),
    )

    entry_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    trigger_id: Mapped[str] = mapped_column(String(64), index=True)
    event_id: Mapped[str] = mapped_column(String(128))
    failure_reason: Mapped[str] = mapped_column(String(2000))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class TriggerNonceRow(_TenantScoped, Base):
    """Seen webhook nonces for replay protection (M27-02)."""

    __tablename__ = "trigger_nonces"
    __table_args__ = (Index("ix_trigger_nonces_seen", "trigger_id", "seen_at"),)

    trigger_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(256), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class TriggerFreezeRow(_TenantScoped, Base):
    """Declared maintenance/freeze windows (M28-05)."""

    __tablename__ = "trigger_freezes"
    __table_args__ = (Index("ix_trigger_freezes_scope", "scope", "scope_ref"),)

    freeze_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope: Mapped[str] = mapped_column(String(16))
    scope_ref: Mapped[str | None] = mapped_column(String(253), nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    drain: Mapped[str] = mapped_column(String(16))
    declared_by: Mapped[str] = mapped_column(String(253))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class PipelineRow(_TenantScoped, Base):
    """Versioned pipeline DAGs (M25-03)."""

    __tablename__ = "pipelines"
    __table_args__ = (
        UniqueConstraint("pipeline_id", "tenant_id", name="uq_pipelines_id_tenant"),
    )

    pipeline_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(253), index=True)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class PipelineRunRow(_TenantScoped, Base):
    """Pipeline-run linkage to parent and child runs (M25-03)."""

    __tablename__ = "pipeline_runs"
    __table_args__ = (
        Index("ix_pipeline_runs_pipeline_state", "pipeline_id", "state"),
        ForeignKeyConstraint(
            ["pipeline_id", "tenant_id"],
            ["pipelines.pipeline_id", "pipelines.tenant_id"],
        ),
    )

    pipeline_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pipeline_id: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    node_id: Mapped[str] = mapped_column(String(128))
    parent_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    child_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    state: Mapped[str] = mapped_column(String(32), index=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class PipelineRunHeaderRow(_TenantScoped, Base):
    """The parent record of one pipeline execution (M29-02)."""

    __tablename__ = "pipeline_run_headers"
    __table_args__ = (
        Index("ix_pipeline_run_headers_pipeline_state", "pipeline_id", "state"),
    )

    pipeline_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pipeline_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(32), index=True)
    budget_usd: Mapped[float] = mapped_column(Float, default=0.0)
    spent_usd: Mapped[float] = mapped_column(Float, default=0.0)
    parent_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class PipelineNodeRunRow(_TenantScoped, Base):
    """One node execution within a pipeline run (M29-02)."""

    __tablename__ = "pipeline_node_runs"
    __table_args__ = (
        Index("ix_pipeline_node_runs_run_node", "pipeline_run_id", "node_id"),
    )

    pipeline_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    node_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    attempt: Mapped[int] = mapped_column(Integer, primary_key=True)
    child_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class RouterDecisionRow(_TenantScoped, Base):
    """A recorded smart-router decision (M30-03)."""

    __tablename__ = "router_decisions"

    decision_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_hash: Mapped[str] = mapped_column(String(64), index=True)
    outcome: Mapped[str] = mapped_column(String(32), index=True)
    chosen: Mapped[str | None] = mapped_column(String(253), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class AgentToolInvocationRow(_TenantScoped, Base):
    """A nested agent-as-tool invocation (M30-04..M30-06)."""

    __tablename__ = "agent_tool_invocations"

    invocation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    caller_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    nested_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    workload: Mapped[str] = mapped_column(String(253), index=True)
    depth: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class PromotionRow(_TenantScoped, Base):
    """A recorded promotion request and its outcome (M32-03)."""

    __tablename__ = "promotions"

    promotion_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workload: Mapped[str] = mapped_column(String(253), index=True)
    manifest_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    to_context: Mapped[str] = mapped_column(String(32))
    certification_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class PolicyPackVersionRow(_TenantScoped, Base):
    """Versioned, inheritable policy packs (M25-04)."""

    __tablename__ = "policy_pack_versions"
    __table_args__ = (
        UniqueConstraint(
            "pack_version_id", "tenant_id", name="uq_policy_pack_versions_id_tenant"
        ),
        UniqueConstraint(
            "tenant_id", "name", "version", name="uq_policy_pack_versions_name_version"
        ),
        ForeignKeyConstraint(
            ["parent_pack_id", "tenant_id"],
            ["policy_pack_versions.pack_version_id", "policy_pack_versions.tenant_id"],
        ),
    )

    pack_version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(253), index=True)
    version: Mapped[int] = mapped_column(Integer)
    parent_pack_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lint_status: Mapped[str] = mapped_column(String(32), index=True)
    content_hash: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class PolicyDecisionRow(_TenantScoped, Base):
    """Append-only policy decisions with their reason and rule (M25-04)."""

    __tablename__ = "policy_decisions"
    __table_args__ = (
        ForeignKeyConstraint(["run_id", "tenant_id"], ["runs.id", "runs.tenant_id"]),
    )

    decision_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    workload: Mapped[str] = mapped_column(String(253), index=True)
    outcome: Mapped[str] = mapped_column(String(32), index=True)
    reason: Mapped[str] = mapped_column(String(2000))
    originating_rule_id: Mapped[str | None] = mapped_column(String(253), nullable=True)
    action_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    policy_pack_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class SecretRow(_TenantScoped, Base):
    """Ciphertext-at-rest secrets with rotation metadata (M25-05)."""

    __tablename__ = "secrets"
    __table_args__ = (
        UniqueConstraint("secret_id", "tenant_id", name="uq_secrets_id_tenant"),
        UniqueConstraint("tenant_id", "name", name="uq_secrets_tenant_name"),
    )

    secret_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(253), index=True)
    scope: Mapped[str] = mapped_column(String(32))
    scope_ref: Mapped[str | None] = mapped_column(String(253), nullable=True)
    ciphertext: Mapped[str] = mapped_column(String)
    key_id: Mapped[str] = mapped_column(String(253))
    next_rotation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class SecretRefRow(_TenantScoped, Base):
    """Where a secret was injected (audit trail) (M25-05)."""

    __tablename__ = "secret_refs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["secret_id", "tenant_id"],
            ["secrets.secret_id", "secrets.tenant_id"],
            ondelete="CASCADE",
        ),
    )

    ref_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    secret_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    injection_target: Mapped[str] = mapped_column(String(32))
    target_name: Mapped[str] = mapped_column(String(253))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class WorkerRow(_TenantScoped, Base):
    """Registered execution workers (M25-06)."""

    __tablename__ = "workers"
    __table_args__ = (
        UniqueConstraint("worker_id", "tenant_id", name="uq_workers_id_tenant"),
    )

    worker_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[str] = mapped_column(String(64))
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class WorkerLeaseRow(_TenantScoped, Base):
    """Current run lease held by a worker (M25-06)."""

    __tablename__ = "worker_leases"
    __table_args__ = (
        Index("ix_worker_leases_worker_expires", "worker_id", "expires_at"),
        ForeignKeyConstraint(
            ["worker_id", "tenant_id"],
            ["workers.worker_id", "workers.tenant_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(["run_id", "tenant_id"], ["runs.id", "runs.tenant_id"]),
    )

    lease_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    worker_id: Mapped[str] = mapped_column(String(64), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fencing_token: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class WorkerHeartbeatRow(_TenantScoped, Base):
    """Bounded rolling worker liveness signals (M25-06)."""

    __tablename__ = "worker_heartbeats"
    __table_args__ = (
        Index("ix_worker_heartbeats_worker_seen", "worker_id", "seen_at"),
        ForeignKeyConstraint(
            ["worker_id", "tenant_id"],
            ["workers.worker_id", "workers.tenant_id"],
            ondelete="CASCADE",
        ),
    )

    heartbeat_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    worker_id: Mapped[str] = mapped_column(String(64), index=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class RetentionPolicyRow(_TenantScoped, Base):
    """Per-tenant artifact retention rules (M25-07)."""

    __tablename__ = "retention_policies"
    __table_args__ = (
        UniqueConstraint("policy_id", "tenant_id", name="uq_retention_policies_id_tenant"),
    )

    policy_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    data_class: Mapped[str] = mapped_column(String(128), index=True)
    retain_days: Mapped[int] = mapped_column(Integer)
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class ArtifactRow(_TenantScoped, Base):
    """Metadata for stored execution artifacts (M25-07)."""

    __tablename__ = "artifacts"
    __table_args__ = (
        Index("ix_artifacts_run_created", "run_id", "created_at"),
        ForeignKeyConstraint(["run_id", "tenant_id"], ["runs.id", "runs.tenant_id"]),
        ForeignKeyConstraint(
            ["retention_policy_id", "tenant_id"],
            ["retention_policies.policy_id", "retention_policies.tenant_id"],
        ),
    )

    artifact_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    location: Mapped[str] = mapped_column(String(2048))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    content_hash: Mapped[str] = mapped_column(String(128))
    retention_policy_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class MeteringEventRow(_TenantScoped, Base):
    """Append-only attributed usage facts (M25-08)."""

    __tablename__ = "metering_events"
    __table_args__ = (
        Index("ix_metering_events_tenant_occurred", "tenant_id", "occurred_at"),
        Index("ix_metering_events_workload_occurred", "workload_id", "occurred_at"),
    )

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(64), index=True)
    workload_id: Mapped[str] = mapped_column(String(253), index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    pipeline_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cost_type: Mapped[str] = mapped_column(String(32), index=True)
    model: Mapped[str | None] = mapped_column(String(253), nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float)
    saved_usd: Mapped[float] = mapped_column(Float, default=0.0)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class CostPeriodRow(Base):
    """Materialized day/week/month cost buckets (M25-08)."""

    __tablename__ = "cost_periods"
    __table_args__ = (Index("ix_cost_periods_scope", "tenant_id", "team_id", "workload_id"),)

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workload_id: Mapped[str] = mapped_column(String(253), primary_key=True)
    period: Mapped[str] = mapped_column(String(16), primary_key=True)
    period_start: Mapped[date] = mapped_column(Date, primary_key=True)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cost_per_completed_task: Mapped[float] = mapped_column(Float, default=0.0)
    cache_savings_usd: Mapped[float] = mapped_column(Float, default=0.0)
    carry_in_usd: Mapped[float] = mapped_column(Float, default=0.0)
    roi_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class DesiredSpecRow(_TenantScoped, Base):
    """Declared fleet objects from a desired-state source (M25-09)."""

    __tablename__ = "desired_specs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "source_id", "kind", "name", name="uq_desired_specs_scope"
        ),
    )

    spec_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), index=True)
    source: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(253))
    revision: Mapped[str] = mapped_column(String(128))
    content_hash: Mapped[str] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class ReconcileStateRow(Base):
    """Last-observed convergence state per source (M25-09)."""

    __tablename__ = "reconcile_state"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source: Mapped[str] = mapped_column(String(16))
    last_revision: Mapped[str] = mapped_column(String(128))
    last_observed_hash: Mapped[str] = mapped_column(String(128))
    last_reconcile_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class DriftRecordRow(_TenantScoped, Base):
    """Field-level declared-vs-observed differences (M25-09)."""

    __tablename__ = "drift_records"
    __table_args__ = (Index("ix_drift_records_object", "object_ref"),)

    drift_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    object_ref: Mapped[str] = mapped_column(String(253))
    field: Mapped[str] = mapped_column(String(253))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str] = mapped_column(String(32), index=True)
    reconcile_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class ReconcileRunRow(_TenantScoped, Base):
    """Append-only reconcile pass history per source (M26-04)."""

    __tablename__ = "reconcile_runs"
    __table_args__ = (Index("ix_reconcile_runs_source", "source_id", "started_at"),)

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), index=True)
    source: Mapped[str] = mapped_column(String(16))
    revision: Mapped[str] = mapped_column(String(128))
    mode: Mapped[str] = mapped_column(String(16))
    outcome: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)
