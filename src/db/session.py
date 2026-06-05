"""Database engine, session factory, and schema initialisation."""

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from config.settings import settings
from src.db.models import Base

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,   # reconnect silently after idle-timeout drops
    pool_size=5,
    max_overflow=10,
)

SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Yield a transactional session and guarantee commit-or-rollback on exit."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create all tables that do not yet exist. Safe to call on every startup."""
    Base.metadata.create_all(bind=engine)
