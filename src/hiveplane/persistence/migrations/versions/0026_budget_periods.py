"""budget periods and alerts

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-27

Adds ``budget_periods`` and ``budget_alerts`` for cost showback and threshold
alerts (M49). Defensive and idempotent: ``0001`` builds the full schema from
current metadata.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

_TABLES = ("budget_periods", "budget_alerts")


def upgrade() -> None:
    """Create the budget-period tables (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the budget-period tables."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
