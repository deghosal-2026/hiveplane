"""promotions

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-26

Adds ``promotions`` (recorded staging -> production promotion attempts, M32).
Defensive and idempotent: ``0001`` builds the full schema from current metadata,
so on a fresh database the step is a no-op.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_TABLES = ("promotions",)


def upgrade() -> None:
    """Create the promotions table (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the promotions table."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
