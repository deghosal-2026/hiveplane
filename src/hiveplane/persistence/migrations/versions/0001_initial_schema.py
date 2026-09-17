"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-01-01
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the full HivePlane schema and its indexes."""
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    """Drop the full HivePlane schema."""
    Base.metadata.drop_all(bind=op.get_bind())
