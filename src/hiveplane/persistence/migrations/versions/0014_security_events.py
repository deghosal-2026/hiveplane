"""security events

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-26

Adds ``security_events`` (append-only defense telemetry for injection, egress
denial, taint blocks, and repeated-attempt escalation, M39). Defensive and
idempotent: ``0001`` builds the full schema from current metadata, so on a fresh
database the step is a no-op.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

_TABLES = ("security_events",)


def upgrade() -> None:
    """Create the security-events table (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the security-events table."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
