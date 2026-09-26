"""signing keys

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-26

Adds the global ``signing_keys`` table for public verification key distribution
and rotation (M35-06). Defensive and idempotent: ``0001`` builds the full schema
from current metadata, so on a fresh database the step is a no-op.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

_TABLES = ("signing_keys",)


def upgrade() -> None:
    """Create the signing-keys table (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the signing-keys table."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
