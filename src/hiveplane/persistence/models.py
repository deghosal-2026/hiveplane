"""ORM models for the HivePlane system of record (M18, D7).

Typed columns carry the fields queried for fleet/budget/audit views; a JSONB
``payload`` on each row stores the exact pydantic domain record so round-trips
are lossless. ``run_admissions`` is an implementation addition needed by
``RunStore`` (admissions are absent from the design's entity list).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from hiveplane.persistence.base import Base

_PAYLOAD = JSONB


class WorkloadRow(Base):
    """Current manifest and certification status for a workload."""

    __tablename__ = "workloads"

    name: Mapped[str] = mapped_column(String(253), primary_key=True)
    owner: Mapped[str] = mapped_column(String(253))
    team: Mapped[str | None] = mapped_column(String(253), nullable=True)
    certification_status: Mapped[str] = mapped_column(String(32), index=True)
    current_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class WorkloadVersionRow(Base):
    """Append-only manifest version history."""

    __tablename__ = "workload_versions"
    __table_args__ = (UniqueConstraint("workload", "version", name="uq_workload_versions"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workload: Mapped[str] = mapped_column(ForeignKey("workloads.name", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class RunRow(Base):
    """A single run and its current state."""

    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_workload_state_created", "workload_id", "state", "created_at"),
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


class RunEventRow(Base):
    """Append-only run transition log."""

    __tablename__ = "run_events"
    __table_args__ = (Index("ix_run_events_run_timestamp", "run_id", "timestamp"),)

    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    actor: Mapped[str] = mapped_column(String(253))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class UsageEventRow(Base):
    """Token/tool usage for budget and cost attribution."""

    __tablename__ = "usage_events"
    __table_args__ = (
        Index("ix_usage_events_run_timestamp", "run_id", "timestamp"),
        Index("ix_usage_events_model_timestamp", "model_identity", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    model_identity: Mapped[str | None] = mapped_column(String(253), nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class RunAdmissionRow(Base):
    """The admission decision recorded for a run."""

    __tablename__ = "run_admissions"

    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    outcome: Mapped[str] = mapped_column(String(32))
    context: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class AuditRow(Base):
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


class ApprovalRow(Base):
    """Pending and resolved approval requests."""

    __tablename__ = "approvals"

    approval_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class CertificationRow(Base):
    """Certification pipeline records."""

    __tablename__ = "certifications"

    certification_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workload: Mapped[str] = mapped_column(String(253), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class AttestationRow(Base):
    """Immutable signed attestations."""

    __tablename__ = "attestations"
    __table_args__ = (Index("ix_attestations_workload_created", "workload", "created_at"),)

    attestation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workload: Mapped[str] = mapped_column(String(253))
    model_identity: Mapped[str] = mapped_column(String(253))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class ToolRow(Base):
    """MCP tool registry entries."""

    __tablename__ = "tools"

    tool_id: Mapped[str] = mapped_column(String(253), primary_key=True)
    mcp_server: Mapped[str] = mapped_column(String(253), index=True)
    trust_level: Mapped[str] = mapped_column(String(32))
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class TriggerRuleRow(Base):
    """Trigger rules per workload."""

    __tablename__ = "trigger_rules"
    __table_args__ = (Index("ix_trigger_rules_workload_type", "workload", "type"),)

    trigger_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workload: Mapped[str] = mapped_column(String(253))
    type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class DriftScheduleRow(Base):
    """Re-certification schedules."""

    __tablename__ = "drift_schedules"
    __table_args__ = (Index("ix_drift_schedules_next_run", "next_re_cert_run"),)

    schedule_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workload: Mapped[str] = mapped_column(String(253), index=True)
    next_re_cert_run: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class FanOutDeliveryRow(Base):
    """Result fan-out delivery attempts."""

    __tablename__ = "fan_out_deliveries"
    __table_args__ = (Index("ix_fan_out_deliveries_run_status", "run_id", "status"),)

    delivery_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64))
    destination_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class HealthSignalRow(Base):
    """Agent health signals."""

    __tablename__ = "health_signals"
    __table_args__ = (Index("ix_health_signals_workload_updated", "workload", "last_updated"),)

    signal_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workload: Mapped[str] = mapped_column(String(253))
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class CostAttributionRow(Base):
    """Cost showback records."""

    __tablename__ = "cost_attributions"
    __table_args__ = (Index("ix_cost_attributions_team_period", "team", "period", "workload"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team: Mapped[str | None] = mapped_column(String(253), nullable=True)
    workload: Mapped[str] = mapped_column(String(253))
    period: Mapped[str] = mapped_column(String(32))
    total_spend_usd: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)


class BudgetRunSpendRow(Base):
    """Durable per-run spend total for budget enforcement (#128)."""

    __tablename__ = "budget_run_spend"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    amount_usd: Mapped[float] = mapped_column(Float)


class BudgetDaySpendRow(Base):
    """Durable per-workload, per-day spend total for budget enforcement (#128)."""

    __tablename__ = "budget_day_spend"

    workload: Mapped[str] = mapped_column(String(253), primary_key=True)
    day: Mapped[str] = mapped_column(String(32), primary_key=True)
    amount_usd: Mapped[float] = mapped_column(Float)


class BudgetTeamSpendRow(Base):
    """Durable per-team, per-day spend total for budget enforcement (#128)."""

    __tablename__ = "budget_team_spend"

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
        UniqueConstraint("team_id", "tenant_id", name="uq_teams_team_tenant"),
        UniqueConstraint("tenant_id", "name", name="uq_teams_tenant_name"),
        UniqueConstraint("tenant_id", "attribution_key", name="uq_teams_tenant_attribution"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
    )

    team_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
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
        ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
    )

    membership_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    team_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operator_id: Mapped[str] = mapped_column(String(253))
    role: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, object]] = mapped_column(_PAYLOAD)
