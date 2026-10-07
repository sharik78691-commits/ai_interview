"""User + password-reset-token ORM models.

Design notes
------------
* ``password_hash`` is NULL for Google-only accounts and an Argon2id hash for
  local accounts. A single account can support BOTH providers: the Google
  identity is linked by ``google_subject_id`` on the same row, so signing in
  with Google and with email/password never creates duplicate accounts.
* ``google_subject_id`` is the stable Google ``sub`` claim (never the email,
  which can change). It is unique when present.
* No Google password is ever stored.
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    # NULL for Google-only accounts; Argon2id hash for local accounts.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    # "local" | "google" — the provider used at first sign-up. An account may
    # still be linked to the other provider later (see google_subject_id).
    auth_provider: Mapped[str] = mapped_column(String(20), default="local", nullable=False)
    # Google's stable subject identifier ("sub"). Unique when set.
    google_subject_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True, nullable=True
    )
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    reset_tokens: Mapped[list["PasswordResetToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def has_password(self) -> bool:
        return bool(self.password_hash)


class PasswordResetToken(Base):
    """Single-use, expiring password-reset token.

    Only a SHA-256 hash of the token is stored, so a database leak does not
    expose usable reset links.
    """

    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped[User] = relationship(back_populates="reset_tokens")

    def is_valid(self, now: datetime | None = None) -> bool:
        now = now or _utcnow()
        expires = self.expires_at
        # SQLite returns naive datetimes; normalise before comparing.
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return self.used_at is None and expires > now


class RevokedSession(Base):
    """Session cookie invalidated by logout.

    Session tokens are signed and stateless, so without this list a copied
    cookie would stay valid until it expires. Only a SHA-256 hash is stored.
    """

    __tablename__ = "revoked_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)


# Re-exported for convenience in tests / services.
__all__ = ["User", "PasswordResetToken", "RevokedSession", "Text"]
