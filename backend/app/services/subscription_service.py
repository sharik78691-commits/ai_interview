"""Subscription rules: plans, pricing, access periods, manual changes, audit.

Transport-agnostic (no FastAPI). Callers pass a SQLAlchemy ``Session`` and
own the request; every mutating function here commits its own unit of work
and writes a ``subscription_audit_logs`` row in the SAME transaction.

Period model
------------
Access periods stack: buying (or being granted) time while a period is still
running starts the new period when the current one ends, so nobody loses
days by renewing early. Status is derived from dates at read time
(``scheduled`` / ``active`` / ``expired``) plus the stored ``revoked`` flag,
so no cron job is needed to "expire" rows.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import overload

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.billing import (
    Payment,
    SubscriptionAuditLog,
    SubscriptionPlan,
    SubscriptionSource,
    SubscriptionStatus,
    UserSubscription,
)
from app.models.user import User

logger = logging.getLogger(__name__)


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
    """The price a checkout started NOW must charge (promo inside its window)."""
    now = now or utcnow()
    promo = plan.promo_price_minor
    starts, ends = aware(plan.promo_starts_at), aware(plan.promo_ends_at)
    in_window = promo is not None and (starts is None or starts <= now) and (ends is None or now < ends)
    if in_window and promo is not None and promo < plan.price_minor:
        return EffectivePrice(promo, plan.currency, True, plan.price_minor)
    return EffectivePrice(plan.price_minor, plan.currency, False, plan.price_minor)


def plan_features(plan: SubscriptionPlan) -> list[str]:
    try:
        data = json.loads(plan.features_json or "[]")
    except ValueError:
        return []
    return [str(x) for x in data if isinstance(x, (str, int, float))][:20]


def list_plans(db: Session, *, include_inactive: bool = False) -> list[SubscriptionPlan]:
    stmt = select(SubscriptionPlan).order_by(SubscriptionPlan.sort_order, SubscriptionPlan.id)
    if not include_inactive:
        stmt = stmt.where(SubscriptionPlan.is_active.is_(True))
    return list(db.scalars(stmt).all())


def get_active_plan(db: Session, plan_id: int) -> SubscriptionPlan:
    plan = db.get(SubscriptionPlan, plan_id)
    if plan is None or not plan.is_active:
        raise SubscriptionError("This plan is not available.")
    return plan


# The four plans the public pricing page has always shown (prices/durations
# taken from the previous static page). Features list only what the product
# actually does today.
_DEFAULT_FEATURES = [
    "Real-time AI answer guidance",
    "Resume- and job-description-aware answers",
    "Technical, behavioral and coding questions",
    "Meeting / tab audio transcription",
    "Answer length control (short / medium / long)",
    "Choice of available AI models",
]
DEFAULT_PLANS = [
    dict(code="3-days", name="3 Days", price_minor=15_000, duration_days=3, sort_order=10,
         description="A single interview, fully assisted."),
    dict(code="7-days", name="7 Days", price_minor=30_000, duration_days=7, sort_order=20,
         description="A week of interviews and practice."),
    dict(code="1-month", name="1 Month", price_minor=90_000, duration_days=30, sort_order=30,
         description="A full job-hunt month of coverage.", badge="Most popular", is_highlighted=True),
    dict(code="3-months", name="3 Months", price_minor=200_000, duration_days=90, sort_order=40,
         description="Long interview cycles at the lowest daily price.", badge="Best value"),
]


def seed_default_plans(db: Session) -> int:
    """Insert the default catalogue ONLY when no plan exists yet (idempotent).

    Never touches an existing catalogue, so admin edits always win.
    """
    if db.scalar(select(func.count(SubscriptionPlan.id))):
        return 0
    for spec in DEFAULT_PLANS:
        db.add(SubscriptionPlan(currency="INR", features_json=json.dumps(_DEFAULT_FEATURES), **spec))
    db.commit()
    logger.info("Seeded %d default subscription plans", len(DEFAULT_PLANS))
    return len(DEFAULT_PLANS)


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
        "currency": plan.currency, "duration_days": plan.duration_days,
        "trial_days": plan.trial_days, "promo_price_minor": plan.promo_price_minor,
        "promo_starts_at": aware(plan.promo_starts_at), "promo_ends_at": aware(plan.promo_ends_at),
        "is_active": plan.is_active,
    }


def record_plan_change(
    db: Session, plan: SubscriptionPlan, actor: User, action: str, before: dict | None
) -> None:
    """Audit a catalogue change (caller commits)."""
    _audit(db, action=action, actor_user_id=actor.id, plan_id=plan.id,
           details={"before": before, "after": plan_snapshot(plan)})


# ------------------------------------------------------- periods / status


def derived_status(sub: UserSubscription, now: datetime | None = None) -> str:
    now = now or utcnow()
    if sub.status == SubscriptionStatus.REVOKED:
        return "revoked"
    if aware(sub.starts_at) > now:
        return "scheduled"
    if aware(sub.expires_at) <= now:
        return "expired"
    return "active"


def _live_query(user_id: int):
    return select(UserSubscription).where(
        UserSubscription.user_id == user_id,
        UserSubscription.status == SubscriptionStatus.ACTIVE,
    )


def current_subscription(db: Session, user_id: int, now: datetime | None = None) -> UserSubscription | None:
    """The period granting access right now (latest-ending if several overlap)."""
    now = now or utcnow()
    rows = db.scalars(
        _live_query(user_id)
        .where(UserSubscription.starts_at <= now, UserSubscription.expires_at > now)
        .order_by(UserSubscription.expires_at.desc())
    ).all()
    return rows[0] if rows else None


def access_ends_at(db: Session, user_id: int, now: datetime | None = None) -> datetime | None:
    """When the user's paid/granted access (including stacked periods) ends."""
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
    """For feature gating (not enforced anywhere yet — a product decision)."""
    return current_subscription(db, user_id) is not None


