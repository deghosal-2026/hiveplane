"""incident-mode halt records

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-27

Adds ``incidents`` for incident mode: global halt, drain, broadcast, and
attributed recovery (M53). Defensive and idempotent: ``0001`` builds the full
schema from current metadata.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

_TABLES = ("incidents",)


def upgrade() -> None:
    """Create the incidents table (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the incidents table."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
