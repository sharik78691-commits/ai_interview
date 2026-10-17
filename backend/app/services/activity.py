"""Fire-and-forget business-event + AI-usage logging for the admin panel.

Every writer below inserts into the single ``activity_log`` table with its
OWN session and swallows every failure, so instrumenting a hot path (login,
prepare, AI call) can never break the request it measures. Call them AFTER
the main commit succeeds.

Removed (business-owner lean cut): per-request visit logging, button-click
tracking, dwell-time heartbeats, GeoIP, and raw-IP/user-agent capture.
"""
import logging

from app.db.session import SessionLocal
from app.models.activity_log import (
    ActivityLog,
    LLM_USAGE,
)

logger = logging.getLogger(__name__)


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


def log_event(name: str, user_id: int | None = None) -> None:
    """Record a named business event (login/register/prepare/analyze). Never raises."""
    _write(kind=name, user_id=user_id)


def log_llm_usage(
    user_id: int | None,
    request_type: str,
    provider: str | None,
    model: str | None,
    latency_ms: float | None,
    input_tokens: int | None,
    output_tokens: int | None,
    error_category: str | None,
) -> None:
    """Record one answer-generation call's usage. Never raises.

    Token counts are provider-REPORTED (None when the provider didn't return
    them) — never estimated here. provider/model are non-secret identifiers.
    """
    _write(
        kind=LLM_USAGE, user_id=user_id,
        request_type=(request_type or "")[:30] or None,
        provider=(provider or "")[:40] or None,
        model=(model or "")[:120] or None,
        latency_ms=int(round(latency_ms)) if latency_ms is not None else None,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        error_category=(error_category or "")[:40] or None,
    )


__all__ = [
    "log_event",
    "log_llm_usage",
    "LLM_USAGE",
]
