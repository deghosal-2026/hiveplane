"""Alembic environment: URL from settings, metadata from the ORM models."""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from hiveplane.config import get_settings
from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

target_metadata = Base.metadata


def _url() -> str:
    return get_settings().database.url


def run_migrations_offline() -> None:
    """Emit SQL without a database connection."""
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live database."""
    section = context.config.get_section(context.config.config_ini_section) or {}
    section["sqlalchemy.url"] = _url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
