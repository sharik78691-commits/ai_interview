"""Security headers + request logging middleware.

Adds production-appropriate headers without breaking microphone / tab-audio
functionality (``Permissions-Policy`` explicitly allows microphone and
display-capture for same-origin use), and records every request with enough
context to diagnose cross-origin / auth problems.
"""
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Paths that would otherwise flood the log stream with health-check noise.
_QUIET_PATHS = {"/api/health", "/health", "/favicon.ico"}


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log one line per request: method, path, status, duration, origin.

    This middleware is added LAST in ``main.py`` so it wraps ``CORSMiddleware``
    and therefore still logs requests that CORS rejects outright (a rejected
    preflight never reaches the route, which previously produced a silent
    browser-side CORS error with no matching server log).
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = uuid.uuid4().hex[:8]
        start = time.perf_counter()
        origin = request.headers.get("origin")
        is_ws = request.headers.get("upgrade", "").lower() == "websocket"
        path = request.url.path
        quiet = path in _QUIET_PATHS

        if not quiet:
            logger.info(
                "[%s] --> %s %s origin=%s host=%s ws_upgrade=%s",
                request_id,
                request.method,
                path,
                origin or "-",
                request.headers.get("host", "-"),
                is_ws,
            )

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            # An unhandled exception is exactly the case where the request id
            # in this line lets the traceback be matched to the client error.
            logger.exception(
                "[%s] <!! %s %s failed after %.1fms",
                request_id,
                request.method,
                path,
                duration_ms,
            )
            raise

        duration_ms = (time.perf_counter() - start) * 1000
        settings = get_settings()

        if not quiet:
            level = logging.INFO
            if response.status_code >= 500:
                level = logging.ERROR
            elif response.status_code >= 400:
                level = logging.WARNING

            logger.log(
                level,
                "[%s] <-- %s %s -> %s (%.1fms)",
                request_id,
                request.method,
                path,
                response.status_code,
                duration_ms,
            )

        # Make refusals explicit: a 4xx on a preflighted request is otherwise
        # indistinguishable from a business-logic rejection in the log.
        if response.status_code in (401, 403) and origin and not quiet:
            allowed = origin in settings.cors_origins
            logger.warning(
                "[%s] %s %s returned %s for origin=%s (origin_allowed_by_cors=%s). "
                "If this is unexpected, check CORS_ORIGINS and the session cookie.",
                request_id,
                request.method,
                path,
                response.status_code,
                origin,
                allowed,
            )

        response.headers["X-Request-ID"] = request_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        settings = get_settings()

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        # Auth responses describe who is signed in; never let a browser or
        # proxy reuse them after logout.
        if request.url.path.startswith("/api/auth/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")

        # Allow the app's own origin to use the microphone and share tab audio.
        response.headers.setdefault(
            "Permissions-Policy",
            "microphone=(self), display-capture=(self), camera=()",
        )

        # CSP: the API serves JSON/redirects, so a locked-down policy is safe.
        # frame-ancestors 'none' is the modern equivalent of X-Frame-Options.
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        )

        # HSTS only makes sense over HTTPS (production).
        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response
