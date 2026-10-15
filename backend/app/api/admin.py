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
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth.dependencies import require_admin
from app.core.config import get_settings
from app.core.ratelimit import rate_limit
from app.db.session import get_db
from app.models.activity_log import (
    ANALYZE,
    CLICK,
    LOGIN,
    PREPARE,
    REGISTER,
    STAY,
    VISIT,
    ActivityLog,
)
from app.models.resume_record import ResumeRecord
from app.models.target_role import TargetRole
from app.models.user import User
from app.schemas.admin import (
    AdminStats,
    AdminUserRow,
    AdminUsersPage,
    AllTime,
    DayCount,
    InRange,
    LiveFeedItem,
    LoginRow,
    PageTimeRow,
    RangeInfo,
    SetActiveRequest,
    SystemStatus,
    TopClick,
    TopPage,
    VisitRow,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def _as_utc(value: datetime | None) -> datetime | None:
    # SQLite returns naive datetimes; treat them as UTC for day-bucketing.
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _parse_range(from_s: str | None, to_s: str | None) -> tuple[date, date]:
    """Parse ?from=&to= (YYYY-MM-DD) with a last-30-days default.

    Invalid input falls back to the default; a reversed range is swapped;
    spans longer than a year are clamped — the admin UI always gets a sane,
    bounded window.
    """
    today = datetime.now(timezone.utc).date()
    default_from = today - timedelta(days=29)
    d0, d1 = default_from, today
    try:
        if from_s:
            d0 = date.fromisoformat(from_s)
    except ValueError:
        pass
    try:
        if to_s:
            d1 = date.fromisoformat(to_s)
    except ValueError:
        pass
    if d0 > d1:
        d0, d1 = d1, d0
    if (d1 - d0).days > 365:
        d0 = d1 - timedelta(days=365)
    return d0, d1


def _in_range(value: datetime | None, d0: date, d1: date) -> bool:
    value = _as_utc(value)
    return value is not None and d0 <= value.date() <= d1


def _series_days(d0: date, d1: date) -> list[DayCount]:
    """Zero-filled daily buckets covering exactly [d0, d1]."""
    out, day = [], d0
    while day <= d1:
        out.append(DayCount(day=day.isoformat(), count=0))
        day += timedelta(days=1)
    return out


def _bucket_range(
    datetimes: list[datetime | None], days: list[DayCount]
) -> list[DayCount]:
    by_day = {b.day: b for b in days}
    for value in datetimes:
        value = _as_utc(value)
        if value is None:
            continue
        bucket = by_day.get(value.date().isoformat())
        if bucket is not None:
            bucket.count += 1
    return days


def _location(country: str | None, region: str | None, city: str | None) -> str:
    """Human location from GeoIP labels. Raw IPs never leave the server."""
    if country == "Local":
        return "Local network"
    parts = [p for p in (city, region, country) if p and p != "Unknown"]
    return ", ".join(parts) if parts else "Unknown"


def _device(user_agent: str | None) -> str:
    """Short 'Browser · OS' label from a user-agent string."""
    ua = user_agent or ""
    if "Edg/" in ua:
        browser = "Edge"
    elif "Chrome/" in ua:
        browser = "Chrome"
    elif "Firefox/" in ua:
        browser = "Firefox"
    elif "Safari/" in ua:
        browser = "Safari"
    else:
        browser = "Browser"
    if "Windows" in ua:
        os_name = "Windows"
    elif "Android" in ua:
        os_name = "Android"
    elif "iPhone" in ua or "iPad" in ua:
        os_name = "iOS"
    elif "Macintosh" in ua or "Mac OS" in ua:
        os_name = "Mac"
    elif "Linux" in ua:
        os_name = "Linux"
    else:
        os_name = ""
    return f"{browser} · {os_name}" if os_name else browser


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
    location: str = "",
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
        last_location=location,
    )


def _last_locations(
    db: Session,
) -> dict[int, str]:
    """Latest known location per user from any geo-tagged activity."""
    latest: dict[int, tuple[datetime, str]] = {}

    def _note(user_id: int | None, at: datetime | None, loc: str) -> None:
        if not user_id or not loc or loc == "Unknown":
            return
        at = _as_utc(at) or datetime.min.replace(tzinfo=timezone.utc)
        if user_id not in latest or at > latest[user_id][0]:
            latest[user_id] = (at, loc)

    for a in db.scalars(select(ActivityLog)).all():
        _note(a.user_id, a.created_at, _location(a.country, a.region, a.city))
    return {uid: loc for uid, (_, loc) in latest.items()}


