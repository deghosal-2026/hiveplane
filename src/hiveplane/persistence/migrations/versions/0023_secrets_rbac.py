"""secrets store and rbac

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-27

Adds ``secrets``, ``secret_versions``, ``api_keys``, and ``access_audit`` for the
secret store, RBAC-lite, and access audit (M45). Defensive and idempotent:
``0001`` builds the full schema from current metadata.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

_TABLES = (
    "secret_vault",
    "secret_vault_versions",
    "api_keys",
    "access_audit",
)


def upgrade() -> None:
    """Create the secret/RBAC tables (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the secret/RBAC tables."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
