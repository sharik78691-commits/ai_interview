"""Fire-and-forget activity logging for the admin panel.

``log_event`` writes one row with its OWN session and swallows every failure,
so instrumenting a hot path (login, prepare, analyze) can never break the
request it measures. Call it AFTER the main commit succeeds.
"""
import logging

from app.db.session import SessionLocal
from app.models.event import EventLog

logger = logging.getLogger(__name__)


def log_event(name: str, user_id: int | None = None) -> None:
    """Record an activity event. Never raises."""
    db = SessionLocal()
    try:
        db.add(EventLog(name=name, user_id=user_id))
        db.commit()
    except Exception as exc:
        logger.warning("Activity logging failed (%s=%s): %s", name, user_id, exc)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()
