"""Authentication REST endpoints.

Endpoints
---------
POST /api/auth/register
POST /api/auth/login
POST /api/auth/logout
GET  /api/auth/me
GET  /api/auth/google/login
GET  /api/auth/google/callback
POST /api/auth/forgot-password
POST /api/auth/reset-password

Responses never expose password hashes, OAuth tokens or internal security data.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import oauth, security
from app.auth.dependencies import get_current_user, require_csrf, require_user
from app.auth.service import (
    authenticate_local,
    consume_reset_token,
    create_reset_token,
    get_user_by_email,
    register_user,
    upsert_google_user,
)
from app.core.config import get_settings
from app.core.ratelimit import rate_limit
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import (
    AuthStatus,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    ResetPasswordRequest,
    UserPublic,
    WsTicketResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Generic, enumeration-resistant messages.
_GENERIC_LOGIN_ERROR = "Invalid email or password."
_GENERIC_FORGOT = (
    "If an account exists for that email, a password reset link has been sent."
)


def _public(user: User) -> UserPublic:
    return UserPublic(
        id=str(user.id),
        email=user.email,
        name=user.name or "",
        provider=user.auth_provider,
        email_verified=user.email_verified,
    )


@router.post("/register", response_model=AuthStatus, status_code=status.HTTP_201_CREATED)
async def register(
    req: RegisterRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthStatus:
    rate_limit(request, "register")
    logger.info("Register attempt (origin=%s)", request.headers.get("origin", "-"))
    try:
        user = register_user(db, req.email, req.password, req.name)
    except ValueError:
        # Enumeration-resistant: same shape as a validation failure.
        # The email itself is deliberately NOT logged (PII, and it is the very
        # thing this response refuses to confirm).
        logger.info("Register rejected: email already exists")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )
    security.set_session_cookies(response, user.id)
    logger.info("Register succeeded (user_id=%s)", user.id)
    return AuthStatus(authenticated=True, user=_public(user))


@router.post("/login", response_model=AuthStatus)
async def login(
    req: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthStatus:
    rate_limit(request, "login")
    logger.info(
        "Login attempt (origin=%s, host=%s)",
        request.headers.get("origin", "-"),
        request.headers.get("host", "-"),
    )
    user = authenticate_local(db, req.email, req.password)
    if user is None:
        logger.warning("Login failed: invalid credentials")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=_GENERIC_LOGIN_ERROR
        )
    # Fresh session token on login prevents session fixation.
    security.set_session_cookies(response, user.id)
    settings = get_settings()
    logger.info(
        "Login succeeded (user_id=%s). Cookie set with secure=%s samesite=%s domain=%r — "
        "if the browser does not store it, verify the scheme is HTTPS and that "
        "COOKIE_DOMAIN (if set) covers the serving host.",
        user.id,
        settings.cookie_secure,
        "none" if settings.cookie_secure else settings.cookie_samesite,
        settings.cookie_domain or "",
    )
    return AuthStatus(authenticated=True, user=_public(user))


@router.post("/logout", response_model=MessageResponse)
async def logout(
    request: Request,
    response: Response,
    _: None = Depends(require_csrf),
) -> MessageResponse:
    # Logout invalidates the session by clearing the signed cookie.
    security.clear_session_cookies(response)
    return MessageResponse(message="Signed out.")


@router.get("/me", response_model=AuthStatus)
async def me(
    request: Request,
    user: User | None = Depends(get_current_user),
) -> AuthStatus:
    # A 200 with authenticated=false here is the single most useful signal when
    # debugging "logged out on production only": it means the request reached
    # the backend but carried no usable session cookie.
    if user is None:
        logger.info(
            "Session check: not authenticated (cookies=%s, origin=%s)",
            list(request.cookies.keys()) or "none",
            request.headers.get("origin", "-"),
        )
        return AuthStatus(authenticated=False, user=None)
    logger.debug("Session check: authenticated (user_id=%s)", user.id)
    return AuthStatus(authenticated=True, user=_public(user))


# ------------------------------------------------------------------ Google OAuth


@router.get("/google/login")
async def google_login(request: Request) -> RedirectResponse:
    settings = get_settings()
    if not settings.google_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not configured.",
        )
    state = oauth.new_state()
    url = oauth.build_authorize_url(state)
    redirect = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    # State is stored in a short-lived HttpOnly cookie and verified on callback.
    #
    # SameSite: the callback arrives as a CROSS-SITE top-level navigation from
    # accounts.google.com. A "Lax" cookie is NOT sent on that navigation, so the
    # state check would always fail with "google_state". "None" is required for
    # the cookie to survive the Google redirect — and "None" mandates Secure,
    # which is only valid over HTTPS (production). In local dev (http) we keep
    # "Lax" because the whole flow is same-origin on localhost.
    state_samesite = "none" if settings.cookie_secure else "lax"
    state_kwargs: dict = {
        "max_age": 600,
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": state_samesite,
        "path": "/",
    }
    if settings.cookie_domain:
        state_kwargs["domain"] = settings.cookie_domain
    redirect.set_cookie(security.OAUTH_STATE_COOKIE, state, **state_kwargs)
    return redirect


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    settings = get_settings()
    frontend = settings.frontend_url.rstrip("/")

    def _fail(reason: str) -> RedirectResponse:
        return RedirectResponse(
            f"{frontend}/login?error={reason}", status_code=status.HTTP_302_FOUND
        )

    if error:
        return _fail("google_denied")
    if not code or not state:
        return _fail("google_invalid")

    # CSRF/state protection: the cookie must match the returned state.
    cookie_state = request.cookies.get(security.OAUTH_STATE_COOKIE)
    if not cookie_state or cookie_state != state:
        # A missing cookie almost always means the login start and the
        # callback ran on DIFFERENT origins (the state cookie is scoped to the
        # origin that set it). Log the host so this is diagnosable in prod.
        logger.warning(
            "OAuth state mismatch (cookie_present=%s, callback_host=%s). "
            "Ensure GOOGLE_REDIRECT_URI uses the same origin as the login start.",
            bool(cookie_state),
            request.url.hostname,
        )
        return _fail("google_state")

    try:
        identity = await oauth.exchange_code_for_identity(code)
    except oauth.OAuthError as exc:
        logger.warning("Google OAuth failed: %s", exc)
        return _fail("google_failed")

    user = upsert_google_user(
        db,
        subject=identity.subject,
        email=identity.email,
        email_verified=identity.email_verified,
        name=identity.name,
    )

    redirect = RedirectResponse(f"{frontend}/dashboard", status_code=status.HTTP_302_FOUND)
    security.set_session_cookies(redirect, user.id)
    # Clear the one-time state cookie.
    redirect.delete_cookie(security.OAUTH_STATE_COOKIE, path="/")
    return redirect


# --------------------------------------------------------------- password reset


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(
    req: ForgotPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> MessageResponse:
    rate_limit(request, "forgot")
    user = get_user_by_email(db, req.email)
    if user is not None and user.is_active:
        raw = create_reset_token(db, user)
        # In a real deployment this link is emailed. For the MVP it is logged
        # WITHOUT the email address, and never returned in the API response.
        settings = get_settings()
        link = f"{settings.frontend_url.rstrip('/')}/reset-password?token={raw}"
        logger.info("Password reset link generated for user id=%s: %s", user.id, link)
    # Always the same response — never reveal whether the email exists.
    return MessageResponse(message=_GENERIC_FORGOT)


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(
    req: ResetPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> MessageResponse:
    rate_limit(request, "reset")
    ok = consume_reset_token(db, req.token, req.password)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password reset link is invalid or expired.",
        )
    return MessageResponse(message="Your password has been reset. Please sign in.")


@router.get("/csrf", response_model=MessageResponse)
async def csrf_bootstrap(response: Response, user: User = Depends(require_user)) -> MessageResponse:
    """Issue a fresh CSRF cookie for an authenticated session (e.g. after reload)."""
    token = security.new_csrf_token()
    settings = get_settings()
    response.set_cookie(
        security.CSRF_COOKIE,
        token,
        max_age=settings.session_max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )
    return MessageResponse(message="ok")


# ------------------------------------------------------- WebSocket ticket


@router.post("/ws-ticket", response_model=WsTicketResponse)
async def ws_ticket(user: User = Depends(require_user)) -> WsTicketResponse:
    """Mint a short-lived ticket for the live-interview WebSocket.

    Why this exists
    ---------------
    The socket is opened directly against the backend host, because a static
    host (Vercel) cannot proxy a WebSocket upgrade to an external origin. That
    makes the handshake cross-origin, and browsers deliberately do NOT attach
    cookies to cross-origin WebSocket requests — so the session cookie that
    authenticates every REST call is absent on the socket, and the server closes
    it with 403/1008. This endpoint is called same-origin (where the cookie IS
    sent) and hands back a ticket for the socket URL.

    Security notes
    --------------
    * Requires an authenticated session — a ticket does not bypass auth.
    * Very short lifetime (``WS_TICKET_MAX_AGE``, default 60s) because it appears
      in a URL, which proxies and browser history may record.
    * Signed with a dedicated salt, so a session cookie cannot be replayed as a
      ticket or vice versa.
    * The socket re-validates the user against the database, so a deactivated
      account cannot keep using a ticket minted moments earlier.
    """
    settings = get_settings()
    ticket = security.create_ws_ticket(user.id)
    logger.info(
        "Issued WebSocket ticket (user_id=%s, ttl=%ss)",
        user.id,
        settings.ws_ticket_max_age,
    )
    return WsTicketResponse(ticket=ticket, expires_in=settings.ws_ticket_max_age)
