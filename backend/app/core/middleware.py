"""Security headers middleware.

Adds production-appropriate headers without breaking microphone / tab-audio
functionality (``Permissions-Policy`` explicitly allows microphone and
display-capture for same-origin use).
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        settings = get_settings()

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
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
