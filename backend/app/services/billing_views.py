"""ORM → response-schema mapping shared by the user and admin billing routers."""
from __future__ import annotations

import json
from datetime import datetime

from app.models.billing import Payment, SubscriptionAuditLog, SubscriptionPlan, UserSubscription
from app.schemas.billing import AuditOut, PaymentOut, PlanAdmin, PlanPublic, SubscriptionOut
from app.services.subscription_service import aware, derived_status, effective_price, plan_features


def plan_public(plan: SubscriptionPlan, now: datetime | None = None) -> PlanPublic:
    price = effective_price(plan, now)
    return PlanPublic(
        id=plan.id, code=plan.code, name=plan.name, description=plan.description,
        currency=plan.currency, duration_days=plan.duration_days, trial_days=plan.trial_days,
        price_minor=price.amount_minor, regular_price_minor=price.regular_minor,
        is_promo=price.is_promo, promo_label=plan.promo_label if price.is_promo else "",
        promo_ends_at=aware(plan.promo_ends_at) if price.is_promo else None,
        features=plan_features(plan), badge=plan.badge, is_highlighted=plan.is_highlighted,
    )


def plan_admin(plan: SubscriptionPlan) -> PlanAdmin:
    base = plan_public(plan).model_dump()
    base["promo_label"] = plan.promo_label
    base["promo_ends_at"] = aware(plan.promo_ends_at)
    return PlanAdmin(
        **base, is_active=plan.is_active, sort_order=plan.sort_order,
        promo_price_minor=plan.promo_price_minor, promo_starts_at=aware(plan.promo_starts_at),
        created_at=aware(plan.created_at), updated_at=aware(plan.updated_at),
    )


def subscription_out(sub: UserSubscription, now: datetime | None = None) -> SubscriptionOut:
    return SubscriptionOut(
        id=sub.id, user_id=sub.user_id, user_email=sub.user_email, plan_id=sub.plan_id,
        plan_code=sub.plan_code, plan_name=sub.plan_name, status=derived_status(sub, now),
        source=sub.source, starts_at=aware(sub.starts_at), expires_at=aware(sub.expires_at),
        amount_minor=sub.amount_minor, currency=sub.currency, is_renewal=sub.is_renewal,
        payment_id=sub.payment_id, notes=sub.notes, revoked_at=aware(sub.revoked_at),
        created_at=aware(sub.created_at),
    )


def payment_out(p: Payment) -> PaymentOut:
    return PaymentOut(
        id=p.id, user_id=p.user_id, user_email=p.user_email, plan_id=p.plan_id,
        plan_code=p.plan_code, plan_name=p.plan_name, duration_days=p.duration_days,
        amount_minor=p.amount_minor, currency=p.currency, gateway=p.gateway, receipt=p.receipt,
        gateway_order_id=p.gateway_order_id, gateway_payment_id=p.gateway_payment_id,
        status=p.status, method=p.method, failure_reason=p.failure_reason,
        verified_via=p.verified_via, is_renewal=p.is_renewal,
        created_at=aware(p.created_at), paid_at=aware(p.paid_at),
    )


def audit_out(a: SubscriptionAuditLog) -> AuditOut:
    try:
        details = json.loads(a.details_json or "{}")
    except ValueError:
        details = {}
    return AuditOut(
        id=a.id, user_id=a.user_id, plan_id=a.plan_id, subscription_id=a.subscription_id,
        payment_id=a.payment_id, actor_user_id=a.actor_user_id, action=a.action,
        reason=a.reason, details=details if isinstance(details, dict) else {},
        created_at=aware(a.created_at),
    )