def _next_period(db: Session, user_id: int, days: int, now: datetime) -> tuple[datetime, datetime, bool]:
    ends = access_ends_at(db, user_id, now)
    start = ends if ends and ends > now else now
    return start, start + timedelta(days=days), ends is not None


# ------------------------------------------------- payment-driven periods


def activate_from_payment(db: Session, payment: Payment, now: datetime | None = None) -> UserSubscription:
    """Create the access period for a VERIFIED payment (caller commits).

    Idempotent: the unique ``payment_id`` means a second call returns the
    existing period instead of granting the purchase twice.
    """
    existing = db.scalar(select(UserSubscription).where(UserSubscription.payment_id == payment.id))
    if existing is not None:
        return existing
    now = now or utcnow()
    start, end, renewal = _next_period(db, payment.user_id, payment.duration_days, now)
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
    )
    db.add(sub)
    db.flush()
    payment.is_renewal = renewal
    _audit(db, action="purchase", user_id=payment.user_id, subscription_id=sub.id,
           payment_id=payment.id, plan_id=payment.plan_id,
           details={"gateway": payment.gateway, "amount_minor": payment.amount_minor,
                    "currency": payment.currency, "starts_at": start, "expires_at": end,
                    "renewal": renewal})
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
    start, end, _ = _next_period(db, user.id, plan.trial_days, now)
    sub = UserSubscription(
        user_id=user.id, user_email=user.email, plan_id=plan.id, plan_code=plan.code,
        plan_name=plan.name, status=SubscriptionStatus.ACTIVE, source=SubscriptionSource.TRIAL,
        starts_at=start, expires_at=end, amount_minor=0, currency=plan.currency,
    )
    db.add(sub)
    db.flush()
    _audit(db, action="trial_start", user_id=user.id, subscription_id=sub.id, plan_id=plan.id,
           details={"starts_at": start, "expires_at": end})
    db.commit()
    db.refresh(sub)
    return sub


# --------------------------------------------- manual (admin) changes


