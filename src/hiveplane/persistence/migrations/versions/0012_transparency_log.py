"""attestation transparency log

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-26

Adds the append-only, hash-chained ``attestation_log`` table (M35-01).
Defensive and idempotent: ``0001`` builds the full schema from current metadata,
so on a fresh database the step is a no-op.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

_TABLES = ("attestation_log",)


def upgrade() -> None:
    """Create the transparency log table (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the transparency log table."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
