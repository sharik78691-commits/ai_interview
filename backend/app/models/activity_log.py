"""Unified activity log — ONE table for business events + AI usage.

Kinds: ``user_login`` / ``user_registered`` (auth), ``interview_prepared``,
``question_analyzed`` (engagement), and ``llm_usage`` (AI cost/reliability).

Only what a business owner needs is stored: WHO (user_id) did WHAT (kind)
and WHEN (created_at), plus LLM usage telemetry. Page analytics (per-request
visits, button clicks, dwell time, GeoIP, device) were removed — they were
high-volume noise, not signal.
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

LOGIN = "user_login"
REGISTER = "user_registered"
PREPARE = "interview_prepared"
ANALYZE = "question_analyzed"
# LLM usage row: one per answer-generation call (provider/model/latency/tokens).
LLM_USAGE = "llm_usage"

# Backwards-compatible aliases so existing call sites barely change.
USER_LOGIN = LOGIN
USER_REGISTERED = REGISTER
INTERVIEW_PREPARED = PREPARE
QUESTION_ANALYZED = ANALYZE


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ActivityLog(Base):
    __tablename__ = "activity_log"
    # Serves "kind = X AND created_at BETWEEN ..." (admin dashboard). Existing
    # databases get it from db.session._ensure_activity_indexes().
    __table_args__ = (Index("ix_activity_log_kind_created_at", "kind", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    # Email snapshot at event time ('' for anonymous rows); filled by
    # app.db.email_snapshot, never rewritten when the user changes email.
    user_email: Mapped[str] = mapped_column(String(320), index=True, default="", nullable=False)
    # --- LLM usage tracking (kind = "llm_usage") ---
    # provider key + model id actually used (never secrets).
    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # e.g. "ok", "rate_limit", "quota", "auth", "not_found", "network", "timeout"
    error_category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    request_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )


__all__ = [
    "ActivityLog",
    "LOGIN",
    "REGISTER",
    "PREPARE",
    "ANALYZE",
    "LLM_USAGE",
    "USER_LOGIN",
    "USER_REGISTERED",
    "INTERVIEW_PREPARED",
    "QUESTION_ANALYZED",
]
