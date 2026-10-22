"""Screenshot-analysis credits: reserve → complete / release.

Policy (documented in docs/PAYMENTS.md)
---------------------------------------
* One credit = one screenshot image. A request with 3 screenshots needs 3.
* Requests rejected BEFORE processing (validation, no subscription, not
  enough credits, server not configured, rate limit) consume nothing.
* Credits are RESERVED atomically before the AI call and charged to ONE
  live subscription — the soonest-expiring one with enough credits left.
  Allowances of different subscriptions are never merged.
* Success → the reservation becomes consumption. Any failure after the call
  started (provider error, timeout, unreadable answer, safety block) →
  the reservation is released, so users only pay for delivered analyses.
* Idempotency: ``(user_id, request_id)`` is unique. Re-sending a completed
  request is refused without charging again; re-sending a failed one
  retries it (one row, ``attempts`` counts the retries).
* A reservation left behind by a crashed worker is released after
  ``STALE_AFTER`` the next time that user analyzes.
* Admins (``User.is_admin``) have unlimited analyses: no plan is needed and
  no subscription is charged. Their requests are still recorded as usage rows
  (``subscription_id`` NULL) for idempotency and the audit trail.
* Free trial: when no plan can pay, the account's one-time free credits
  (``free_trial_service``, 2 by default) are used instead. Those rows also
  have ``subscription_id`` NULL; for a non-admin that means "free credits",
  which is how a stale or failed reservation gives them back.

Concurrency: the reservation is a single conditional UPDATE
(``screenshots_used + n <= screenshot_allowance`` in the WHERE clause), so
two parallel requests can never push usage over the allowance — on both
SQLite and PostgreSQL — and the table CHECK constraint backs it up.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.billing import ScreenshotUsage, SubscriptionStatus, UsageStatus, UserSubscription
from app.models.user import User
from app.services import free_trial_service as free_trial
from app.services import subscription_service as subs

logger = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=10)
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,64}$")


class CreditError(Exception):
    """Refusal with a stable code (mirrors ScreenshotAnalysisError's shape)."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class Reservation:
    usage_id: int
    #: None for an admin request (unlimited) or free-trial credits.
    subscription_id: int | None
    credits: int
    #: Paid from the free trial (given back to it on failure).
    free: bool = False


def normalize_request_id(raw: str | None) -> str:
    """Client idempotency key, or a fresh server id when none was sent."""
    value = (raw or "").strip()
    if not value:
        return f"srv-{uuid.uuid4().hex}"
    if not _REQUEST_ID_RE.match(value):
        raise CreditError("VALIDATION", "Invalid request id.", 422)
    return value


def _take(db: Session, sub_id: int, n: int, now: datetime) -> bool:
    """Atomically move ``n`` credits from available to used on one row."""
    result = db.execute(
        update(UserSubscription)
        .where(
            UserSubscription.id == sub_id,
            UserSubscription.status == SubscriptionStatus.ACTIVE,
            UserSubscription.starts_at <= now,
            UserSubscription.expires_at > now,
            UserSubscription.screenshots_used + n <= UserSubscription.screenshot_allowance,
        )
        .values(screenshots_used=UserSubscription.screenshots_used + n)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def _give_back(db: Session, sub_id: int, n: int) -> None:
    db.execute(
        update(UserSubscription)
        .where(UserSubscription.id == sub_id, UserSubscription.screenshots_used >= n)
        .values(screenshots_used=UserSubscription.screenshots_used - n)
        .execution_options(synchronize_session=False)
    )


def _release_stale(db: Session, user_id: int, now: datetime, is_admin: bool = False) -> None:
    stale = db.scalars(
        select(ScreenshotUsage).where(
            ScreenshotUsage.user_id == user_id,
            ScreenshotUsage.status == UsageStatus.RESERVED,
            ScreenshotUsage.updated_at < now - STALE_AFTER,
        )
    ).all()
    for row in stale:
        if row.subscription_id:
            _give_back(db, row.subscription_id, row.credits)
        elif not is_admin:
            free_trial.give_back_screenshots(db, user_id, row.credits)
        row.status = UsageStatus.FAILED
        row.failure_code = "STALE_RESERVATION"
        row.credits_consumed = 0
        logger.warning("Released stale screenshot reservation %s (user %s)", row.id, user_id)


def reserve(db: Session, user: User, request_id: str, credits: int, now: datetime | None = None) -> Reservation:
    """Hold ``credits`` for this request or raise :class:`CreditError`.

    Everything happens in one transaction: the usage row and the counter
    update commit together or not at all.
    """
    if credits <= 0:
        raise CreditError("VALIDATION", "Add at least one screenshot.", 422)
    now = now or subs.utcnow()
    if not user.is_admin:
        free_trial.get_or_create(db, user)  # commits on first use; before the transaction below
    try:
        _release_stale(db, user.id, now, user.is_admin)
        row = db.scalar(select(ScreenshotUsage).where(
            ScreenshotUsage.user_id == user.id, ScreenshotUsage.request_id == request_id
        ))
        if row is not None:
            if row.status == UsageStatus.COMPLETED:
                raise CreditError(
                    "DUPLICATE_REQUEST",
                    "This analysis was already completed and charged once. Start a new analysis.",
                    409,
                )
            if row.status == UsageStatus.RESERVED:
                raise CreditError("IN_PROGRESS", "This analysis is already running.", 409)
        free = False
        if user.is_admin:
            chosen = None  # unlimited: record the request, charge nothing
        else:
            try:
                chosen = _charge(db, user.id, credits, now)
            except CreditError:
                # No plan can pay: fall back to the one-time free credits.
                if not free_trial.take_screenshots(db, user, credits):
                    raise
                chosen, free = None, True
        if row is None:
            row = ScreenshotUsage(user_id=user.id, user_email=user.email, request_id=request_id,
                                  subscription_id=chosen.id if chosen else None, credits=credits,
                                  status=UsageStatus.RESERVED)
            db.add(row)
        else:  # retry of a failed request: same row, new attempt
            row.subscription_id = chosen.id if chosen else None
            row.credits = credits
            row.status = UsageStatus.RESERVED
            row.failure_code = ""
            row.completed_at = None
            row.attempts = (row.attempts or 1) + 1
        db.flush()
        db.commit()
    except CreditError:
        db.rollback()
        raise
    except IntegrityError:
        # A parallel request with the same id won the insert; nothing held.
        db.rollback()
        raise CreditError("IN_PROGRESS", "This analysis is already running.", 409) from None
    return Reservation(row.id, chosen.id if chosen else None, credits, free)


def _charge(db: Session, user_id: int, credits: int, now: datetime) -> UserSubscription:
    """Take ``credits`` from one live subscription or raise :class:`CreditError`."""
    live = subs.live_subscriptions(db, user_id, now)
    if not live:
        raise CreditError(
            "SUBSCRIPTION_REQUIRED",
            "Screenshot analysis needs an active plan. Buy a pass on the Pricing page.",
            402,
        )
    chosen = next((s for s in live if subs.credits_remaining(s) >= credits and _take(db, s.id, credits, now)), None)
    if chosen is None:
        left = max(subs.credits_remaining(s) for s in live)
        if left == 0:
            message = "You have used all screenshot analyses in your plan. Buy another pass to continue."
        else:
            message = (f"This analysis needs {credits} screenshot credits but your plan has "
                       f"{left} left. Remove some screenshots or buy another pass.")
        raise CreditError("NO_CREDITS", message, 402)
    return chosen


def complete(db: Session, reservation: Reservation) -> None:
    row = db.get(ScreenshotUsage, reservation.usage_id)
    if row is None or row.status != UsageStatus.RESERVED:
        return
    row.status = UsageStatus.COMPLETED
    row.credits_consumed = reservation.credits
    row.completed_at = subs.utcnow()
    db.commit()


def release(db: Session, reservation: Reservation, failure_code: str) -> None:
    """The analysis failed after it started: give the credits back."""
    row = db.get(ScreenshotUsage, reservation.usage_id)
    if row is None or row.status != UsageStatus.RESERVED:
        return
    if reservation.subscription_id is not None:
        _give_back(db, reservation.subscription_id, reservation.credits)
    elif reservation.free:
        free_trial.give_back_screenshots(db, row.user_id, reservation.credits)
    row.status = UsageStatus.FAILED
    row.credits_consumed = 0
    row.failure_code = (failure_code or "ERROR")[:40]
    row.completed_at = subs.utcnow()
    db.commit()


@dataclass(frozen=True)
class UsageSummary:
    subscription_id: int | None
    plan_name: str
    status: str
    allowance: int
    used: int
    remaining: int
    expires_at: datetime | None
    #: Remaining credits across ALL live subscriptions (each usable on its own).
    total_remaining: int


def summary(db: Session, user_id: int, now: datetime | None = None) -> UsageSummary:
    now = now or subs.utcnow()
    live = subs.live_subscriptions(db, user_id, now)
    sub = subs.current_subscription(db, user_id, now)
    if sub is None:
        return UsageSummary(None, "", "none", 0, 0, 0, None, 0)
    return UsageSummary(
        subscription_id=sub.id, plan_name=sub.plan_name, status=subs.derived_status(sub, now),
        allowance=sub.screenshot_allowance, used=sub.screenshots_used,
        remaining=subs.credits_remaining(sub), expires_at=subs.aware(sub.expires_at),
        total_remaining=sum(subs.credits_remaining(s) for s in live),
    )


__all__ = [
    "CreditError",
    "Reservation",
    "UsageSummary",
    "complete",
    "normalize_request_id",
    "release",
    "reserve",
    "summary",
]
