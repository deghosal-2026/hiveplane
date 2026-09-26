"""trigger freeze windows

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-26

Adds ``trigger_freezes`` (M28-05): declared maintenance/freeze windows that
suppress triggers and drain running work. Defensive and idempotent: ``0001``
builds the full schema from current metadata, so on a fresh database the step is
a no-op.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_TABLE = "trigger_freezes"


def upgrade() -> None:
    """Create the freeze-window table (idempotent)."""
    Base.metadata.tables[_TABLE].create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    """Drop the freeze-window table."""
    Base.metadata.tables[_TABLE].drop(op.get_bind(), checkfirst=True)
