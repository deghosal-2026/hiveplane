"""replay records

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-30

Adds ``replays`` for audited replay/fork/A-B operations over a source run (M60).
Defensive and idempotent: ``0001`` builds the full schema from current metadata.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

_TABLES = ("replays",)


def upgrade() -> None:
    """Create the replay table (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the replay table."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
