"""FastAPI dependencies for authentication.

``get_current_user`` derives identity **only** from the signed session cookie
and re-validates the user against the database on every request. Client-supplied
ids are never trusted for authorization.
"""
import logging

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth import security
from app.auth.service import get_user_by_id, is_session_token_revoked
from app.core.config import get_settings
from app.core.ratelimit import rate_limit_key
from app.db.session import get_db
from app.models.user import User

logger = logging.getLogger(__name__)


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User | None:
    """Return the authenticated user or None (does not raise)."""
    token = request.cookies.get(security.SESSION_COOKIE)
    payload = security.read_session_payload(token) if token else None
    if payload is None:
        return None
    user_id, issued_at = payload
    if is_session_token_revoked(db, token):
        return None
    user = get_user_by_id(db, user_id)
    if user is None or not user.is_active:
        return None
    if security.session_issued_before_revocation(issued_at, user.sessions_revoked_at):
        return None
    return user


def require_user(user: User | None = Depends(get_current_user)) -> User:
    """Require an authenticated, active user; otherwise 401."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session has expired. Please sign in again.",
        )
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    """Require an admin user: 401 when signed out, 403 when not an admin."""
    if not user.is_admin:
        logger.warning("Forbidden admin access attempt (user_id=%s)", user.id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return user


def require_admin_rate_limited(user: User = Depends(require_admin)) -> User:
    """Admin + per-admin-user rate limit (ADMIN_RATE_LIMIT_PER_MINUTE).

    Keyed by user id, not IP: admins behind one office/NAT IP do not share a
    budget, and the login brute-force limit (RATE_LIMIT_PER_MINUTE) stays
    separate from normal dashboard use.
    """
    rate_limit_key("admin", str(user.id), get_settings().admin_rate_limit_per_minute)
    return user


def require_csrf(request: Request) -> None:
    """Enforce the double-submit CSRF token on state-changing requests."""
    if not security.csrf_ok(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing CSRF token.",
        )


def require_user_csrf(
    user: User = Depends(require_user),
    _: None = Depends(require_csrf),
) -> User:
    """Authenticated user + CSRF check, for protected mutating endpoints."""
    return user
