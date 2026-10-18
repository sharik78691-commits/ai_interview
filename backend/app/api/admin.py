"""Admin-panel REST endpoints (admin users only).

GET  /api/admin/stats           — business dashboard: system status, totals,
                                   signups (last 30 days), AI usage summary
GET  /api/admin/business        — owner report: growth, funnel, retention,
                                   segments, heatmap, who-to-contact, insights
GET  /api/admin/users           — searchable, paginated user list
PATCH /api/admin/users/{id}     — activate/deactivate, promote/demote admin
GET  /api/admin/users/export    — full user list as CSV download
GET  /api/admin/ai-models       — model catalog + provider error stats
PATCH /api/admin/ai-models/{key} — admin-editable model flags

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
from app.models.activity_log import ANALYZE, LLM_USAGE, PREPARE, ActivityLog
from app.models.ai_model_config import AIModelConfig
from app.models.user import User
from app.providers.llm import registry
from app.providers.stt.provider import (
    active_stt_model,
    default_stt_key,
    list_stt_models,
)
from app.services.ai_cost import build_cost_report
from app.services.business_analytics import EventRow, UserRow, build_report
from app.schemas.admin import (
    AIModelUpdateRequest,
    AdminStats,
    AdminUserRow,
    AdminUsersPage,
    AiUsageRow,
    DayCount,
    InRange,
    RangeInfo,
    SetActiveRequest,
    SystemStatus,
    Totals,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

_DEFAULT_DAYS = 30
_MAX_DAYS = 365


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
    d0, d1 = today - timedelta(days=_DEFAULT_DAYS - 1), today
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
    if (d1 - d0).days > _MAX_DAYS:
        d0 = d1 - timedelta(days=_MAX_DAYS)
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


def _row(user: User) -> AdminUserRow:
    return AdminUserRow(
        id=user.id,
        email=user.email,
        name=user.name or "",
        username=user.username,
        provider=user.auth_provider,
        is_admin=bool(user.is_admin),
        is_active=bool(user.is_active),
        login_count=user.login_count or 0,
        last_login_at=_as_utc(user.last_login_at),
        created_at=_as_utc(user.created_at),
    )


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
    acts = db.scalars(select(ActivityLog)).all()

    # --- lifetime account counters (states, not events) ---
    totals = Totals(
        users_total=len(users),
        active_users=sum(1 for u in users if u.is_active),
        admin_count=sum(1 for u in users if u.is_admin),
        total_logins=sum(u.login_count or 0 for u in users),
    )

    # --- in-range event metrics ---
    signups = [u for u in users if _in_range(u.created_at, d0, d1)]
    interviews = [a for a in acts if a.kind == PREPARE and _in_range(a.created_at, d0, d1)]
    questions = [a for a in acts if a.kind == ANALYZE and _in_range(a.created_at, d0, d1)]
    llm = [a for a in acts if a.kind == LLM_USAGE and _in_range(a.created_at, d0, d1)]
    in_range = InRange(
        signups=len(signups),
        interviews=len(interviews),
        questions=len(questions),
        ai_calls=len(llm),
        ai_errors=sum(
            1 for a in llm if a.error_category is not None and a.error_category != "ok"
        ),
    )

    # --- signups per day over the selected window ---
    buckets: dict[str, int] = {b.day: 0 for b in _series_days(d0, d1)}
    for u in users:
        created = _as_utc(u.created_at)
        if created is not None:
            key = created.date().isoformat()
            if key in buckets:
                buckets[key] += 1
    signups_by_day = [DayCount(day=k, count=v) for k, v in buckets.items()]

    # --- AI usage summary over the window (cost/reliability view) ---
    by_model: dict[tuple[str, str], list[ActivityLog]] = {}
    for a in llm:
        by_model.setdefault((a.provider or "unknown", a.model or "unknown"), []).append(a)

    def _avg_latency(rows: list[ActivityLog]) -> float | None:
        lat = [r.latency_ms for r in rows if r.latency_ms is not None]
        return round(sum(lat) / len(lat), 1) if lat else None

    ai_usage = sorted(
        (
            AiUsageRow(
                provider=p,
                model=m,
                calls=len(rows),
                errors=sum(
                    1 for r in rows if r.error_category is not None and r.error_category != "ok"
                ),
                avg_latency_ms=_avg_latency(rows),
            )
            for (p, m), rows in by_model.items()
        ),
        key=lambda r: r.calls,
        reverse=True,
    )

    system = SystemStatus(
        demo_mode=get_settings().demo_mode,
        llm_configured=get_settings().llm_configured,
        stt_configured=get_settings().stt_configured,
        google_configured=get_settings().google_configured,
        database=(
            "PostgreSQL"
            if get_settings().database_url.startswith("postgres")
            else "SQLite"
        ),
        version="0.1.0",
    )

    return AdminStats(
        system=system,
        range=RangeInfo(from_date=d0.isoformat(), to_date=d1.isoformat()),
        totals=totals,
        in_range=in_range,
        signups_by_day=signups_by_day,
        ai_usage=ai_usage,
        recent_users=[
            _row(u) for u in sorted(users, key=lambda u: u.id, reverse=True)[:8]
        ],
    )


@router.get("/business")
async def business(
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    from_date: str | None = Query(default=None, alias="from", max_length=10),
    to_date: str | None = Query(default=None, alias="to", max_length=10),
) -> dict:
    """Owner-level business report: growth, funnel, retention, segments,
    usage heatmap, AI reliability, who to contact, and ranked next actions."""
    rate_limit(request, "admin")
    d0, d1 = _parse_range(from_date, to_date)
    users = [
        UserRow(
            id=u.id, name=u.name or "", email=u.email,
            provider=u.auth_provider or "local", created_at=u.created_at,
            is_admin=bool(u.is_admin), is_active=bool(u.is_active),
        )
        for u in db.scalars(select(User)).all()
    ]
    events = [
        EventRow(
            kind=a.kind, user_id=a.user_id, created_at=a.created_at,
            provider=a.provider, model=a.model, latency_ms=a.latency_ms,
            input_tokens=a.input_tokens, output_tokens=a.output_tokens,
            error_category=a.error_category,
        )
        for a in db.scalars(select(ActivityLog)).all()
    ]
    return build_report(users, events, d0, d1)


DEMO_EMAIL_SUFFIX = "@demo.oyeinterview.test"


@router.get("/ai-costs")
async def ai_costs(
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    include_demo: bool = Query(default=False),
) -> dict:
    """Per-model usage + cost for 24h / 7d / 30d / 90d (rolling, live data).

    Demo-seeded users (``@demo.oyeinterview.test``) are excluded unless
    ``include_demo`` so seeded sample data never pollutes real spend.
    """
    rate_limit(request, "admin")
    demo_ids: set[int] = set()
    if not include_demo:
        demo_ids = set(
            db.scalars(select(User.id).where(User.email.like(f"%{DEMO_EMAIL_SUFFIX}")))
        )
    rows = db.scalars(select(ActivityLog).where(ActivityLog.kind == LLM_USAGE)).all()
    events = [
        EventRow(
            kind=a.kind, user_id=a.user_id, created_at=a.created_at,
            provider=a.provider, model=a.model, latency_ms=a.latency_ms,
            input_tokens=a.input_tokens, output_tokens=a.output_tokens,
            error_category=a.error_category,
        )
        for a in rows
        if a.user_id not in demo_ids
    ]
    return build_cost_report(events, registry.list_models())


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
    return AdminUsersPage(
        total=total, page=page, page_size=page_size,
        users=[_row(u) for u in rows],
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
    return _row(user)


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


# ------------------------------------------------------------- AI model management


def _llm_error_stats(db: Session) -> dict[str, dict[str, int]]:
    """Provider -> {error_category: count} for llm_usage rows. No raw tokens."""
    rows = db.execute(
        select(ActivityLog.provider, ActivityLog.error_category, func.count())
        .where(ActivityLog.kind == LLM_USAGE)
        .group_by(ActivityLog.provider, ActivityLog.error_category)
    ).all()
    out: dict[str, dict[str, int]] = {}
    for provider, category, count in rows:
        p = provider or "unknown"
        out.setdefault(p, {})[(category or "ok")] = count
    return out


@router.get("/ai-models")
async def list_ai_models_admin(
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Model catalog + provider health/error stats (no secrets)."""
    rate_limit(request, "admin")
    return {
        "models": registry.list_models(),
        "default": registry.default_model_key(),
        "errors": _llm_error_stats(db),
    }


