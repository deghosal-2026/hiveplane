"""worker tokens

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-27

Adds the ``worker_tokens`` table for signed worker identity (M46-05). Defensive
and idempotent: ``0001`` builds the full schema from current metadata.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

_TABLES = ("worker_tokens",)


def upgrade() -> None:
    """Create the worker-tokens table (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the worker-tokens table."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
