"""shadow runs

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-27

Adds ``shadow_runs`` for progressive delivery (M37). Defensive and idempotent:
``0001`` builds the full schema from current metadata.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

_TABLES = ("shadow_runs",)


def upgrade() -> None:
    """Create the shadow-runs table (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the shadow-runs table."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
