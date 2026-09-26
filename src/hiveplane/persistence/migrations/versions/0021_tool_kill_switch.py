"""tool kill switch

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-27

Adds the global ``tool_kill_switch`` table (M40-06). Defensive and idempotent:
``0001`` builds the full schema from current metadata.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

_TABLES = ("tool_kill_switch",)


def upgrade() -> None:
    """Create the kill-switch table (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the kill-switch table."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
