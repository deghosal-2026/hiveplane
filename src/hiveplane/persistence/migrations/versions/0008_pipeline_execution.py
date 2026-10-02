"""pipeline execution records

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-26

Adds ``pipeline_run_headers`` (parent pipeline runs) and ``pipeline_node_runs``
(per-node executions) for the M29 pipeline engine. Defensive and idempotent:
``0001`` builds the full schema from current metadata, so on a fresh database the
step is a no-op.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_TABLES = ("pipeline_run_headers", "pipeline_node_runs")


def upgrade() -> None:
    """Create the pipeline execution tables (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the pipeline execution tables."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
