"""mcp registry v2

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-27

Adds ``mcp_servers``, ``mcp_tools``, and ``mcp_tool_versions`` for the live MCP
Registry v2 (M44). Defensive and idempotent: ``0001`` builds the full schema
from current metadata.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

_TABLES = ("mcp_servers", "mcp_tools", "mcp_tool_versions")


def upgrade() -> None:
    """Create the MCP registry tables (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the MCP registry tables."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
