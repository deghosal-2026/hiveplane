"""canary rollouts and model experiments

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-27

Adds ``canary_rollouts``, ``canary_samples``, ``experiment_campaigns``, and
``experiment_arms`` for progressive delivery (M38). Defensive and idempotent:
``0001`` builds the full schema from current metadata.
"""

from __future__ import annotations

from alembic import op

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

_TABLES = (
    "canary_rollouts",
    "canary_samples",
    "experiment_campaigns",
    "experiment_arms",
)


def upgrade() -> None:
    """Create the canary and experiment tables (idempotent)."""
    bind = op.get_bind()
    for name in _TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    """Drop the canary and experiment tables."""
    bind = op.get_bind()
    for name in reversed(_TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
