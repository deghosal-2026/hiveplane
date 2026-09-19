"""budget spend aggregates

Revision ID: 0002
Revises: 0001
Create Date: 2026-01-02
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_TABLES = ("budget_run_spend", "budget_day_spend", "budget_team_spend")


def upgrade() -> None:
    """Create the durable budget aggregate tables (#128)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the durable budget aggregate tables."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
