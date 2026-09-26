"""trigger webhook nonces

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-26

Adds ``trigger_nonces`` (M27-02): seen ``(trigger_id, nonce)`` pairs used for
webhook replay protection. Defensive and idempotent: ``0001`` builds the full
schema from current metadata, so on a fresh database the step is a no-op.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_TABLE = "trigger_nonces"


def upgrade() -> None:
    """Create the webhook-nonce table (idempotent)."""
    Base.metadata.tables[_TABLE].create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    """Drop the webhook-nonce table."""
    Base.metadata.tables[_TABLE].drop(op.get_bind(), checkfirst=True)
