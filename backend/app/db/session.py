"""Engine + session factory.

SQLite is used for local development/testing (zero infrastructure); production
sets ``DATABASE_URL`` to a PostgreSQL DSN. The same SQLAlchemy 2.x code path
serves both, so no code changes are needed to switch.

On startup :func:`init_db` ensures the PostgreSQL *database itself* exists
(creating it when missing) and then creates all tables — a fresh machine only
needs a running Postgres server, nothing manual.
"""
import logging
from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.base import Base

logger = logging.getLogger(__name__)

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


def _ensure_postgres_database(url: str) -> None:
    """Create the PostgreSQL database itself when it does not exist yet.

    SQLite files appear on first connect, so only ``postgresql*`` URLs need
    this. Connects to the ``postgres`` maintenance database and issues
    ``CREATE DATABASE`` outside a transaction. Any failure (no server, no
    permission, no driver) is only logged — the engine/table creation below
    will surface the real error.
    """
    if not url.startswith("postgres"):
        return
    try:
        from sqlalchemy.engine.url import make_url

        parsed = make_url(url)
    except Exception as exc:
        logger.warning("Could not parse DATABASE_URL (%s); skipping database auto-create", exc)
        return
    dbname = parsed.database
    if not dbname or dbname in ("postgres", "template0", "template1"):
        return
    try:
        import psycopg
        from psycopg import sql as _sql
    except ImportError:
        logger.warning(
            "psycopg is not installed — cannot auto-create database %r. "
            'Install it with: pip install "psycopg[binary]>=3.1"',
            dbname,
        )
        return
    try:
        # NOTE: plain str(URL) masks the password as "***" — render with
        # hide_password=False or the maintenance connection fails auth.
        maint_url = parsed.set(drivername="postgresql", database="postgres")
        maint_url = maint_url.render_as_string(hide_password=False)
        conn = psycopg.connect(maint_url, autocommit=True, connect_timeout=5)
    except Exception as exc:
        logger.warning(
            "Postgres server not reachable (%s); skipping database auto-create", exc
        )
        return
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            if cur.fetchone():
                return
            logger.info("Database %r not found — creating it automatically", dbname)
            cur.execute(_sql.SQL("CREATE DATABASE {}").format(_sql.Identifier(dbname)))
    except Exception as exc:
        logger.warning(
            "Could not auto-create database %r (%s). Create it manually or "
            "grant CREATEDB to the role.",
            dbname,
            exc,
        )
    finally:
        conn.close()


def _ensure_user_columns() -> None:
    """Add newer ``users`` columns to databases created before they existed.

    ``create_all`` only creates missing TABLES, never missing columns — so a
    database from an earlier release would otherwise lack ``username`` /
    ``is_admin`` / ``login_count``. This brings it up to date idempotently on
    both SQLite and PostgreSQL. Data is never touched.
    """
    from sqlalchemy import inspect

    try:
        existing = {c["name"] for c in inspect(engine).get_columns("users")}
    except Exception as exc:
        logger.warning("Could not inspect users table (%s); skipping column migration", exc)
        return
    dialect = engine.dialect.name
    wanted: list[tuple[str, str]] = (
        [
            ("username", "VARCHAR(150)"),
            ("is_admin", "BOOLEAN DEFAULT FALSE"),
            ("login_count", "INTEGER DEFAULT 0"),
            ("sessions_revoked_at", "TIMESTAMP WITH TIME ZONE"),
        ]
        if dialect == "postgresql"
        else [
            ("username", "VARCHAR(150)"),
            ("is_admin", "BOOLEAN DEFAULT 0"),
            ("login_count", "INTEGER DEFAULT 0"),
            ("sessions_revoked_at", "DATETIME"),
        ]
    )
    with engine.begin() as conn:
        for name, ddl in wanted:
            if name in existing:
                continue
            try:
                logger.info("Adding missing column users.%s", name)
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {ddl}"))
            except Exception as exc:
                logger.warning("Could not add users.%s (%s)", name, exc)
        try:
            if dialect == "postgresql":
                conn.execute(
                    text("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_username "
                         "ON users(username)")
                )
            else:
                conn.execute(
                    text("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_username "
                         "ON users(username)")
                )
        except Exception as exc:
            logger.warning("Could not ensure username unique index (%s)", exc)


