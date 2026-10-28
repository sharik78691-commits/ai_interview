"""Subscription rules: plans, access periods, credits, manual changes, audit.

Transport-agnostic (no FastAPI). Callers pass a SQLAlchemy ``Session`` and
own the request; every mutating function here commits its own unit of work
and writes a ``subscription_audit_logs`` row in the SAME transaction.

Period model
------------
* A period is EXACT: ``expires_at = starts_at + duration_hours`` in UTC. A
  1 Day Pass activated at 23:15 IST ends at 23:15 IST the next day; nothing
  is rounded to midnight, calendar months or quarters.
* Repurchase policy: every purchase is its own subscription row and starts
  IMMEDIATELY (product decision 2026-10-21), with its own end time and its
  own screenshot allowance. Allowances are never merged: a screenshot
  request is charged to ONE live subscription — the one that expires first
  and still has enough credits (``screenshot_credit_service``).
* Status is derived from dates at read time (``scheduled`` / ``active`` /
  ``expired``) plus the stored terminal states (``revoked`` / ``cancelled``
  / ``refunded``), so no cron job is needed to "expire" rows. Pending or
  failed payments never create a subscription, so they never grant access.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import overload

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.billing import (
    BillingType,
    Payment,
    SubscriptionAuditLog,
    SubscriptionPlan,
    SubscriptionSource,
    SubscriptionStatus,
    UserSubscription,
)
from app.models.user import User

logger = logging.getLogger(__name__)

#: Upper bound for any single period / extension (10 years).
MAX_HOURS = 3660 * 24


class SubscriptionError(ValueError):
    """Business-rule violation (safe to show to the caller)."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@overload
def aware(dt: datetime) -> datetime: ...
@overload
def aware(dt: None) -> None: ...
def aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; everything here is UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ plans


@dataclass(frozen=True)
class EffectivePrice:
    amount_minor: int
    currency: str
    is_promo: bool
    regular_minor: int


def effective_price(plan: SubscriptionPlan, now: datetime | None = None) -> EffectivePrice:
    """Price in the plan's PRIMARY currency right now (promo inside its window).

    Other currencies come from ``pricing_service.price_for``.
    """
    now = now or utcnow()
    promo = plan.promo_price_minor
    starts, ends = aware(plan.promo_starts_at), aware(plan.promo_ends_at)
    in_window = promo is not None and (starts is None or starts <= now) and (ends is None or now < ends)
    if in_window and promo is not None and promo < plan.price_minor:
        return EffectivePrice(promo, plan.currency, True, plan.price_minor)
    return EffectivePrice(plan.price_minor, plan.currency, False, plan.price_minor)


def plan_hours(plan: SubscriptionPlan) -> int:
    """The plan's exact length in hours (legacy rows fall back to days*24)."""
    return plan.duration_hours or plan.duration_days * 24


def days_for_hours(hours: int) -> int:
    """Legacy whole-day mirror of an hour duration (rounded up, >= 1)."""
    return max(1, math.ceil(hours / 24))


def plan_features(plan: SubscriptionPlan) -> list[str]:
    try:
        data = json.loads(plan.features_json or "[]")
    except ValueError:
        return []
    return [str(x) for x in data if isinstance(x, (str, int, float))][:20]


def list_plans(
    db: Session, *, include_inactive: bool = False, public_only: bool = False
) -> list[SubscriptionPlan]:
    stmt = select(SubscriptionPlan).order_by(SubscriptionPlan.sort_order, SubscriptionPlan.id)
    if not include_inactive:
        stmt = stmt.where(SubscriptionPlan.is_active.is_(True))
    if public_only:
        stmt = stmt.where(SubscriptionPlan.is_public.is_(True))
    return list(db.scalars(stmt).all())


def get_active_plan(db: Session, plan_id: int) -> SubscriptionPlan:
    plan = db.get(SubscriptionPlan, plan_id)
    if plan is None or not plan.is_active:
        raise SubscriptionError("This plan is not available.")
    return plan


