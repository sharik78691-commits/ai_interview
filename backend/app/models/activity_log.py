"""Unified activity log — ONE table replacing visit_logs, events, page_times
and click_events.

Kinds: ``visit`` (every request), ``stay`` (dwell-time heartbeat),
``click`` (button/link press), ``login`` / ``register`` (auth),
``prepare`` (interview prepared), ``analyze`` (question answered).
Only the columns a kind needs are filled; the rest stay NULL.

Privacy rules (non-negotiable): raw IPs are NEVER stored (salted hash +
GeoIP labels only); clicks record button labels, never input values.
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

VISIT = "visit"
STAY = "stay"
CLICK = "click"
LOGIN = "user_login"
REGISTER = "user_registered"
PREPARE = "interview_prepared"
ANALYZE = "question_analyzed"

# Backwards-compatible aliases so existing call sites barely change.
USER_LOGIN = LOGIN
USER_REGISTERED = REGISTER
INTERVIEW_PREPARED = PREPARE
QUESTION_ANALYZED = ANALYZE


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    path: Mapped[str | None] = mapped_column(String(500), index=True, nullable=True)
    method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    target: Mapped[str | None] = mapped_column(String(120), nullable=True)
    seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )


__all__ = [
    "ActivityLog",
    "VISIT",
    "STAY",
    "CLICK",
    "LOGIN",
    "REGISTER",
    "PREPARE",
    "ANALYZE",
    "USER_LOGIN",
    "USER_REGISTERED",
    "INTERVIEW_PREPARED",
    "QUESTION_ANALYZED",
]
