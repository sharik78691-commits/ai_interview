"""One-time free trial: live-interview minutes and screenshot credits.

Policy (product decision 2026-10-22, documented in docs/PAYMENTS.md)
--------------------------------------------------------------------
* Every account (new and existing) gets the allowance ONCE:
  ``FREE_WEB_MINUTES`` (20) of live interview on the website, a SEPARATE
  ``FREE_DESKTOP_MINUTES`` (15) in the desktop app, and ``FREE_SCREENSHOTS``
  (2) screenshot analyses. The pools never merge.
* Live interview needs a live plan once the pool for that client is used up.
  Admins and users with a live plan are unlimited and use none of the trial.
* Minutes count ACTIVE session time only (see ``api/websocket.py``): the
  socket is metered while interviewer audio is being captured or an answer
  was requested recently, so an idle open tab costs nothing.
* Screenshot credits: a plan's credits are used first; the free ones only
  when no plan can pay.

The client type comes from the socket's ``client`` query parameter. It is
not authenticated, so a user could spend the desktop pool on the website;
each pool is still one-time per account, which caps that at 35 minutes.
Limits live in settings (not the row), so they can change without a migration.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.billing import FreeTrialUsage
from app.models.user import User
from app.services import subscription_service as subs

logger = logging.getLogger(__name__)

WEB = "web"
DESKTOP = "desktop"


def normalize_client(raw: str | None) -> str:
    return DESKTOP if (raw or "").strip().lower() == DESKTOP else WEB


def limit_seconds(client: str) -> int:
    s = get_settings()
    return (s.free_desktop_minutes if client == DESKTOP else s.free_web_minutes) * 60


def screenshot_limit() -> int:
    return get_settings().free_screenshots


def _column(client: str):
    return FreeTrialUsage.desktop_seconds_used if client == DESKTOP else FreeTrialUsage.web_seconds_used


def get_or_create(db: Session, user: User) -> FreeTrialUsage:
    row = db.get(FreeTrialUsage, user.id)
    if row is not None:
        return row
    try:
        row = FreeTrialUsage(user_id=user.id, user_email=user.email or "")
        db.add(row)
        db.commit()
    except IntegrityError:  # a parallel request created it first
        db.rollback()
        row = db.get(FreeTrialUsage, user.id)
    return row


def seconds_remaining(row: FreeTrialUsage, client: str) -> int:
    used = row.desktop_seconds_used if client == DESKTOP else row.web_seconds_used
    return max(0, limit_seconds(client) - (used or 0))


def screenshots_remaining(row: FreeTrialUsage) -> int:
    return max(0, screenshot_limit() - (row.screenshots_used or 0))


@dataclass(frozen=True)
class LiveAccess:
    #: "unlimited" (admin / live plan / gate off), "trial" or "denied".
    mode: str
    #: Trial seconds left for this client (0 unless mode == "trial").
    remaining_seconds: int
    limit_seconds: int


def live_access(db: Session, user: User, client: str) -> LiveAccess:
    """May this user run a live interview session from ``client`` right now?"""
    limit = limit_seconds(client)
    if user.is_admin or not get_settings().free_trial_enforced:
        return LiveAccess("unlimited", 0, limit)
    if subs.live_subscriptions(db, user.id):
        return LiveAccess("unlimited", 0, limit)
    left = seconds_remaining(get_or_create(db, user), client)
    return LiveAccess("trial" if left > 0 else "denied", left, limit)


def consume_seconds(db: Session, user: User, client: str, seconds: int) -> int:
    """Add ``seconds`` of active session time; returns the seconds left."""
    get_or_create(db, user)
    col = _column(client)
    db.execute(
        update(FreeTrialUsage)
        .where(FreeTrialUsage.user_id == user.id)
        .values({col.key: col + max(0, int(seconds))})
        .execution_options(synchronize_session=False)
    )
    db.commit()
    row = db.get(FreeTrialUsage, user.id)
    db.refresh(row)
    return seconds_remaining(row, client)


def take_screenshots(db: Session, user: User, n: int) -> bool:
    """Atomically use ``n`` free screenshot credits (caller commits).

    The row must already exist (``get_or_create``) — this never commits.
    """
    result = db.execute(
        update(FreeTrialUsage)
        .where(
            FreeTrialUsage.user_id == user.id,
            FreeTrialUsage.screenshots_used + n <= screenshot_limit(),
        )
        .values(screenshots_used=FreeTrialUsage.screenshots_used + n)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def give_back_screenshots(db: Session, user_id: int, n: int) -> None:
    """Return ``n`` free screenshot credits (caller commits)."""
    db.execute(
        update(FreeTrialUsage)
        .where(FreeTrialUsage.user_id == user_id, FreeTrialUsage.screenshots_used >= n)
        .values(screenshots_used=FreeTrialUsage.screenshots_used - n)
        .execution_options(synchronize_session=False)
    )


def summary(db: Session, user: User) -> dict:
    """The caller's trial state for the UI (camelCase, like the screenshot API)."""
    row = get_or_create(db, user)
    has_plan = bool(user.is_admin or subs.live_subscriptions(db, user.id))
    s = get_settings()
    return {
        "enforced": s.free_trial_enforced,
        "hasPlan": has_plan,
        "unlimited": bool(user.is_admin),
        "web": {"limitSeconds": limit_seconds(WEB), "remainingSeconds": seconds_remaining(row, WEB)},
        "desktop": {"limitSeconds": limit_seconds(DESKTOP), "remainingSeconds": seconds_remaining(row, DESKTOP)},
        "screenshots": {"limit": screenshot_limit(), "remaining": screenshots_remaining(row)},
    }


__all__ = [
    "DESKTOP",
    "LiveAccess",
    "WEB",
    "consume_seconds",
    "get_or_create",
    "give_back_screenshots",
    "live_access",
    "normalize_client",
    "screenshots_remaining",
    "summary",
    "take_screenshots",
]
