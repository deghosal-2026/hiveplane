"""tenant lifecycle

Revision ID: 0034
Revises: 0033
Create Date: 2026-09-29

Persists the tenant lifecycle status (M58-06) on the ``tenants`` table so
suspend/activate is queryable. Defensive and idempotent: ``0001`` builds the
full schema from current metadata, so an already-populated database only needs
the new column added.
"""

from __future__ import annotations

from alembic import context, op
from sqlalchemy import Column, Connection, String, inspect

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

_TABLE = "tenants"
_COLUMN = "status"
_SERVER_DEFAULT = "active"


def _status_column() -> Column[str]:
    return Column(
        _COLUMN,
        String(32),
        nullable=False,
        server_default=_SERVER_DEFAULT,
    )


def _column_names(bind: Connection) -> set[str]:
    return {column["name"] for column in inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    """Add the tenant status column when it is absent (idempotent)."""
    bind = None if context.is_offline_mode() else op.get_bind()
    if bind is None or _COLUMN not in _column_names(bind):
        op.add_column(_TABLE, _status_column())


def downgrade() -> None:
    """Drop the tenant status column when it is present."""
    bind = None if context.is_offline_mode() else op.get_bind()
    if bind is None or _COLUMN in _column_names(bind):
        op.drop_column(_TABLE, _COLUMN)
