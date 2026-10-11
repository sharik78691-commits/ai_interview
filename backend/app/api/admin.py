"""Admin-panel REST endpoints (admin users only).

GET  /api/admin/stats          — headline counters + 14-day series + recent users
GET  /api/admin/users          — searchable, paginated user list
PATCH /api/admin/users/{id}    — activate/deactivate, promote/demote admin
GET  /api/admin/users/export   — full user list as CSV download

Every route requires an admin session (401 anonymous, 403 non-admin).
Aggregations are computed in Python over small result sets so the same code
runs unchanged on SQLite and PostgreSQL.
"""
import csv
import io
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth.dependencies import require_admin
from app.core.config import get_settings
from app.core.ratelimit import rate_limit
from app.db.session import get_db
from app.models.event import (
    INTERVIEW_PREPARED,
    QUESTION_ANALYZED,
    USER_LOGIN,
    EventLog,
)
from app.models.resume_record import ResumeRecord
from app.models.target_role import TargetRole
from app.models.user import User
from app.models.visit import VisitLog
from app.schemas.admin import (
    AdminStats,
    AdminUserRow,
    AdminUsersPage,
    DayCount,
    SetActiveRequest,
    SystemStatus,
    TopPage,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

_SERIES_DAYS = 14


def _as_utc(value: datetime | None) -> datetime | None:
    # SQLite returns naive datetimes; treat them as UTC for day-bucketing.
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _is_today(value: datetime | None) -> bool:
    value = _as_utc(value)
    return value is not None and value.date() == datetime.now(timezone.utc).date()


def _empty_series(days: int = _SERIES_DAYS) -> list[DayCount]:
    today = datetime.now(timezone.utc).date()
    return [
        DayCount(day=(today - timedelta(days=i)).isoformat(), count=0)
        for i in reversed(range(days))
    ]


def _bucket(datetimes: list[datetime | None], days: int = _SERIES_DAYS) -> list[DayCount]:
    series = _empty_series(days)
    by_day = {b.day: b for b in series}
    for value in datetimes:
        value = _as_utc(value)
        if value is None:
            continue
        bucket = by_day.get(value.date().isoformat())
        if bucket is not None:
            bucket.count += 1
    return series


def _resume_counts(db: Session) -> dict[int, int]:
    """Resumes per user in ONE query (avoids N+1 on every user list)."""
    rows = db.execute(
        select(ResumeRecord.user_id, func.count()).group_by(ResumeRecord.user_id)
    ).all()
    return {user_id: count for user_id, count in rows}


def _role_counts(db: Session) -> dict[int, int]:
    """Saved target roles per user in ONE query."""
    rows = db.execute(
        select(TargetRole.user_id, func.count()).group_by(TargetRole.user_id)
    ).all()
    return {user_id: count for user_id, count in rows}


def _row(
    user: User,
    resume_counts: dict[int, int] | None = None,
    role_counts: dict[int, int] | None = None,
) -> AdminUserRow:
    return AdminUserRow(
        id=user.id,
        email=user.email,
        name=user.name or "",
        username=user.username,
        provider=user.auth_provider,
        is_admin=bool(user.is_admin),
        is_active=bool(user.is_active),
        login_count=user.login_count or 0,
        resumes_count=(resume_counts or {}).get(user.id, 0),
        roles_count=(role_counts or {}).get(user.id, 0),
        last_login_at=_as_utc(user.last_login_at),
        created_at=_as_utc(user.created_at),
    )


@router.get("/stats", response_model=AdminStats)
async def stats(
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminStats:
    rate_limit(request, "admin")

    users = db.scalars(select(User)).all()
    counts = _resume_counts(db)
    role_counts = _role_counts(db)
    resumes_total = sum(counts.values())
    roles_total = sum(role_counts.values())
    users_total = len(users)
    active_users = sum(1 for u in users if u.is_active)
    admin_count = sum(1 for u in users if u.is_admin)
    total_logins = sum(u.login_count or 0 for u in users)
    logins_today = sum(1 for u in users if _is_today(u.last_login_at))

    visits = db.scalars(select(VisitLog)).all()
    visits_total = len(visits)
    today_visits = [v for v in visits if _is_today(v.created_at)]
    visits_today = len(today_visits)
    visitors_today = len({v.ip_hash for v in today_visits if v.ip_hash})

    # --- Activity trends from the append-only event log ---
    events = db.scalars(select(EventLog)).all()

    def _named(name: str) -> list[datetime | None]:
        return [e.created_at for e in events if e.name == name]

    logins = _named(USER_LOGIN)
    interviews = _named(INTERVIEW_PREPARED)
    questions = _named(QUESTION_ANALYZED)
    interviews_total = len(interviews)
    interviews_today = sum(1 for t in interviews if _is_today(t))
    questions_total = len(questions)

    # Unique visitors per day (salted IP hashes, never raw IPs).
    per_day_visitors: dict[str, set[str]] = {}
    for v in visits:
        day = (_as_utc(v.created_at) or datetime.now(timezone.utc)).date().isoformat()
        if v.ip_hash:
            per_day_visitors.setdefault(day, set()).add(v.ip_hash)
    visitors_series = _empty_series()
    for bucket in visitors_series:
        bucket.count = len(per_day_visitors.get(bucket.day, ()))

    # Most-visited paths with their visitor counts.
    path_visits: dict[str, int] = {}
    path_visitors: dict[str, set[str]] = {}
    for v in visits:
        path_visits[v.path] = path_visits.get(v.path, 0) + 1
        if v.ip_hash:
            path_visitors.setdefault(v.path, set()).add(v.ip_hash)
    top_pages = sorted(
        (
            TopPage(path=path, visits=count, visitors=len(path_visitors.get(path, ())))
            for path, count in path_visits.items()
        ),
        key=lambda p: p.visits,
        reverse=True,
    )[:8]

    recent_logins = [
        _row(u, counts, role_counts)
        for u in sorted(
            (u for u in users if u.last_login_at is not None),
            key=lambda u: _as_utc(u.last_login_at) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[:8]
    ]

    settings = get_settings()
    db_url = settings.database_url
    system = SystemStatus(
        demo_mode=settings.demo_mode,
        llm_configured=settings.llm_configured,
        stt_configured=settings.stt_configured,
        google_configured=settings.google_configured,
        database="PostgreSQL" if db_url.startswith("postgres") else "SQLite",
        version="0.1.0",
    )

    return AdminStats(
        users_total=users_total,
        active_users=active_users,
        admin_count=admin_count,
        total_logins=total_logins,
        logins_today=logins_today,
        visits_total=visits_total,
        visits_today=visits_today,
        visitors_today=visitors_today,
        signups_by_day=_bucket([u.created_at for u in users]),
        visits_by_day=_bucket([v.created_at for v in visits]),
        recent_users=[_row(u, counts, role_counts) for u in sorted(users, key=lambda u: u.id, reverse=True)[:8]],
        system=system,
        interviews_total=interviews_total,
        interviews_today=interviews_today,
        questions_total=questions_total,
        logins_by_day=_bucket(logins),
        interviews_by_day=_bucket(interviews),
        visitors_by_day=visitors_series,
        top_pages=top_pages,
        recent_logins=recent_logins,
        resumes_total=resumes_total,
        roles_total=roles_total,
    )


@router.get("/users", response_model=AdminUsersPage)
async def list_users(
    request: Request,
    _admin: User = Depends(require_admin),
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> AdminUsersPage:
    rate_limit(request, "admin")
    stmt = select(User)
    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        stmt = stmt.where(or_(User.email.ilike(like), User.name.ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(User.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    counts = _resume_counts(db)
    role_counts = _role_counts(db)
    return AdminUsersPage(
        total=total, page=page, page_size=page_size,
        users=[_row(u, counts, role_counts) for u in rows],
    )


@router.patch("/users/{user_id}", response_model=AdminUserRow)
async def set_active(
    user_id: int,
    body: SetActiveRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminUserRow:
    rate_limit(request, "admin")
    if body.is_active is None and body.is_admin is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing to update."
        )
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
        )
    if user_id == admin.id:
        # Nobody may deactivate or demote themselves out of the admin panel
        # (avoids locking every admin out).
        if body.is_active is False or body.is_admin is False:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You cannot deactivate or demote your own admin account.",
            )
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.is_admin is not None:
        user.is_admin = body.is_admin
    db.commit()
    db.refresh(user)
    logger.info(
        "Admin (user_id=%s) updated user_id=%s (is_active=%s is_admin=%s)",
        admin.id,
        user.id,
        user.is_active,
        user.is_admin,
    )
    return _row(user, _resume_counts(db), _role_counts(db))


@router.get("/users/export")
async def export_users(
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Download the whole user list as CSV (opens in Excel/Sheets)."""
    rate_limit(request, "admin")
    users = db.scalars(select(User).order_by(User.id)).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["id", "email", "name", "username", "provider", "is_admin",
         "is_active", "login_count", "last_login_at", "created_at"]
    )
    for u in users:
        writer.writerow(
            [u.id, u.email, u.name or "", u.username or "",
             u.auth_provider, int(bool(u.is_admin)), int(bool(u.is_active)),
             u.login_count or 0, u.last_login_at or "", u.created_at or ""]
        )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=oyeinterview-users.csv"},
    )
