"""published corpus releases

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-27

Adds ``corpus_releases`` for immutable, content-hashed corpus versioning and
sharing (M55-03). Defensive and idempotent: ``0001`` builds the full schema from
current metadata.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

_TABLES = ("corpus_releases",)


def upgrade() -> None:
    """Create the corpus_releases table (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the corpus_releases table."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
