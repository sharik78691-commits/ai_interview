"""Stored resumes — one row per upload, owned by exactly one user.

Today's upload flow only returned parsed data to the browser (localStorage);
this table makes resumes permanent, multi-device and manageable: list them,
pick the active one for interviews, re-download the original file, delete
old versions. Deleting a user cascades to their resumes.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResumeRecord(Base):
    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Owner's email at upload time (denormalized for admin views/exports —
    # no join needed to tell who a resume belongs to).
    user_email: Mapped[str] = mapped_column(String(320), index=True, nullable=False, default="")
    # Original upload name, e.g. "Rahul_Sharma.pdf".
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    # Lowercase extension with dot: ".pdf" | ".docx" | ".txt".
    extension: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Exact uploaded bytes so the original file can be re-downloaded.
    file_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Extracted plain text (editable — the source of truth for interviews).
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # JSON string of the parsed ResumeData (skills, experience, ...).
    parsed_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # Exactly one resume per user is active: it feeds interview preparation.
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


__all__ = ["ResumeRecord"]