def _ensure_resume_columns() -> None:
    """Add newer ``resumes`` columns and backfill owner emails.

    Idempotent on SQLite and PostgreSQL; existing rows keep their data and
    get the owner's current email filled in.
    """
    from sqlalchemy import inspect

    try:
        existing = {c["name"] for c in inspect(engine).get_columns("resumes")}
    except Exception as exc:
        logger.warning("Could not inspect resumes table (%s); skipping migration", exc)
        return
    # VARCHAR + DEFAULT '' is valid on both SQLite and PostgreSQL.
    ddl = "VARCHAR(320) DEFAULT ''"
    added = False
    with engine.begin() as conn:
        if "user_email" not in existing:
            try:
                logger.info("Adding missing column resumes.user_email")
                conn.execute(text(f"ALTER TABLE resumes ADD COLUMN user_email {ddl}"))
                added = True
            except Exception as exc:
                logger.warning("Could not add resumes.user_email (%s)", exc)
                return
        else:
            # Re-run the backfill when the column exists but rows predate it.
            added = True
        if added:
            try:
                backfilled = conn.execute(
                    text(
                        "UPDATE resumes SET user_email = "
                        "(SELECT email FROM users WHERE users.id = resumes.user_id) "
                        "WHERE user_email IS NULL OR user_email = ''"
                    )
                ).rowcount
                if backfilled:
                    logger.info("Backfilled user_email on %d resume row(s)", backfilled)
            except Exception as exc:
                logger.warning("Could not backfill resumes.user_email (%s)", exc)


def _drop_activity_noise_columns() -> None:
    """Drop the removed page-analytics columns from ``activity_log``.

    The business-owner lean cut stopped storing per-request visits, clicks,
    dwell time, GeoIP and device data. Existing databases still carry those
    columns, so this removes them idempotently (SQLite 3.35+ and PostgreSQL
    both support ``DROP COLUMN``). Old rows are NOT deleted — their value is
    preserved via the remaining columns, and the removed ones become NULL.
    """
    from sqlalchemy import inspect

    try:
        existing = {c["name"] for c in inspect(engine).get_columns("activity_log")}
    except Exception as exc:
        logger.warning("Could not inspect activity_log (%s); skipping column cleanup", exc)
        return
    removed = [
        "ip_hash", "country", "region", "city", "path", "method",
        "label", "target", "seconds", "user_agent",
    ]
    with engine.begin() as conn:
        for name in removed:
            if name not in existing:
                continue
            try:
                logger.info("Dropping unused column activity_log.%s", name)
                conn.execute(text(f"ALTER TABLE activity_log DROP COLUMN {name}"))
            except Exception as exc:
                logger.warning("Could not drop activity_log.%s (%s)", name, exc)


def _ensure_activity_indexes() -> None:
    """Composite index for the admin dashboard's range scans.

    ``/api/admin/stats`` filters ``activity_log`` by ``kind`` AND a
    ``created_at`` window; the single-column indexes force a scan of every
    row of that kind. ``create_all`` never adds indexes to an EXISTING table,
    so this creates it idempotently. Rollback:
    ``DROP INDEX IF EXISTS ix_activity_log_kind_created_at``.
    """
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_activity_log_kind_created_at "
                    "ON activity_log (kind, created_at)"
                )
            )
    except Exception as exc:
        logger.warning("Could not ensure activity_log composite index (%s)", exc)


def init_db() -> None:
    """Create the database (when missing), all tables, and missing columns.

    Schema only — data seeding (the admin login) happens in the app startup
    (``app.main.lifespan``) so the DB layer never depends on auth.

    Import models first so they register on ``Base.metadata``.
    """
    _ensure_postgres_database(settings.database_url)
    # Imported for side effects (model registration) — do not remove.
    from app.models import user as _user  # noqa: F401
    from app.models import activity_log as _activity  # noqa: F401
    from app.models import ai_model_config as _aim  # noqa: F401
    from app.models import resume_record as _resumes  # noqa: F401
    from app.models import target_role as _roles  # noqa: F401
    from app.models import billing as _billing  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_user_columns()
    _ensure_resume_columns()
    _drop_activity_noise_columns()
    _ensure_activity_indexes()
