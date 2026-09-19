"""Programmatic Alembic migration runner for control-plane startup (#118)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from hiveplane.config import Settings, get_settings

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _alembic_config(settings: Settings) -> Config:
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", settings.database.url)
    return config


def run_migrations(settings: Settings | None = None) -> None:
    """Upgrade the configured database schema to ``head``.

    Idempotent: applying migrations that are already current is a no-op, so this
    is safe to run on every boot.
    """
    command.upgrade(_alembic_config(settings or get_settings()), "head")
