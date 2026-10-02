"""drift & quarantine

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-26

Adds ``quarantines`` and ``drift_assessments`` (behavioral drift detection,
auto-quarantine, and reinstatement, M34). The pre-existing ``drift_schedules``
table from ``0001`` is left untouched. Defensive and idempotent: ``0001`` builds
the full schema from current metadata, so on a fresh database the step is a
no-op.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

_TABLES = ("quarantines", "drift_assessments")


def upgrade() -> None:
    """Create the drift/quarantine tables (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the drift/quarantine tables."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
