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
        ]
        if dialect == "postgresql"
        else [
            ("username", "VARCHAR(150)"),
            ("is_admin", "BOOLEAN DEFAULT 0"),
            ("login_count", "INTEGER DEFAULT 0"),
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


def _migrate_to_activity_log() -> None:
    """One-time consolidation into ``activity_log``.

    Copies visit_logs / events / page_times / click_events rows (skipping the
    redundant user_registered events — signups already come from users), then
    drops the legacy tables. Only runs when activity_log is still empty;
    counts are verified before anything is dropped, and any mismatch aborts
    with the legacy tables left intact.
    """
    from sqlalchemy import inspect

    try:
        tables = set(inspect(engine).get_table_names())
    except Exception as exc:
        logger.warning("Could not list tables (%s); skipping activity migration", exc)
        return
    legacy = {"visit_logs", "events", "page_times", "click_events"} & tables
    if not legacy or "activity_log" not in tables:
        return

    def _cols(table: str) -> set[str]:
        try:
            return {c["name"] for c in inspect(engine).get_columns(table)}
        except Exception:
            return set()

    def _pick(cols: set[str], name: str) -> str:
        return name if name in cols else "NULL"

    with engine.begin() as conn:
        try:
            existing = conn.execute(text("SELECT COUNT(*) FROM activity_log")).scalar() or 0
        except Exception as exc:
            logger.warning("Could not read activity_log (%s); skipping migration", exc)
            return
        if existing:
            # Already live (or a previous partial run): drop only EMPTY shells.
            for table in sorted(legacy):
                try:
                    n = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0
                    if n == 0:
                        conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
                        logger.info("Dropped empty legacy table %s", table)
                except Exception as exc:
                    logger.warning("Could not drop %s (%s)", table, exc)
            return

        stmts: list[str] = []
        vc = _cols("visit_logs")
        if "visit_logs" in legacy:
            stmts.append(
                "INSERT INTO activity_log (kind, user_id, ip_hash, country, region, "
                f"city, path, method, user_agent, created_at) SELECT 'visit', user_id, "
                f"{_pick(vc, 'ip_hash')}, {_pick(vc, 'country')}, {_pick(vc, 'region')}, "
                f"{_pick(vc, 'city')}, path, {_pick(vc, 'method')}, "
                f"{_pick(vc, 'user_agent')}, created_at FROM visit_logs"
            )
        if "events" in legacy:
            stmts.append(
                "INSERT INTO activity_log (kind, user_id, ip_hash, country, region, "
                "city, created_at) SELECT name, user_id, "
                f"{_pick(_cols('events'), 'ip_hash')}, "
                f"{_pick(_cols('events'), 'country')}, "
                f"{_pick(_cols('events'), 'region')}, "
                f"{_pick(_cols('events'), 'city')}, created_at FROM events "
                "WHERE name IN ('user_login', 'interview_prepared', 'question_analyzed')"
            )
        if "page_times" in legacy:
            stmts.append(
                "INSERT INTO activity_log (kind, user_id, ip_hash, path, seconds, "
                "created_at) SELECT 'stay', user_id, ip_hash, path, seconds, "
                "created_at FROM page_times"
            )
        if "click_events" in legacy:
            stmts.append(
                "INSERT INTO activity_log (kind, user_id, ip_hash, path, label, "
                "target, created_at) SELECT 'click', user_id, ip_hash, path, label, "
                "target, created_at FROM click_events"
            )
        moved = 0
        try:
            for sql in stmts:
                result = conn.execute(text(sql))
                moved += result.rowcount if result.rowcount and result.rowcount > 0 else 0
        except Exception as exc:
            # Transaction rolls back: nothing was copied, legacy tables kept,
            # and startup continues normally.
            logger.warning("Activity migration copy failed (%s); legacy tables kept", exc)
            return
        # Verify before dropping: every migratable source row must have landed.
        try:
            sources = 0
            for table, extra in (
                ("visit_logs", ""),
                ("events", "WHERE name IN ('user_login', 'interview_prepared', 'question_analyzed')"),
                ("page_times", ""),
                ("click_events", ""),
            ):
                if table in legacy:
                    sources += conn.execute(
                        text(f"SELECT COUNT(*) FROM {table} {extra}")
                    ).scalar() or 0
            if sources != moved:
                logger.warning(
                    "Activity migration count mismatch (%s sources vs %s copied); "
                    "legacy tables kept", sources, moved,
                )
                return
            for table in sorted(legacy):
                conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
            logger.info(
                "Consolidated %s activity rows into activity_log; dropped %s",
                moved, sorted(legacy),
            )
        except Exception as exc:
            logger.warning("Activity migration finalize failed (%s); legacy tables kept", exc)
            return


def init_db() -> None:
    """Create the database (when missing), all tables, and missing columns.

    Import models first so they register on ``Base.metadata``.
    """
    _ensure_postgres_database(settings.database_url)
    # Imported for side effects (model registration) — do not remove.
    from app.models import user as _user  # noqa: F401
    from app.models import activity_log as _activity  # noqa: F401
    from app.models import resume_record as _resumes  # noqa: F401
    from app.models import target_role as _roles  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_user_columns()
    _ensure_resume_columns()
    _migrate_to_activity_log()
    # Seed the admin login when ADMIN_PASSWORD is configured (idempotent).
    from app.auth.service import ensure_admin

    db = SessionLocal()
    try:
        ensure_admin(db)
    finally:
        db.close()
