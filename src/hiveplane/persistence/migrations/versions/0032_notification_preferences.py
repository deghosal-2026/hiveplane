"""notification preferences

Revision ID: 0032
Revises: 0031
Create Date: 2026-09-27

Persists per-team notification preferences (M51-06) so digest routing survives
reload (M57-02). Defensive and idempotent: ``0001`` builds the full schema from
current metadata.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

_TABLES = ("notification_preferences",)


def upgrade() -> None:
    """Create the notification-preferences table (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the notification-preferences table."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
