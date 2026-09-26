"""run feedback

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-27

Adds ``run_feedback`` for operator feedback on terminal production runs
(M36-01). Defensive and idempotent: ``0001`` builds the full schema from current
metadata, so on a fresh database the step is a no-op.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

_TABLES = ("run_feedback",)


def upgrade() -> None:
    """Create the run-feedback table (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the run-feedback table."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