def seed_default_plans(db: Session) -> int:
    """Create / migrate the plan catalogue (idempotent; admin edits win).

    Kept under its historical name; the work lives in
    ``pricing_service.ensure_catalogue``.
    """
    from app.services import pricing_service

    return pricing_service.ensure_catalogue(db)


# --------------------------------------------------------- audit helpers


def _audit(
    db: Session,
    *,
    action: str,
    user_id: int | None = None,
    actor_user_id: int | None = None,
    subscription_id: int | None = None,
    payment_id: int | None = None,
    plan_id: int | None = None,
    reason: str = "",
    details: dict | None = None,
) -> None:
    db.add(
        SubscriptionAuditLog(
            action=action,
            user_id=user_id,
            actor_user_id=actor_user_id,
            subscription_id=subscription_id,
            payment_id=payment_id,
            plan_id=plan_id,
            reason=(reason or "")[:500],
            details_json=json.dumps(details or {}, default=str),
        )
    )


def plan_snapshot(plan: SubscriptionPlan) -> dict:
    return {
        "code": plan.code, "name": plan.name, "price_minor": plan.price_minor,
        "currency": plan.currency, "duration_hours": plan_hours(plan),
        "duration_unit": plan.duration_unit, "screenshot_allowance": plan.screenshot_allowance,
        "billing_type": plan.billing_type, "billing_interval_days": plan.billing_interval_days,
        "trial_days": plan.trial_days, "promo_price_minor": plan.promo_price_minor,
        "promo_starts_at": aware(plan.promo_starts_at), "promo_ends_at": aware(plan.promo_ends_at),
        "is_active": plan.is_active, "is_public": plan.is_public,
        "is_highlighted": plan.is_highlighted, "sort_order": plan.sort_order,
    }


def record_plan_change(
    db: Session, plan: SubscriptionPlan, actor: User | None, action: str, before: dict | None,
    reason: str = "",
) -> None:
    """Audit a catalogue change (caller commits)."""
    _audit(db, action=action, actor_user_id=actor.id if actor else None, plan_id=plan.id,
           reason=reason, details={"before": before, "after": plan_snapshot(plan)})


# ------------------------------------------------------- periods / status


def derived_status(sub: UserSubscription, now: datetime | None = None) -> str:
    now = now or utcnow()
    if sub.status in SubscriptionStatus.TERMINAL:
        return sub.status
    if aware(sub.starts_at) > now:
        return "scheduled"
    if aware(sub.expires_at) <= now:
        return "expired"
    return "active"


def credits_remaining(sub: UserSubscription) -> int:
    return max(0, (sub.screenshot_allowance or 0) - (sub.screenshots_used or 0))


def live_subscriptions(db: Session, user_id: int, now: datetime | None = None) -> list[UserSubscription]:
    """Subscriptions granting access right now, soonest-expiring first."""
    now = now or utcnow()
    return list(db.scalars(
        select(UserSubscription)
        .where(
            UserSubscription.user_id == user_id,
            UserSubscription.status == SubscriptionStatus.ACTIVE,
            UserSubscription.starts_at <= now,
            UserSubscription.expires_at > now,
        )
        .order_by(UserSubscription.expires_at.asc(), UserSubscription.id.asc())
    ).all())


def current_subscription(db: Session, user_id: int, now: datetime | None = None) -> UserSubscription | None:
    """The applicable subscription right now.

    The one the next screenshot would be charged to (soonest-expiring with
    credits left); if none has credits, the latest-ending live period.
    """
    rows = live_subscriptions(db, user_id, now)
    for sub in rows:
        if credits_remaining(sub) > 0:
            return sub
    return rows[-1] if rows else None


def access_ends_at(db: Session, user_id: int, now: datetime | None = None) -> datetime | None:
    """When the user's paid/granted access (across all live periods) ends."""
    now = now or utcnow()
    latest = db.scalar(
        select(func.max(UserSubscription.expires_at)).where(
            UserSubscription.user_id == user_id,
            UserSubscription.status == SubscriptionStatus.ACTIVE,
            UserSubscription.expires_at > now,
        )
    )
    return aware(latest)