@router.patch("/ai-models/{key}")
async def update_ai_model(
    key: str,
    body: AIModelUpdateRequest,
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Update admin-editable model flags. ``key`` is validated against the
    server allowlist — arbitrary/executable config can never be injected."""
    rate_limit(request, "admin")
    try:
        registry.get_model(key)  # raises ValueError for unknown keys
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    record = db.get(AIModelConfig, key)
    if record is None:
        record = AIModelConfig(key=key)
        db.add(record)

    if body.enabled is not None:
        record.enabled = body.enabled
    if body.is_default is not None:
        record.is_default = body.is_default
        if body.is_default:
            # Exactly one default: clear the flag on every other model.
            for other in db.scalars(select(AIModelConfig)).all():
                if other.key != key:
                    other.is_default = False
    if body.display_name is not None:
        record.display_name = body.display_name.strip()[:120]
    if body.description is not None:
        record.description = body.description.strip()[:2000]
    if body.free_tier is not None:
        record.free_tier = body.free_tier
    if body.priority is not None:
        record.priority = max(-1000, min(1000, body.priority))

    db.commit()
    # Cached overrides would otherwise hide this change for up to the TTL.
    registry.invalidate_model_overrides()
    logger.info("Admin updated AI model config key=%s enabled=%s default=%s",
                key, record.enabled, record.is_default)
    return {"models": registry.list_models(), "default": registry.default_model_key()}


# ------------------------------------------------------------- STT model management


@router.get("/stt-models")
async def list_stt_models_admin(
    request: Request,
    _admin: User = Depends(require_admin),
) -> dict:
    """STT model catalog + active transcription model (no secrets)."""
    rate_limit(request, "admin")
    return {
        "models": list_stt_models(),
        "default": default_stt_key(),
        "active": active_stt_model(),
    }


@router.patch("/stt-models/{key}")
async def update_stt_model(
    key: str,
    body: AIModelUpdateRequest,
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Enable/disable a speech-to-text model or change the STT default."""
    rate_limit(request, "admin")
    valid_keys = {m["key"] for m in list_stt_models()}
    if key not in valid_keys:
        raise HTTPException(status_code=404, detail=f"unknown STT model '{key}'")

    record = db.get(AIModelConfig, key)
    if record is None:
        record = AIModelConfig(key=key)
        db.add(record)

    if body.enabled is not None:
        record.enabled = body.enabled
    if body.is_default is not None:
        record.is_default = body.is_default
        if body.is_default:
            # Exactly one STT default: clear the flag only on other STT models
            # (never touch the LLM models sharing this table).
            for other in db.scalars(select(AIModelConfig)).all():
                if other.key != key and other.key in valid_keys:
                    other.is_default = False

    db.commit()
    # The overrides cache covers this shared table — drop it so both the LLM
    # and STT reads see the fresh rows immediately.
    registry.invalidate_model_overrides()
    logger.info("Admin updated STT model key=%s enabled=%s default=%s",
                key, record.enabled, record.is_default)
    return {"models": list_stt_models(), "default": default_stt_key()}
