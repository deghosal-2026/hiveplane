"""audit chain anchor

Revision ID: 0033
Revises: 0032
Create Date: 2026-09-27

Persists the global audit-chain anchor (the hash of the last pruned record) so a
pruned chain stays verifiable across restarts (M57-05). Defensive and idempotent:
``0001`` builds the full schema from current metadata.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

_TABLES = ("audit_anchor",)


def upgrade() -> None:
    """Create the audit-anchor table (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the audit-anchor table."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
