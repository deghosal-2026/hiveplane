"""router decisions and agent-tool invocations

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-26

Adds ``router_decisions`` (smart-router explanations, M30-03) and
``agent_tool_invocations`` (nested agent-as-tool calls, M30-04..M30-06).
Defensive and idempotent: ``0001`` builds the full schema from current metadata,
so on a fresh database the step is a no-op.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_TABLES = ("router_decisions", "agent_tool_invocations")


def upgrade() -> None:
    """Create the router and agent-tool tables (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the router and agent-tool tables."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
