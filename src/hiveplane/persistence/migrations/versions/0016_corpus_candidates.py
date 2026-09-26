"""corpus candidates and reviews

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-27

Adds ``corpus_candidates`` and ``candidate_reviews`` for the feedback → corpus
pipeline and its mandatory review gate (M36-02, M36-03). Defensive and
idempotent: ``0001`` builds the full schema from current metadata.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

_TABLES = ("corpus_candidates", "candidate_reviews")


def upgrade() -> None:
    """Create the candidate and review tables (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the candidate and review tables."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
