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
from app.services.activity import log_event

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == normalize_email(email)))


def get_user_by_login(db: Session, identifier: str) -> User | None:
    """Find a user by email OR username (the login box accepts both).

    Email is tried first; usernames are matched case-insensitively. Returns
    None for blank input without touching the database.
    """
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    user = get_user_by_email(db, identifier)
    if user is not None:
        return user
    return db.scalar(select(User).where(User.username == identifier.lower()))


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
        # Registering signs the user straight in — that first session counts.
        login_count=1,
        last_login_at=_utcnow(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    log_event("user_registered", user.id)
    return user


def authenticate_local(db: Session, identifier: str, password: str) -> User | None:
    """Verify email-or-username + password. Returns the user or None.

    Always performs a hash verification (dummy when the user is missing) so the
    response time does not reveal whether the identifier exists.
    """
    user = get_user_by_login(db, identifier)
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
    user.login_count = (user.login_count or 0) + 1
    db.commit()
    db.refresh(user)
    log_event("user_login", user.id)
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
        user.login_count = (user.login_count or 0) + 1
        if name and not user.name:
            user.name = name
        db.commit()
        db.refresh(user)
        log_event("user_login", user.id)
        return user

    existing = get_user_by_email(db, email)
    if existing is not None:
        # Link the Google identity to the existing account.
        existing.google_subject_id = subject
        existing.email_verified = existing.email_verified or email_verified
        if name and not existing.name:
            existing.name = name
        existing.last_login_at = _utcnow()
        existing.login_count = (existing.login_count or 0) + 1
        db.commit()
        db.refresh(existing)
        log_event("user_login", existing.id)
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
        login_count=1,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    log_event("user_login", user.id)
    return user


# --------------------------------------------------------------- admin seed


def ensure_admin(db: Session) -> User | None:
    """Create the admin login on first startup (idempotent).

    Credentials come ONLY from the environment (``ADMIN_EMAIL`` /
    ``ADMIN_USERNAME`` / ``ADMIN_PASSWORD``) — the password is never hardcoded.
    When ``ADMIN_PASSWORD`` is unset, seeding is skipped with a warning and an
    existing admin is left untouched. Safe to call on every startup.
    """
    settings = get_settings()
    password = settings.admin_password
    if not password:
        logger.warning(
            "ADMIN_PASSWORD is not set — admin login NOT seeded. Set it in "
            "backend/.env (local) or host env vars (production)."
        )
        return None
    email = normalize_email(settings.admin_email)
    username = (settings.admin_username or "").strip().lower()
    admin = get_user_by_email(db, email)
    if admin is None and username:
        admin = db.scalar(select(User).where(User.username == username))
    if admin is not None:
        # Promote / repair a pre-existing row without touching its password.
        changed = False
        if not admin.is_admin:
            admin.is_admin = True
            changed = True
        if not admin.is_active:
            admin.is_active = True
            changed = True
        if username and not admin.username:
            admin.username = username
            changed = True
        if changed:
            db.commit()
            db.refresh(admin)
        return admin
    admin = User(
        email=email,
        username=username or None,
        password_hash=hash_password(password),
        name=settings.admin_name or "Administrator",
        auth_provider="local",
        email_verified=True,
        is_active=True,
        is_admin=True,
        login_count=0,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    logger.info("Admin login seeded (%s / %s)", username, email)
    return admin


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

