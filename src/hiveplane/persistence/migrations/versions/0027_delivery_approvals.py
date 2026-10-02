"""delivery attempts and approval decisions

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-27

Adds ``delivery_attempts`` and ``approval_decisions`` for fan-out audit and
interactive/mobile approvals (M51). Defensive and idempotent: ``0001`` builds
the full schema from current metadata.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

_TABLES = ("delivery_attempts", "approval_decisions")


def upgrade() -> None:
    """Create the delivery/approval tables (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the delivery/approval tables."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
