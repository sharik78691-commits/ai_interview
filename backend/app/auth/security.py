"""Session + CSRF token handling and cookie helpers.

Design
------
* Authentication is a **server-managed session** carried in a signed,
  ``HttpOnly`` cookie. No long-lived token is ever exposed to JavaScript, so
  nothing sensitive lives in ``localStorage``/``sessionStorage``.
* The session cookie is signed with ``SESSION_SECRET`` (itsdangerous). The
  payload is only ``{uid, iat}`` — the server re-validates the user on every
  request, so a stolen cookie can be revoked by deactivating the user.
* A separate, readable ``csrf_token`` cookie (double-submit pattern) must be
  echoed in the ``X-CSRF-Token`` header for state-changing requests. Because
  the session cookie is ``SameSite=Lax`` and the CSRF token is required on
  POST/PUT/PATCH/DELETE, cross-site form posts cannot forge authenticated
  actions.
"""
import hmac
import logging
import secrets
from datetime import datetime, timezone

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import get_settings

logger = logging.getLogger(__name__)

SESSION_COOKIE = "aia_session"
CSRF_COOKIE = "aia_csrf"
CSRF_HEADER = "x-csrf-token"
OAUTH_STATE_COOKIE = "aia_oauth_state"

# Salt for the short-lived WebSocket ticket. A different salt means a session
# token can never be replayed as a ticket (or vice versa), even though both are
# signed with the same secret.
_WS_TICKET_SALT = "aia-ws-ticket"

# Methods that mutate state and therefore require a CSRF token.
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _secret() -> str:
    settings = get_settings()
    # The fallback keeps local development working, but it is shared by every
    # install, so production MUST set SESSION_SECRET (warned at startup).
    return settings.session_secret or "dev-insecure-session-secret-change-me"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key=_secret(), salt="aia-session")


def create_session_token(user_id: int) -> str:
    """Sign a minimal session payload. The user is re-checked on each request."""
    return _serializer().dumps({"uid": user_id, "iat": int(datetime.now(timezone.utc).timestamp())})


def read_session_token(token: str) -> int | None:
    """Return the user id from a valid, unexpired session token, else None."""
    settings = get_settings()
    try:
        data = _serializer().loads(token, max_age=settings.session_max_age)
    except SignatureExpired:
        logger.info("Session token rejected: expired")
        return None
    except BadSignature:
        logger.warning(
            "Session token rejected: bad signature. This usually means "
            "SESSION_SECRET changed (all existing sessions are invalidated)."
        )
        return None
    except Exception:  # pragma: no cover - defensive
        logger.exception("Session token rejected: unexpected error")
        return None
    uid = data.get("uid") if isinstance(data, dict) else None
    return int(uid) if isinstance(uid, int) else None


# --------------------------------------------------------------- WebSocket auth


def create_ws_ticket(user_id: int) -> str:
    """Mint a very short-lived ticket for the cross-origin WebSocket.

    The live-interview socket is opened directly against the backend host, so
    the browser treats it as a different origin and does NOT attach the session
    cookie. The ticket travels in the query string instead.

    It is deliberately short-lived and single-purpose: URLs leak into proxy and
    browser logs far more readily than headers.
    """
    return URLSafeTimedSerializer(secret_key=_secret(), salt=_WS_TICKET_SALT).dumps(
        {"uid": user_id, "iat": int(datetime.now(timezone.utc).timestamp())}
    )


def read_ws_ticket(token: str) -> int | None:
    """Validate a WebSocket ticket and return its user id (else None)."""
    settings = get_settings()
    try:
        data = URLSafeTimedSerializer(secret_key=_secret(), salt=_WS_TICKET_SALT).loads(
            token, max_age=settings.ws_ticket_max_age
        )
    except SignatureExpired:
        logger.warning(
            "WebSocket ticket rejected: expired (max_age=%ss). The client should "
            "request a fresh ticket immediately before opening the socket.",
            settings.ws_ticket_max_age,
        )
        return None
    except BadSignature:
        logger.warning("WebSocket ticket rejected: bad signature")
        return None
    except Exception:  # pragma: no cover - defensive
        logger.exception("WebSocket ticket rejected: unexpected error")
        return None
    uid = data.get("uid") if isinstance(data, dict) else None
    return int(uid) if isinstance(uid, int) else None


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_tokens_match(cookie_value: str | None, header_value: str | None) -> bool:
    if not cookie_value or not header_value:
        return False
    return hmac.compare_digest(cookie_value, header_value)


def _cookie_kwargs(max_age: int) -> dict:
    settings = get_settings()
    # SameSite=None is required for cross-origin WebSocket auth in production,
    # where the frontend (www.oyeinterview.com) connects to the backend
    # (ai-interview-1-309j.onrender.com). None requires Secure (HTTPS).
    samesite = "none" if settings.cookie_secure else settings.cookie_samesite
    kwargs: dict = {
        "max_age": max_age,
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": samesite,
        "path": "/",
    }
    if settings.cookie_domain:
        kwargs["domain"] = settings.cookie_domain
    
    logger.debug(f"Setting cookie with: secure={kwargs['secure']}, samesite={kwargs['samesite']}, domain={kwargs.get('domain', 'None')}")
    return kwargs


def set_session_cookies(response: Response, user_id: int) -> str:
    """Issue the session + CSRF cookies. Returns the CSRF token.

    Session fixation is prevented by always minting a fresh session token on
    login (the caller never reuses a pre-login token).
    """
    settings = get_settings()
    token = create_session_token(user_id)
    csrf = new_csrf_token()

    response.set_cookie(SESSION_COOKIE, token, **_cookie_kwargs(settings.session_max_age))
    # CSRF cookie is intentionally readable by JS (double-submit pattern).
    csrf_kwargs = _cookie_kwargs(settings.session_max_age)
    csrf_kwargs["httponly"] = False
    response.set_cookie(CSRF_COOKIE, csrf, **csrf_kwargs)
    return csrf


def clear_session_cookies(response: Response) -> None:
    # Deletion must repeat the attributes used when setting the cookie;
    # otherwise browsers can ignore it (notably SameSite=None in production).
    for name, httponly in ((SESSION_COOKIE, True), (CSRF_COOKIE, False)):
        kwargs = _cookie_kwargs(0)
        kwargs.pop("max_age")
        kwargs["httponly"] = httponly
        response.delete_cookie(name, **kwargs)


def get_session_user_id(request: Request) -> int | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return read_session_token(token)


def csrf_ok(request: Request) -> bool:
    """Validate the double-submit CSRF token for unsafe methods."""
    if request.method.upper() not in UNSAFE_METHODS:
        return True
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    return csrf_tokens_match(cookie, header)
