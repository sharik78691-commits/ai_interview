"""Admin-editable AI model configuration.

Stores ONLY safe, non-secret overrides: enabled flag, default marker, display
name, description, free-tier label and ordering priority. The provider model
id, endpoint and API key live in backend env vars — never here, never in the
browser. ``create_all`` on startup creates this table automatically.
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AIModelConfig(Base):
    __tablename__ = "ai_model_config"

    # The registry key, e.g. "groq-gpt-oss" — NOT the provider model id.
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Admin display override ("" = use the built-in label).
    display_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Admin override of the free-tier flag (None = use built-in value).
    free_tier: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


__all__ = ["AIModelConfig"]
