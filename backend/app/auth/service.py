"""Authentication business logic.

All user lookups/creations live here so the API layer stays thin. Every
function derives identity from the database, never from client input.
"""
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.password import dummy_verify, hash_password, needs_rehash, verify_password
from app.core.config import get_settings
from app.models.user import PasswordResetToken, RevokedSession, User

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == normalize_email(email)))


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def _hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def revoke_session_token(db: Session, token: str) -> None:
    """Deny-list a session token until it would have expired anyway."""
    now = _utcnow()
    db.execute(delete(RevokedSession).where(RevokedSession.expires_at < now))
    db.add(
        RevokedSession(
            token_hash=_hash_session_token(token),
            expires_at=now + timedelta(seconds=get_settings().session_max_age),
        )
    )
    try:
        db.commit()
    except IntegrityError:
        # Already revoked (e.g. logout clicked twice).
        db.rollback()


def is_session_token_revoked(db: Session, token: str) -> bool:
    return (
        db.scalar(
            select(RevokedSession.id).where(
                RevokedSession.token_hash == _hash_session_token(token)
            )
        )
        is not None
    )


def get_user_by_google_subject(db: Session, subject: str) -> User | None:
    return db.scalar(select(User).where(User.google_subject_id == subject))


def register_user(db: Session, email: str, password: str, name: str = "") -> User:
    """Create a local (email/password) account.

    Raises ``ValueError("exists")`` when the email is already registered. The
    API layer converts that into an enumeration-resistant response.
    """
    email = normalize_email(email)
    if get_user_by_email(db, email):
        raise ValueError("exists")
    user = User(
        email=email,
        password_hash=hash_password(password),
        name=(name or "").strip(),
        auth_provider="local",
        email_verified=False,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_local(db: Session, email: str, password: str) -> User | None:
    """Verify email/password. Returns the user or None.

    Always performs a hash verification (dummy when the user is missing) so the
    response time does not reveal whether the email exists.
    """
    user = get_user_by_email(db, email)
    if user is None or not user.password_hash:
        dummy_verify()
        return None
    if not verify_password(password, user.password_hash):
        return None
    if not user.is_active:
        return None
    # Opportunistically upgrade the hash if parameters changed.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = _utcnow()
    db.commit()
    db.refresh(user)
    return user


def upsert_google_user(
    db: Session,
    *,
    subject: str,
    email: str,
    email_verified: bool,
    name: str = "",
) -> User:
    """Create or link a Google identity.

    Linking rules (no duplicate accounts):
    1. If a user already has this Google ``sub``, return it.
    2. Else if a local account exists with the same verified email, link the
       Google identity onto that row (so both login methods share one account).
    3. Else create a new Google-only account (``password_hash = NULL``).
    """
    email = normalize_email(email)

    user = get_user_by_google_subject(db, subject)
    if user is not None:
        user.last_login_at = _utcnow()
        if name and not user.name:
            user.name = name
        db.commit()
        db.refresh(user)
        return user

    existing = get_user_by_email(db, email)
    if existing is not None:
        # Link the Google identity to the existing account.
        existing.google_subject_id = subject
        existing.email_verified = existing.email_verified or email_verified
        if name and not existing.name:
            existing.name = name
        existing.last_login_at = _utcnow()
        db.commit()
        db.refresh(existing)
        return existing

    user = User(
        email=email,
        password_hash=None,
        name=(name or "").strip(),
        auth_provider="google",
        google_subject_id=subject,
        email_verified=email_verified,
        is_active=True,
        last_login_at=_utcnow(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# --------------------------------------------------------------- password reset


def _hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_reset_token(db: Session, user: User) -> str:
    """Create a single-use, expiring reset token. Returns the RAW token.

    Only the SHA-256 hash is stored, so a DB leak cannot be used to reset
    passwords. Any previous unused tokens for the user are invalidated.
    """
    settings = get_settings()
    # Invalidate outstanding tokens for this user.
    for old in db.scalars(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        )
    ):
        old.used_at = _utcnow()

    raw = secrets.token_urlsafe(32)
    token = PasswordResetToken(
        user_id=user.id,
        token_hash=_hash_reset_token(raw),
        expires_at=_utcnow() + timedelta(seconds=settings.reset_token_max_age),
    )
    db.add(token)
    db.commit()
    return raw


def consume_reset_token(db: Session, raw_token: str, new_password: str) -> bool:
    """Validate + consume a reset token and set the new password.

    Returns True on success. The token is single-use and must be unexpired.
    """
    token_hash = _hash_reset_token(raw_token or "")
    row = db.scalar(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    )
    if row is None or not row.is_valid():
        return False
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        return False
    user.password_hash = hash_password(new_password)
    # A Google-only account that resets a password becomes a dual-provider
    # account; keep auth_provider as-is but it now has a local credential.
    row.used_at = _utcnow()
    db.commit()
    return True
