"""Append-only activity events — powers admin "how much" trends.

One row per interesting action (``user_login``, ``interview_prepared``,
``question_analyzed``, ...). Writers must NEVER fail the request they
instrument: see ``app.services.activity.log_event``.

``user_id`` is a plain nullable integer (deliberately NOT a foreign key) so
deleting a user can never be blocked by its own history rows.
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Event names in use. New features add names here — never rename old ones,
# the admin charts aggregate by exact string.
USER_REGISTERED = "user_registered"
USER_LOGIN = "user_login"
INTERVIEW_PREPARED = "interview_prepared"
QUESTION_ANALYZED = "question_analyzed"


class EventLog(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )


__all__ = [
    "EventLog",
    "USER_REGISTERED",
    "USER_LOGIN",
    "INTERVIEW_PREPARED",
    "QUESTION_ANALYZED",
]
