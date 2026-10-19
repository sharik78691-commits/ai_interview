"""In-process, dependency-free rate limiting.

A fixed-window counter keyed by bucket + caller. This is intentionally
lightweight for a single-process Web MVP (no Redis). It protects the auth
endpoints against brute force and the AI endpoints against cost abuse.

Client IP
---------
``X-Forwarded-For`` is a client-writable header: only the entries APPENDED by
proxies we control can be trusted. ``TRUSTED_PROXY_COUNT`` says how many such
proxies sit in front of the app; the client IP is then the entry that many
places from the right. ``0`` (default) keeps the legacy left-most behaviour so
an existing deployment is never accidentally collapsed into one shared bucket.

Limits are per process: with N workers the effective limit is N x limit.
"""
import logging
import math
import threading
import time

from fastapi import HTTPException, Request, status

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 60
# Sweep expired windows once the table grows past this many keys, so a flood
# of distinct (or spoofed) callers cannot grow memory without bound.
_SWEEP_THRESHOLD = 10_000

# key -> (window_start_epoch, count)
_buckets: dict[str, tuple[float, int]] = {}
_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return peer
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    if not hops:
        return peer
    trusted = get_settings().trusted_proxy_count
    if trusted <= 0:
        return hops[0]
    # Same semantics as Werkzeug's ProxyFix(x_for=N): each trusted proxy
    # appends the address it received the request from, so the client is the
    # N-th entry from the right; anything further left is client-supplied.
    index = len(hops) - trusted
    return hops[index] if 0 <= index < len(hops) else hops[0]


def _sweep(now: float) -> None:
    expired = [k for k, (start, _) in _buckets.items() if now - start >= _WINDOW_SECONDS]
    for k in expired:
        del _buckets[k]


def _hit(key: str, limit: int) -> bool:
    """Count one request for ``key``; True when it is over ``limit``."""
    now = time.time()
    with _lock:
        if len(_buckets) >= _SWEEP_THRESHOLD:
            _sweep(now)
        window_start, count = _buckets.get(key, (0.0, 0))
        if now - window_start >= _WINDOW_SECONDS:
            window_start, count = now, 0
        count += 1
        _buckets[key] = (window_start, count)
    return count > limit


def retry_after_seconds(key: str) -> int:
    """Seconds until ``key``'s current window resets (at least 1)."""
    with _lock:
        window_start, _ = _buckets.get(key, (0.0, 0))
    remaining = _WINDOW_SECONDS - (time.time() - window_start)
    return max(1, min(_WINDOW_SECONDS, math.ceil(remaining)))


def _reject(key: str) -> None:
    logger.warning("Rate limit exceeded for bucket %s", key.split(":", 1)[0])
    # Retry-After tells well-behaved clients exactly when to try again instead
    # of hammering the endpoint (and extending the lockout).
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many attempts. Please try again later.",
        headers={"Retry-After": str(retry_after_seconds(key))},
    )


def rate_limit(request: Request, bucket: str, limit: int | None = None) -> None:
    """Raise 429 when the caller's IP exceeds ``limit`` requests per minute."""
    limit = limit or get_settings().rate_limit_per_minute
    key = f"{bucket}:{_client_ip(request)}"
    if _hit(key, limit):
        _reject(key)


def rate_limit_key(bucket: str, subject: str, limit: int) -> None:
    """Raise 429 when ``subject`` (user id, account name) exceeds ``limit``/min.

    Independent of the caller's IP, so rotating or spoofing addresses does not
    reset it — used to throttle brute force against ONE account and AI spend
    by ONE user.
    """
    key = f"{bucket}:{subject}"
    if _hit(key, limit):
        _reject(key)


def reset_rate_limits() -> None:
    """Test helper: clear all counters."""
    with _lock:
        _buckets.clear()
