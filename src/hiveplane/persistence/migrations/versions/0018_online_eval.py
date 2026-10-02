"""online eval samples, judge scores, and rubrics

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-27

Adds ``eval_samples``, ``judge_scores``, and ``rubrics`` for online evaluation
and the production quality signal (M36-05, M36-06). Defensive and idempotent:
``0001`` builds the full schema from current metadata.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

_TABLES = ("eval_samples", "judge_scores", "rubrics")


def upgrade() -> None:
    """Create the eval tables (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the eval tables."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
