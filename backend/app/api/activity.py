"""Product-analytics ingestion (no auth required — guests are counted too).

POST /api/activity/page-time — one dwell-time heartbeat {path, seconds}
POST /api/activity/clicks    — a batch of button/link presses {events: [...]}

Writes are fire-and-forget server-side and can never fail the caller's
request. A generous rate limit (120/min) stops runaway loops from flooding
the tables while normal browsing stays far below it.
"""
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth import security
from app.core.ratelimit import rate_limit
from app.schemas.activity import ClickBatchIn, PageTimeIn
from app.services.activity import client_ip, log_clicks, log_page_time

logger = logging.getLogger(__name__)

router = APIRouter()


def _visitor(request: Request) -> tuple[int | None, str | None]:
    """(user_id, ip) without touching the database."""
    token = request.cookies.get(security.SESSION_COOKIE)
    user_id = security.read_session_token(token) if token else None
    return user_id, client_ip(request)


@router.post("/activity/page-time")
async def page_time(
    body: PageTimeIn, request: Request
) -> dict:
    rate_limit(request, "activity", limit=120)
    user_id, ip = _visitor(request)
    log_page_time(user_id, ip, body.path, body.seconds)
    return {"ok": True}


@router.post("/activity/clicks")
async def clicks(
    body: ClickBatchIn, request: Request
) -> dict:
    rate_limit(request, "activity", limit=120)
    user_id, ip = _visitor(request)
    log_clicks(
        user_id, ip,
        [{"path": e.path, "label": e.label, "target": e.target} for e in body.events],
    )
    return {"ok": True}
