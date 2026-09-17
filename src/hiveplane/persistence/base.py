"""SQLAlchemy base, engine, and session factory (M18)."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from hiveplane.config import Settings, get_settings


class Base(DeclarativeBase):
    """Declarative base for all persistence models."""


def create_engine_from_settings(settings: Settings | None = None) -> Engine:
    """Create a SQLAlchemy engine for the configured database."""
    resolved = settings or get_settings()
    return create_engine(resolved.database.url, pool_pre_ping=True, future=True)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a session factory bound to ``engine``."""
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
