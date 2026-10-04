"""In-process, dependency-free rate limiting.

A fixed-window counter keyed by client IP + route. This is intentionally
lightweight for a single-process Web MVP (no Redis). It protects the auth
endpoints against brute force and abuse. Behind a proxy, set the real client IP
via ``X-Forwarded-For`` (uvicorn ``--proxy-headers``).
"""
import logging
import time
from collections import defaultdict

from fastapi import HTTPException, Request, status

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# key -> (window_start_epoch, count)
_buckets: dict[str, tuple[float, int]] = defaultdict(lambda: (0.0, 0))


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request, bucket: str, limit: int | None = None) -> None:
    """Raise 429 when the caller exceeds ``limit`` requests per minute."""
    settings = get_settings()
    limit = limit or settings.rate_limit_per_minute
    key = f"{bucket}:{_client_ip(request)}"
    now = time.time()
    window_start, count = _buckets[key]
    if now - window_start >= 60:
        window_start, count = now, 0
    count += 1
    _buckets[key] = (window_start, count)
    if count > limit:
        logger.warning("Rate limit exceeded for %s", key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Please try again later.",
        )


def reset_rate_limits() -> None:
    """Test helper: clear all counters."""
    _buckets.clear()
