"""Stored target roles — one row per saved position + job description.

Mirrors the resume library: a user keeps several roles, exactly one is
active, and interviews consume the active one's job description. Deleting a
user cascades to their roles.
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TargetRole(Base):
    __tablename__ = "target_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Owner's email at save time (denormalized for admin views/exports).
    user_email: Mapped[str] = mapped_column(String(320), index=True, nullable=False, default="")
    # Job title, e.g. "Senior Backend Engineer". May be empty (JD-only role).
    position: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # Full job description text (editable — feeds interview preparation).
    job_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Exactly one role per user is active: it feeds interview preparation.
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


__all__ = ["TargetRole"]
