"""Engine + session factory.

SQLite is used for local development/testing (zero infrastructure); production
sets ``DATABASE_URL`` to a PostgreSQL DSN. The same SQLAlchemy 2.x code path
serves both, so no code changes are needed to switch.
"""
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.base import Base

settings = get_settings()

# check_same_thread is a SQLite-only quirk: FastAPI serves requests from a
# threadpool, so the connection must be allowed to cross threads.
_connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables. Import models first so they register on ``Base.metadata``."""
    # Imported for side effects (model registration) — do not remove.
    from app.models import user as _user  # noqa: F401

    Base.metadata.create_all(bind=engine)
