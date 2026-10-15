"""Fire-and-forget activity logging for the admin panel.

Every writer below inserts into the single ``activity_log`` table with its
OWN session and swallows every failure, so instrumenting a hot path (login,
prepare, heartbeats) can never break the request it measures. Call them
AFTER the main commit succeeds.

Privacy rules (non-negotiable):
- Raw IPs are NEVER stored — only a salted SHA-256 hash plus GeoIP labels.
- Click tracking records button/link labels only — never input values,
  passwords or keystrokes.
"""
import ipaddress
import logging
import time
import urllib.request

from app.db.session import SessionLocal
from app.models.activity_log import (
    ANALYZE,
    CLICK,
    LOGIN,
    PREPARE,
    REGISTER,
    STAY,
    VISIT,
    ActivityLog,
)

logger = logging.getLogger(__name__)

# ip -> (expires_epoch, country, region, city). Small installations see only
# a handful of distinct IPs; the cache keeps GeoIP lookups off the hot path.
_GEO_CACHE: dict[str, tuple[float, tuple[str, str, str]]] = {}
_GEO_TTL_SECONDS = 24 * 3600
_UNKNOWN_GEO = ("Unknown", "Unknown", "Unknown")


def client_ip(request) -> str:
    """Best-effort client IP (proxy-aware, like the rate limiter)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def ip_hash_for(ip: str, secret: str) -> str | None:
    if not ip or ip == "unknown":
        return None
    import hashlib

    return hashlib.sha256(f"{ip}|{secret}".encode()).hexdigest()


def _is_local(ip: str) -> bool:
    if ip in ("127.0.0.1", "::1", "localhost", "unknown", ""):
        return True
    try:
        return ipaddress.ip_address(ip.split("%")[0]).is_private
    except ValueError:
        return False


def geo_for_ip(ip: str) -> tuple[str, str, str]:
    """(country, region, city) for an IP — cached, never raises.

    Local/private addresses resolve instantly without any network call.
    Public IPs go through the free ip-api.com tier (45 req/min) with a short
    timeout; anything failing degrades to Unknown.
    """
    if _is_local(ip):
        return ("Local", "Local", "Local")
    now = time.time()
    cached = _GEO_CACHE.get(ip)
    if cached and cached[0] > now:
        return cached[1]
    geo = _UNKNOWN_GEO
    try:
        req = urllib.request.Request(
            f"http://ip-api.com/json/{ip}?fields=status,country,regionName,city",
            headers={"User-Agent": "OyeInterview/1.0"},
        )
        with urllib.request.urlopen(req, timeout=2) as res:
            import json

            data = json.loads(res.read().decode() or "{}")
            if data.get("status") == "success":
                geo = (
                    str(data.get("country") or "Unknown")[:100],
                    str(data.get("regionName") or "Unknown")[:100],
                    str(data.get("city") or "Unknown")[:100],
                )
    except Exception as exc:
        logger.debug("GeoIP lookup failed for %s (%s)", ip, exc)
    _GEO_CACHE[ip] = (now + _GEO_TTL_SECONDS, geo)
    return geo


def _secret() -> str:
    try:
        from app.core.config import get_settings

        return get_settings().session_secret or "local"
    except Exception:
        return "local"


def _write(**fields) -> None:
    db = SessionLocal()
    try:
        db.add(ActivityLog(**fields))
        db.commit()
    except Exception as exc:
        logger.warning("Activity logging failed (%s)", exc)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


def _geo(ip: str | None, resolve: bool = True) -> tuple[str | None, tuple[str, str, str] | None]:
    if not ip:
        return None, None
    hash_ = ip_hash_for(ip, _secret())
    return hash_, geo_for_ip(ip) if resolve else None


def log_event(name: str, user_id: int | None = None, ip: str | None = None) -> None:
    """Record a named action (login/register/prepare/analyze). Never raises."""
    hash_, geo = _geo(ip)
    _write(
        kind=name, user_id=user_id, ip_hash=hash_,
        country=geo[0] if geo else None,
        region=geo[1] if geo else None,
        city=geo[2] if geo else None,
    )


def log_visit(
    user_id: int | None,
    ip: str | None,
    path: str,
    method: str,
    user_agent: str | None,
) -> None:
    """Record one page/API visit. Never raises."""
    hash_, geo = _geo(ip)
    _write(
        kind=VISIT, user_id=user_id, ip_hash=hash_,
        country=geo[0] if geo else None,
        region=geo[1] if geo else None,
        city=geo[2] if geo else None,
        path=(path or "")[:500] or None,
        method=(method or "")[:10] or None,
        user_agent=(user_agent or "")[:300] or None,
    )


def log_page_time(
    user_id: int | None, ip: str | None, path: str, seconds: int
) -> None:
    """Record one dwell-time heartbeat (clamped). Never raises."""
    path = (path or "")[:500]
    try:
        seconds = max(0, min(600, int(seconds)))
    except (TypeError, ValueError):
        return
    if not path or seconds <= 0:
        return
    # Stays/clicks skip GeoIP: ip_hash alone joins them to visits for location.
    hash_, _ = _geo(ip, resolve=False)
    _write(kind=STAY, user_id=user_id, ip_hash=hash_, path=path, seconds=seconds)


def log_clicks(
    user_id: int | None, ip: str | None, events: list[dict]
) -> None:
    """Record a batch of button/link presses (capped). Never raises."""
    # Clicks skip GeoIP (see log_page_time); ip_hash joins them to visits.
    hash_, _ = _geo(ip, resolve=False)
    for e in (events or [])[:50]:
        if not isinstance(e, dict):
            continue
        path = str(e.get("path") or "")[:500]
        if not path:
            continue
        _write(
            kind=CLICK, user_id=user_id, ip_hash=hash_, path=path,
            label=str(e.get("label") or "")[:120] or None,
            target=str(e.get("target") or "")[:120] or None,
        )


__all__ = [
    "client_ip",
    "geo_for_ip",
    "ip_hash_for",
    "log_event",
    "log_visit",
    "log_page_time",
    "log_clicks",
    "VISIT",
    "STAY",
    "CLICK",
    "LOGIN",
    "REGISTER",
    "PREPARE",
    "ANALYZE",
]
