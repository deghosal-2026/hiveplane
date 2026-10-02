"""reporting, compliance, and purge

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-27

Adds ``report_runs``, ``report_schedules``, ``audit_exports``, ``evidence_packs``,
and ``purge_records`` for the reporting and data-lifecycle surface (M57). Defensive
and idempotent: ``0001`` builds the full schema from current metadata.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

_TABLES = (
    "report_runs",
    "report_schedules",
    "audit_exports",
    "evidence_packs",
    "purge_records",
)


def upgrade() -> None:
    """Create the reporting tables (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the reporting tables."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