@router.get("/stats", response_model=AdminStats)
async def stats(
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    from_date: str | None = Query(default=None, alias="from", max_length=10),
    to_date: str | None = Query(default=None, alias="to", max_length=10),
) -> AdminStats:
    rate_limit(request, "admin")
    d0, d1 = _parse_range(from_date, to_date)

    users = db.scalars(select(User)).all()
    by_id = {u.id: u for u in users}
    counts = _resume_counts(db)
    role_counts = _role_counts(db)
    resumes_total = sum(counts.values())
    roles_total = sum(role_counts.values())

    all_time = AllTime(
        users_total=len(users),
        active_users=sum(1 for u in users if u.is_active),
        admin_count=sum(1 for u in users if u.is_admin),
        resumes_total=resumes_total,
        roles_total=roles_total,
        total_logins=sum(u.login_count or 0 for u in users),
        visits_total=db.scalar(
            select(func.count()).select_from(ActivityLog).where(ActivityLog.kind == VISIT)
        ) or 0,
        interviews_total=db.scalar(
            select(func.count()).select_from(ActivityLog).where(ActivityLog.kind == PREPARE)
        ) or 0,
        questions_total=db.scalar(
            select(func.count()).select_from(ActivityLog).where(ActivityLog.kind == ANALYZE)
        ) or 0,
    )

    # --- Range-bound activity (one unified log, filtered by kind) ---
    acts = db.scalars(select(ActivityLog)).all()

    def _of_kind(kind: str) -> list:
        return [a for a in acts if a.kind == kind and _in_range(a.created_at, d0, d1)]

    logins = _of_kind(LOGIN)
    interviews = _of_kind(PREPARE)
    questions = _of_kind(ANALYZE)
    visits = _of_kind(VISIT)
    clicks = _of_kind(CLICK)
    stays = _of_kind(STAY)

    by_id = {u.id: u for u in users}
    signups = [u.created_at for u in users if _in_range(u.created_at, d0, d1)]
    range_visits, range_clicks, range_stays = visits, clicks, stays
    range_ips = {v.ip_hash for v in visits if v.ip_hash}

    in_range = InRange(
        signups=len(signups),
        logins=len(logins),
        interviews=len(interviews),
        questions=len(questions),
        visits=len(range_visits),
        visitors=len(range_ips),
        clicks=len(range_clicks),
        seconds=sum(s.seconds or 0 for s in range_stays),
    )

    days = _series_days(d0, d1)
    signups_by_day = _bucket_range([u for u in signups], days)
    logins_by_day = _bucket_range([e.created_at for e in logins], _series_days(d0, d1))
    visits_by_day = _bucket_range([v.created_at for v in range_visits], _series_days(d0, d1))
    interviews_by_day = _bucket_range(
        [e.created_at for e in interviews], _series_days(d0, d1)
    )

    per_day_visitors: dict[str, set[str]] = {}
    for v in range_visits:
        day = (_as_utc(v.created_at) or datetime.now(timezone.utc)).date().isoformat()
        if v.ip_hash:
            per_day_visitors.setdefault(day, set()).add(v.ip_hash)
    visitors_by_day = _series_days(d0, d1)
    for bucket in visitors_by_day:
        bucket.count = len(per_day_visitors.get(bucket.day, ()))


    # Most-visited paths (range) with visitor counts.
    path_visits: dict[str, int] = {}
    path_visitors: dict[str, set[str]] = {}
    for v in range_visits:
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

    # Most-pressed buttons/links (range): grouped by label + page.
    click_groups: dict[tuple[str, str], int] = {}
    for c in range_clicks:
        key = ((c.label or "(no label)"), c.path)
        click_groups[key] = click_groups.get(key, 0) + 1
    top_clicks = sorted(
        (
            TopClick(label=label, path=path, count=count)
            for (label, path), count in click_groups.items()
        ),
        key=lambda t: t.count,
        reverse=True,
    )[:10]

    # Dwell time per page (range): total seconds + stay count.
    stay_groups: dict[str, dict[str, int]] = {}
    for s in range_stays:
        entry = stay_groups.setdefault(s.path, {"seconds": 0, "views": 0})
        entry["seconds"] += s.seconds or 0
        entry["views"] += 1
    page_times = sorted(
        (
            PageTimeRow(path=path, seconds=info["seconds"], views=info["views"])
            for path, info in stay_groups.items()
        ),
        key=lambda p: p.seconds,
        reverse=True,
    )[:10]

    def _user_of(user_id: int | None) -> User | None:
        return by_id.get(user_id) if user_id else None

    recent_logins = []
    for e in sorted(logins, key=lambda e: _as_utc(e.created_at) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:8]:
        u = _user_of(e.user_id)
        recent_logins.append(
            LoginRow(
                email=u.email if u else "",
                name=(u.name or "") if u else "",
                at=_as_utc(e.created_at),
                location=_location(e.country, e.region, e.city),
            )
        )

    # Live activity feed: the freshest actions of any kind, in plain words.
    _FEED_TEXT = {
        LOGIN: "signed in",
        REGISTER: "created an account",
        PREPARE: "prepared an interview",
        ANALYZE: "got an AI answer",
        VISIT: "visited",
        STAY: "spent time on",
        CLICK: "pressed",
    }

    def _feed_text(a) -> str:
        u = _user_of(a.user_id)
        who = (u.name or u.email) if u else "A guest"
        if a.kind == VISIT:
            return f"{who} visited {a.path or 'the site'}"
        if a.kind == STAY:
            return f"{who} spent {a.seconds or 0}s on {a.path or 'a page'}"
        if a.kind == CLICK:
            return f"{who} pressed “{a.label or 'a button'}” on {a.path or 'a page'}"
        return f"{who} {_FEED_TEXT.get(a.kind, a.kind)}"

    live_feed = [
        LiveFeedItem(
            at=_as_utc(a.created_at),
            text=_feed_text(a),
            location=_location(a.country, a.region, a.city),
        )
        for a in sorted(
            (a for a in acts if _in_range(a.created_at, d0, d1)),
            key=lambda a: _as_utc(a.created_at) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[:15]
    ]

    recent_visits = []
    for v in sorted(visits, key=lambda v: _as_utc(v.created_at) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:20]:
        u = _user_of(v.user_id)
        recent_visits.append(
            VisitRow(
                at=_as_utc(v.created_at),
                path=v.path,
                email=u.email if u else "Guest",
                location=_location(v.country, v.region, v.city),
                device=_device(v.user_agent),
                visitor=(v.ip_hash or "")[:8],
            )
        )

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
        range=RangeInfo(from_date=d0.isoformat(), to_date=d1.isoformat()),
        system=system,
        all_time=all_time,
        in_range=in_range,
        signups_by_day=signups_by_day,
        logins_by_day=logins_by_day,
        visits_by_day=visits_by_day,
        interviews_by_day=interviews_by_day,
        visitors_by_day=visitors_by_day,
        top_pages=top_pages,
        top_clicks=top_clicks,
        page_times=page_times,
        recent_logins=recent_logins,
        recent_visits=recent_visits,
        live_feed=live_feed,
        recent_users=[_row(u, counts, role_counts) for u in sorted(users, key=lambda u: u.id, reverse=True)[:8]],
    )


@router.get("/users", response_model=AdminUsersPage)
async def list_users(
    request: Request,
    _admin: User = Depends(require_admin),
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    from_date: str | None = Query(default=None, alias="from", max_length=10),
    to_date: str | None = Query(default=None, alias="to", max_length=10),
    db: Session = Depends(get_db),
) -> AdminUsersPage:
    rate_limit(request, "admin")
    stmt = select(User)
    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        stmt = stmt.where(or_(User.email.ilike(like), User.name.ilike(like)))
    if from_date or to_date:
        d0, d1 = _parse_range(from_date, to_date)
        users_in_range = [
            u.id for u in db.scalars(stmt).all()
            if _in_range(u.created_at, d0, d1)
        ]
        stmt = stmt.where(User.id.in_(users_in_range)) if users_in_range else stmt.where(User.id == -1)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(User.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    counts = _resume_counts(db)
    role_counts = _role_counts(db)
    locations = _last_locations(db)
    return AdminUsersPage(
        total=total, page=page, page_size=page_size,
        users=[_row(u, counts, role_counts, locations.get(u.id, "")) for u in rows],
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