def has_active_subscription(db: Session, user_id: int) -> bool:
    return current_subscription(db, user_id) is not None


def _new_period(db: Session, user_id: int, hours: int, now: datetime) -> tuple[datetime, datetime, bool]:
    """Exact period starting NOW; ``renewal`` = the user already had access."""
    if hours <= 0 or hours > MAX_HOURS:
        raise SubscriptionError("Duration must be between 1 hour and 10 years.")
    had_access = access_ends_at(db, user_id, now) is not None
    return now, now + timedelta(hours=hours), had_access


def require_transition(sub: UserSubscription, target: str) -> None:
    """Only ACTIVE rows may move to a terminal state; terminal is final."""
    if sub.status in SubscriptionStatus.TERMINAL:
        raise SubscriptionError(f"A {sub.status} subscription cannot be changed to {target}.")


# ------------------------------------------------- payment-driven periods


def activate_from_payment(db: Session, payment: Payment, now: datetime | None = None) -> UserSubscription:
    """Create the access period for a VERIFIED payment (caller commits).

    Idempotent: the unique ``payment_id`` means a second call returns the
    existing period instead of granting the purchase (or its credits) twice.
    Duration and allowance come from the payment's own SNAPSHOT, so later
    plan edits never change what was bought.
    """
    existing = db.scalar(select(UserSubscription).where(UserSubscription.payment_id == payment.id))
    if existing is not None:
        return existing
    now = now or utcnow()
    hours = payment.duration_hours or payment.duration_days * 24
    start, end, renewal = _new_period(db, payment.user_id, hours, now)
    plan = db.get(SubscriptionPlan, payment.plan_id)
    sub = UserSubscription(
        user_id=payment.user_id,
        user_email=payment.user_email,
        plan_id=payment.plan_id,
        plan_code=payment.plan_code,
        plan_name=payment.plan_name,
        status=SubscriptionStatus.ACTIVE,
        source=SubscriptionSource.PAYMENT,
        starts_at=start,
        expires_at=end,
        payment_id=payment.id,
        amount_minor=payment.amount_minor,
        currency=payment.currency,
        is_renewal=renewal,
        screenshot_allowance=payment.screenshot_allowance,
        billing_type=plan.billing_type if plan else BillingType.ONE_TIME,
    )
    db.add(sub)
    db.flush()
    payment.is_renewal = renewal
    _audit(db, action="purchase", user_id=payment.user_id, subscription_id=sub.id,
           payment_id=payment.id, plan_id=payment.plan_id,
           details={"gateway": payment.gateway, "amount_minor": payment.amount_minor,
                    "currency": payment.currency, "starts_at": start, "expires_at": end,
                    "duration_hours": hours, "screenshot_allowance": payment.screenshot_allowance,
                    "renewal": renewal})
    return sub


def end_for_payment(
    db: Session, payment: Payment, *, status: str, reason: str, actor_user_id: int | None = None,
) -> UserSubscription | None:
    """Refund / lost chargeback: end the access the payment bought (caller commits).

    Usage history is kept; only the subscription's stored status changes.
    """
    sub = db.scalar(select(UserSubscription).where(UserSubscription.payment_id == payment.id))
    if sub is None or sub.status in SubscriptionStatus.TERMINAL:
        return sub
    sub.status = status
    sub.revoked_at = utcnow()
    _audit(db, action="refund", user_id=sub.user_id, actor_user_id=actor_user_id, subscription_id=sub.id,
           payment_id=payment.id, plan_id=sub.plan_id, reason=reason,
           details={"status": status, "expires_at": aware(sub.expires_at),
                    "screenshots_used": sub.screenshots_used})
    return sub


# ------------------------------------------------------------- trials


