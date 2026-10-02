"""used approval tokens

Revision ID: 0036
Revises: 0035
Create Date: 2026-09-30

Persists claimed single-use interactive/mobile approval tokens (M51-03/04) so a
replayed token is rejected across processes and replicas. The primary key makes
the claim atomic: the first insert wins and the second raises a unique violation.
Defensive and idempotent: ``0001`` builds the full schema from current metadata.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None

_TABLES = ("used_approval_tokens",)


def upgrade() -> None:
    """Create the used-approval-tokens table (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the used-approval-tokens table."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
