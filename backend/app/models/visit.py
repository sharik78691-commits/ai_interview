"""Visit log — powers the admin panel's "how many people visit" counters.

One row per HTTP request (health/docs noise excluded by the middleware).
Only aggregates are ever exposed; raw IPs are stored as salted SHA-256 hashes
so a database leak cannot be turned into a visitor list.
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VisitLog(Base):
    __tablename__ = "visit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # e.g. "/interview", "/api/auth/login". Query strings are never stored.
    path: Mapped[str] = mapped_column(String(500), index=True, nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False, default="GET")
    # Signed-in visitor, when the request carried a valid session cookie.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    # SHA-256(client_ip + session_secret): unique-visitor counting without
    # storing anything that identifies a person.
    ip_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )


__all__ = ["VisitLog"]
