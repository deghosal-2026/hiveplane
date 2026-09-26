"""reconcile run history

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-26

Adds the append-only ``reconcile_runs`` table (M26-04): one row per reconcile
pass, carrying the revision, mode, action counts, outcome, and timestamps.
Defensive and idempotent: ``0001`` builds the full schema from current metadata,
so on a fresh database the step is a no-op.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_TABLE = "reconcile_runs"


def upgrade() -> None:
    """Create the reconcile-run history table (idempotent)."""
    Base.metadata.tables[_TABLE].create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    """Drop the reconcile-run history table."""
    Base.metadata.tables[_TABLE].drop(op.get_bind(), checkfirst=True)
