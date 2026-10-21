"""ORM → response-schema mapping shared by the user and admin billing routers."""
from __future__ import annotations

import json
from datetime import datetime

from app.models.billing import (
    Payment,
    PlanPrice,
    ScreenshotUsage,
    SubscriptionAuditLog,
    SubscriptionPlan,
    UserSubscription,
)
from app.schemas.billing import (
    AuditOut,
    PaymentOut,
    PlanAdmin,
    PlanPublic,
    PriceOut,
    SubscriptionOut,
    UsageOut,
    UsageRow,
)
from app.services.pricing_service import Quote
from app.services.screenshot_credit_service import UsageSummary
from app.services.subscription_service import (
    aware,
    credits_remaining,
    derived_status,
    effective_price,
    plan_features,
    plan_hours,
)


def plan_public(
    plan: SubscriptionPlan,
    now: datetime | None = None,
    *,
    quote: Quote | None = None,
    currency: str | None = None,
    payable: bool = True,
) -> PlanPublic:
    """Public view of a plan priced in ``currency``.

    Without ``currency`` the plan's primary-currency price is used. With a
    currency but no ``quote`` the plan is reported as not available in it.
    """
    if currency is None:
        price = effective_price(plan, now)
        amount, regular, is_promo, cur, available = (
            price.amount_minor, price.regular_minor, price.is_promo, price.currency, True
        )
    elif quote is None:
        amount, regular, is_promo, cur, available = 0, 0, False, currency, False
    else:
        amount, regular, is_promo, cur, available = (
            quote.amount_minor, quote.regular_minor, quote.is_promo, quote.currency, True
        )
    return PlanPublic(
        id=plan.id, code=plan.code, name=plan.name, description=plan.description,
        currency=cur, duration_days=plan.duration_days, duration_hours=plan_hours(plan),
        duration_unit=plan.duration_unit or "days", screenshot_allowance=plan.screenshot_allowance,
        billing_type=plan.billing_type, billing_interval_days=plan.billing_interval_days,
        auto_renew_available=False, trial_days=plan.trial_days,
        available=available, payable=available and payable,
        price_minor=amount, regular_price_minor=regular,
        is_promo=is_promo, promo_label=plan.promo_label if is_promo else "",
        promo_ends_at=aware(plan.promo_ends_at) if is_promo else None,
        features=plan_features(plan), badge=plan.badge, is_highlighted=plan.is_highlighted,
    )


def price_out(row: PlanPrice, current_ids: set[int] | None = None) -> PriceOut:
    try:
        refs = json.loads(row.provider_refs_json or "{}")
    except ValueError:
        refs = {}
    return PriceOut(
        id=row.id, plan_id=row.plan_id, currency=row.currency, amount_minor=row.amount_minor,
        is_active=row.is_active, effective_from=aware(row.effective_from),
        effective_to=aware(row.effective_to), provider_refs=refs if isinstance(refs, dict) else {},
        is_current=bool(current_ids and row.id in current_ids),
    )


def plan_admin(
    plan: SubscriptionPlan, prices: list[PlanPrice] | None = None, current_ids: set[int] | None = None
) -> PlanAdmin:
    base = plan_public(plan).model_dump()
    base["promo_label"] = plan.promo_label
    base["promo_ends_at"] = aware(plan.promo_ends_at)
    return PlanAdmin(
        **base, is_active=plan.is_active, is_public=plan.is_public, sort_order=plan.sort_order,
        promo_price_minor=plan.promo_price_minor, promo_starts_at=aware(plan.promo_starts_at),
        created_at=aware(plan.created_at), updated_at=aware(plan.updated_at),
        prices=[price_out(p, current_ids) for p in (prices or [])],
    )


def subscription_out(sub: UserSubscription, now: datetime | None = None) -> SubscriptionOut:
    return SubscriptionOut(
        id=sub.id, user_id=sub.user_id, user_email=sub.user_email, plan_id=sub.plan_id,
        plan_code=sub.plan_code, plan_name=sub.plan_name, status=derived_status(sub, now),
        source=sub.source, starts_at=aware(sub.starts_at), expires_at=aware(sub.expires_at),
        amount_minor=sub.amount_minor, currency=sub.currency, is_renewal=sub.is_renewal,
        payment_id=sub.payment_id, screenshot_allowance=sub.screenshot_allowance or 0,
        screenshots_used=sub.screenshots_used or 0, screenshots_remaining=credits_remaining(sub),
        billing_type=sub.billing_type or "one_time", auto_renew=bool(sub.auto_renew),
        notes=sub.notes, revoked_at=aware(sub.revoked_at), cancelled_at=aware(sub.cancelled_at),
        created_at=aware(sub.created_at),
    )


def payment_out(p: Payment, subscription_id: int | None = None) -> PaymentOut:
    return PaymentOut(
        id=p.id, user_id=p.user_id, user_email=p.user_email, plan_id=p.plan_id,
        plan_code=p.plan_code, plan_name=p.plan_name, duration_days=p.duration_days,
        duration_hours=p.duration_hours or p.duration_days * 24,
        screenshot_allowance=p.screenshot_allowance or 0,
        amount_minor=p.amount_minor, currency=p.currency, gateway=p.gateway, receipt=p.receipt,
        gateway_order_id=p.gateway_order_id, gateway_payment_id=p.gateway_payment_id,
        status=p.status, method=p.method, failure_reason=p.failure_reason,
        verified_via=p.verified_via, is_renewal=p.is_renewal,
        created_at=aware(p.created_at), paid_at=aware(p.paid_at),
        refunded_at=aware(p.refunded_at), refund_reference=p.refund_reference or "",
        subscription_id=subscription_id,
    )


def audit_out(a: SubscriptionAuditLog) -> AuditOut:
    try:
        details = json.loads(a.details_json or "{}")
    except ValueError:
        details = {}
    return AuditOut(
        id=a.id, user_id=a.user_id, user_email=a.user_email or "", plan_id=a.plan_id,
        subscription_id=a.subscription_id, payment_id=a.payment_id,
        actor_user_id=a.actor_user_id, action=a.action,
        reason=a.reason, details=details if isinstance(details, dict) else {},
        created_at=aware(a.created_at),
    )


def usage_out(s: UsageSummary) -> UsageOut:
    return UsageOut(
        subscription_id=s.subscription_id, plan_name=s.plan_name, status=s.status,
        allowance=s.allowance, used=s.used, remaining=s.remaining, expires_at=s.expires_at,
        total_remaining=s.total_remaining,
    )


def usage_row(u: ScreenshotUsage, plan_name: str = "") -> UsageRow:
    return UsageRow(
        id=u.id, user_id=u.user_id, user_email=u.user_email, subscription_id=u.subscription_id,
        plan_name=plan_name, request_id=u.request_id, status=u.status, credits=u.credits,
        credits_consumed=u.credits_consumed, attempts=u.attempts, failure_code=u.failure_code,
        created_at=aware(u.created_at), completed_at=aware(u.completed_at),
    )