def start_trial(db: Session, user: User, plan_id: int) -> UserSubscription:
    """One free trial per user, only on plans that offer one."""
    plan = get_active_plan(db, plan_id)
    if plan.trial_days <= 0:
        raise SubscriptionError("This plan does not include a free trial.")
    used = db.scalar(
        select(func.count(UserSubscription.id)).where(
            UserSubscription.user_id == user.id,
            UserSubscription.source == SubscriptionSource.TRIAL,
        )
    )
    if used:
        raise SubscriptionError("You have already used your free trial.")
    now = utcnow()
    hours = plan.trial_days * 24
    start, end, _ = _new_period(db, user.id, hours, now)
    # Credits pro rata to the trial length (never more than the plan's).
    allowance = min(plan.screenshot_allowance, plan.screenshot_allowance * hours // max(plan_hours(plan), 1))
    sub = UserSubscription(
        user_id=user.id, user_email=user.email, plan_id=plan.id, plan_code=plan.code,
        plan_name=plan.name, status=SubscriptionStatus.ACTIVE, source=SubscriptionSource.TRIAL,
        starts_at=start, expires_at=end, amount_minor=0, currency=plan.currency,
        screenshot_allowance=allowance, billing_type=plan.billing_type,
    )
    db.add(sub)
    db.flush()
    _audit(db, action="trial_start", user_id=user.id, subscription_id=sub.id, plan_id=plan.id,
           details={"starts_at": start, "expires_at": end, "screenshot_allowance": allowance})
    db.commit()
    db.refresh(sub)
    return sub


# --------------------------------------------- manual (admin) changes


def _need_reason(reason: str) -> None:
    if not (reason or "").strip():
        raise SubscriptionError("A reason is required for manual changes.")


def grant(
    db: Session, admin: User, user: User, *, plan_id: int | None, days: int | None, reason: str,
    hours: int | None = None, screenshot_credits: int | None = None,
) -> UserSubscription:
    """Admin grant. Recorded with source="manual", amount 0 and NO payment link,
    so it can never be mistaken for a paid transaction."""
    _need_reason(reason)
    plan = db.get(SubscriptionPlan, plan_id) if plan_id else None
    if plan_id and plan is None:
        raise SubscriptionError("Unknown plan.")
    length = hours or (days * 24 if days else 0) or (plan_hours(plan) if plan else 0)
    if length <= 0 or length > MAX_HOURS:
        raise SubscriptionError("Duration must be between 1 hour and 3660 days.")
    credits = screenshot_credits if screenshot_credits is not None else (
        plan.screenshot_allowance if plan else 0
    )
    if credits < 0 or credits > 100_000:
        raise SubscriptionError("Screenshot credits must be between 0 and 100000.")
    now = utcnow()
    start, end, _ = _new_period(db, user.id, length, now)
    label = f"{length // 24} days" if length % 24 == 0 else f"{length} hours"
    sub = UserSubscription(
        user_id=user.id, user_email=user.email,
        plan_id=plan.id if plan else None,
        plan_code=plan.code if plan else "manual",
        plan_name=plan.name if plan else f"Manual grant ({label})",
        status=SubscriptionStatus.ACTIVE, source=SubscriptionSource.MANUAL,
        starts_at=start, expires_at=end, amount_minor=0,
        currency=plan.currency if plan else "INR",
        screenshot_allowance=credits,
        billing_type=plan.billing_type if plan else BillingType.ONE_TIME,
        granted_by_user_id=admin.id, notes=reason.strip()[:500],
    )
    db.add(sub)
    db.flush()
    _audit(db, action="grant", user_id=user.id, actor_user_id=admin.id, subscription_id=sub.id,
           plan_id=sub.plan_id, reason=reason,
           details={"hours": length, "starts_at": start, "expires_at": end,
                    "screenshot_allowance": credits, "source": "manual", "paid": False})
    db.commit()
    db.refresh(sub)
    logger.info("Admin %s granted %d hours to user %s (sub %s)", admin.id, length, user.id, sub.id)
    return sub


def extend(
    db: Session, admin: User, sub: UserSubscription, *, days: int = 0, reason: str, hours: int = 0
) -> UserSubscription:
    _need_reason(reason)
    total = hours + days * 24
    if total <= 0 or total > MAX_HOURS:
        raise SubscriptionError("Extension must be between 1 hour and 3660 days.")
    require_transition(sub, "extended")
    before = aware(sub.expires_at)
    sub.expires_at = before + timedelta(hours=total)
    _audit(db, action="extend", user_id=sub.user_id, actor_user_id=admin.id,
           subscription_id=sub.id, payment_id=sub.payment_id, reason=reason,
           details={"hours": total, "expires_before": before, "expires_after": sub.expires_at})
    db.commit()
    db.refresh(sub)
    logger.info("Admin %s extended sub %s by %d hours", admin.id, sub.id, total)
    return sub


def _terminate(db: Session, admin: User, sub: UserSubscription, *, status: str, action: str,
               reason: str) -> UserSubscription:
    _need_reason(reason)
    if sub.status == status:
        return sub
    require_transition(sub, status)
    now = utcnow()
    sub.status = status
    if status == SubscriptionStatus.CANCELLED:
        sub.cancelled_at = now
    else:
        sub.revoked_at = now
    _audit(db, action=action, user_id=sub.user_id, actor_user_id=admin.id,
           subscription_id=sub.id, payment_id=sub.payment_id, reason=reason,
           details={"expires_at": aware(sub.expires_at), "source": sub.source,
                    "screenshots_used": sub.screenshots_used})
    db.commit()
    db.refresh(sub)
    logger.info("Admin %s set sub %s to %s", admin.id, sub.id, status)
    return sub


def revoke(db: Session, admin: User, sub: UserSubscription, *, reason: str) -> UserSubscription:
    """Revoke access. The payment record (if any) is untouched — refunds are
    handled in the gateway dashboard and are not implied by a revoke."""
    return _terminate(db, admin, sub, status=SubscriptionStatus.REVOKED, action="revoke", reason=reason)


def cancel(db: Session, admin: User, sub: UserSubscription, *, reason: str) -> UserSubscription:
    """Cancel (e.g. on the customer's request). Access ends now; history kept."""
    return _terminate(db, admin, sub, status=SubscriptionStatus.CANCELLED, action="cancel", reason=reason)


def adjust_credits(
    db: Session, admin: User, sub: UserSubscription, *, allowance: int, reason: str
) -> UserSubscription:
    """Correct a subscription's screenshot allowance (audited).

    Usage records are never edited; only the allowance changes, and it can
    never drop below what has already been used.
    """
    _need_reason(reason)
    if allowance < 0 or allowance > 100_000:
        raise SubscriptionError("Allowance must be between 0 and 100000.")
    db.refresh(sub)  # latest counter (credits may have been used meanwhile)
    if allowance < sub.screenshots_used:
        raise SubscriptionError(
            f"Allowance cannot be below the {sub.screenshots_used} screenshots already used."
        )
    before = sub.screenshot_allowance
    sub.screenshot_allowance = allowance
    _audit(db, action="credits_adjust", user_id=sub.user_id, actor_user_id=admin.id,
           subscription_id=sub.id, payment_id=sub.payment_id, reason=reason,
           details={"allowance_before": before, "allowance_after": allowance,
                    "screenshots_used": sub.screenshots_used})
    db.commit()
    db.refresh(sub)
    logger.info("Admin %s set sub %s allowance %s -> %s", admin.id, sub.id, before, allowance)
    return sub


__all__ = [
    "EffectivePrice",
    "SubscriptionError",
    "access_ends_at",
    "activate_from_payment",
    "adjust_credits",
    "aware",
    "cancel",
    "credits_remaining",
    "current_subscription",
    "days_for_hours",
    "derived_status",
    "effective_price",
    "end_for_payment",
    "extend",
    "get_active_plan",
    "grant",
    "has_active_subscription",
    "list_plans",
    "live_subscriptions",
    "plan_features",
    "plan_hours",
    "plan_snapshot",
    "record_plan_change",
    "revoke",
    "seed_default_plans",
    "start_trial",
    "utcnow",
]