def grant(
    db: Session, admin: User, user: User, *, plan_id: int | None, days: int | None, reason: str
) -> UserSubscription:
    """Admin grant. Recorded with source="manual" and NO payment link."""
    if not reason.strip():
        raise SubscriptionError("A reason is required for manual changes.")
    plan = db.get(SubscriptionPlan, plan_id) if plan_id else None
    if plan_id and plan is None:
        raise SubscriptionError("Unknown plan.")
    length = days or (plan.duration_days if plan else 0)
    if length <= 0 or length > 3660:
        raise SubscriptionError("Duration must be between 1 and 3660 days.")
    now = utcnow()
    start, end, _ = _next_period(db, user.id, length, now)
    sub = UserSubscription(
        user_id=user.id, user_email=user.email,
        plan_id=plan.id if plan else None,
        plan_code=plan.code if plan else "manual",
        plan_name=plan.name if plan else f"Manual grant ({length} days)",
        status=SubscriptionStatus.ACTIVE, source=SubscriptionSource.MANUAL,
        starts_at=start, expires_at=end, amount_minor=0,
        currency=plan.currency if plan else "INR",
        granted_by_user_id=admin.id, notes=reason.strip()[:500],
    )
    db.add(sub)
    db.flush()
    _audit(db, action="grant", user_id=user.id, actor_user_id=admin.id, subscription_id=sub.id,
           plan_id=sub.plan_id, reason=reason,
           details={"days": length, "starts_at": start, "expires_at": end})
    db.commit()
    db.refresh(sub)
    logger.info("Admin %s granted %d days to user %s (sub %s)", admin.id, length, user.id, sub.id)
    return sub


def extend(db: Session, admin: User, sub: UserSubscription, *, days: int, reason: str) -> UserSubscription:
    if not reason.strip():
        raise SubscriptionError("A reason is required for manual changes.")
    if days <= 0 or days > 3660:
        raise SubscriptionError("Extension must be between 1 and 3660 days.")
    if sub.status == SubscriptionStatus.REVOKED:
        raise SubscriptionError("A revoked subscription cannot be extended.")
    before = aware(sub.expires_at)
    sub.expires_at = before + timedelta(days=days)
    _audit(db, action="extend", user_id=sub.user_id, actor_user_id=admin.id,
           subscription_id=sub.id, payment_id=sub.payment_id, reason=reason,
           details={"days": days, "expires_before": before, "expires_after": sub.expires_at})
    db.commit()
    db.refresh(sub)
    logger.info("Admin %s extended sub %s by %d days", admin.id, sub.id, days)
    return sub


def revoke(db: Session, admin: User, sub: UserSubscription, *, reason: str) -> UserSubscription:
    """Revoke access. The payment record (if any) is untouched — refunds are
    handled in the gateway dashboard and are not implied by a revoke."""
    if not reason.strip():
        raise SubscriptionError("A reason is required for manual changes.")
    if sub.status == SubscriptionStatus.REVOKED:
        return sub
    sub.status = SubscriptionStatus.REVOKED
    sub.revoked_at = utcnow()
    _audit(db, action="revoke", user_id=sub.user_id, actor_user_id=admin.id,
           subscription_id=sub.id, payment_id=sub.payment_id, reason=reason,
           details={"expires_at": aware(sub.expires_at), "source": sub.source})
    db.commit()
    db.refresh(sub)
    logger.info("Admin %s revoked sub %s", admin.id, sub.id)
    return sub


__all__ = [
    "DEFAULT_PLANS",
    "EffectivePrice",
    "SubscriptionError",
    "access_ends_at",
    "activate_from_payment",
    "aware",
    "current_subscription",
    "derived_status",
    "effective_price",
    "extend",
    "get_active_plan",
    "grant",
    "has_active_subscription",
    "list_plans",
    "plan_features",
    "plan_snapshot",
    "record_plan_change",
    "revoke",
    "seed_default_plans",
    "start_trial",
    "utcnow",
]
