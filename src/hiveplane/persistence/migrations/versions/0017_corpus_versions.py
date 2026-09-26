"""corpus versions

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-27

Adds ``corpus_versions`` for the immutable cuts that approved feedback-derived
cases land in (M36-04). Defensive and idempotent: ``0001`` builds the full schema
from current metadata.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

_TABLES = ("corpus_versions",)


def upgrade() -> None:
    """Create the corpus-versions table (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the corpus-versions table."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
