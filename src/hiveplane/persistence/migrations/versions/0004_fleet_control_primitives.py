"""fleet control primitives

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-26

Creates the v0.2.0 fleet-control tables (triggers, pipelines, policy packs and
decisions, secrets, workers, artifacts, metering/cost periods, and desired-state
reconciliation). Defensive and idempotent: ``0001`` builds the full schema from
current metadata, so on a fresh database every step is a no-op.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

# Parent tables before their children (composite foreign keys).
_TABLES = (
    "triggers",
    "trigger_events",
    "trigger_runs",
    "trigger_dlq",
    "pipelines",
    "pipeline_runs",
    "policy_pack_versions",
    "policy_decisions",
    "secrets",
    "secret_refs",
    "workers",
    "worker_leases",
    "worker_heartbeats",
    "retention_policies",
    "artifacts",
    "metering_events",
    "cost_periods",
    "desired_specs",
    "reconcile_state",
    "drift_records",
)


def upgrade() -> None:
    """Create the fleet-control tables (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the fleet-control tables."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
