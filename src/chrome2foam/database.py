"""Database engine and session management."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from chrome2foam.models import Base

_DEFAULT_DB = "chrome2foam.db"


def get_engine(db_path: str | Path = _DEFAULT_DB):
    """Create a SQLAlchemy engine for the given SQLite path."""
    url = f"sqlite:///{db_path}"
    return create_engine(url)


def init_db(db_path: str | Path = _DEFAULT_DB):
    """Create tables if they do not exist yet and return the engine."""
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    return engine


def get_session(engine) -> Session:
    """Return a new session bound to *engine*."""
    return Session(engine)
