"""Admin subscription, pricing, payment and usage management (admin session required).

Plans & prices
GET    /api/admin/plans                         all plans (incl. inactive) + price history
POST   /api/admin/plans                         create a plan
PATCH  /api/admin/plans/{id}                    edit name/duration/credits/billing/visibility/...
GET    /api/admin/plans/{id}/prices             full price history of a plan
PUT    /api/admin/plans/{id}/prices             set the price in one currency (versioned)
GET    /api/admin/currencies                    currencies + whether payable right now
PATCH  /api/admin/currencies/{code}             show/hide, enable/disable payments
Subscriptions
GET    /api/admin/subscriptions                 filter/sort/paginate (user id/email/sub id/plan,
                                                status, source, gateway, currency, payment status,
                                                created/activation/expiry ranges)
GET    /api/admin/users/{id}/billing            one user's full history + credits + audit trail
POST   /api/admin/users/{id}/subscriptions      manual grant (source=manual, no payment)
POST   /api/admin/subscriptions/{id}/extend     extend validity
POST   /api/admin/subscriptions/{id}/revoke     revoke access
POST   /api/admin/subscriptions/{id}/cancel     cancel (access ends now)
POST   /api/admin/subscriptions/{id}/credits    correct the screenshot allowance
Payments & usage
GET    /api/admin/payments                      filterable payment list
GET    /api/admin/payments/{id}                 one payment + its webhook events
POST   /api/admin/payments/{id}/reconcile       re-check a payment with its gateway
GET    /api/admin/screenshot-usage              screenshot requests (credits, retries, failures)
GET    /api/admin/billing/overview              dashboard metrics (revenue PER currency)
GET    /api/admin/subscription-audit            audit log

Every route is admin-only on the SERVER (router dependency); hiding buttons
in Angular is not the protection. Every mutation needs the CSRF header and
writes a subscription_audit_logs row naming the admin. Plans are never
deleted (deactivate instead); a price edit never touches past payments
(they store their own amount snapshot); usage rows are never edited.
"""
import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import require_admin, require_admin_rate_limited, require_csrf
from app.db.session import get_db
from app.models.billing import (
    Payment,
    PaymentStatus,
    PaymentWebhookEvent,
    PlanPrice,
    ScreenshotUsage,
    SubscriptionAuditLog,
    SubscriptionPlan,
    SubscriptionStatus,
    UsageStatus,
    UserSubscription,
)
from app.models.user import User
from app.providers.payments.base import PaymentGatewayError
from app.providers.payments.registry import available_gateways
from app.schemas.billing import (
    AdminPaymentsPage,
    AdminSubscriptionRow,
    AdminSubscriptionsPage,
    AdminUsagePage,
    AdminUserBilling,
    AuditOut,
    BillingOverview,
    CreditsRequest,
    CurrencyAmount,
    CurrencyOut,
    CurrencyUpdate,
    ExtendRequest,
    GrantRequest,
    PaymentOut,
    PlanAdmin,
    PlanCreate,
    PlanUpdate,
    PriceOut,
    PriceSet,
    RevokeRequest,
    SubscriptionOut,
    VerifyResponse,
)
from app.services import payment_service, pricing_service as pricing, subscription_service as subs
from app.services import screenshot_credit_service as credits
from app.services.billing_views import (
    audit_out,
    payment_out,
    plan_admin,
    price_out,
    subscription_out,
    usage_out,
    usage_row,
)

logger = logging.getLogger(__name__)

# Every /admin route: admin only + per-admin-user rate limit (see
# require_admin_rate_limited). Applied once here so no endpoint can miss it.
router = APIRouter(
    prefix="/admin",
    tags=["admin-billing"],
    dependencies=[Depends(require_admin_rate_limited)],
)

SubStatusFilter = Literal["active", "scheduled", "expired", "revoked", "cancelled", "refunded"]
PayStatusFilter = Literal["created", "paid", "failed", "cancelled", "refunded", "disputed"]
GatewayFilter = Literal["razorpay", "cashfree", "mock"]
Order = Literal["asc", "desc"]


def _bad(exc: Exception, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(code, str(exc))


# ------------------------------------------------------------------ plans


def _plans_out(db: Session, plans: list[SubscriptionPlan]) -> list[PlanAdmin]:
    ids = [p.id for p in plans]
    rows = db.scalars(
        select(PlanPrice).where(PlanPrice.plan_id.in_(ids))
        .order_by(PlanPrice.currency, PlanPrice.effective_from.desc(), PlanPrice.id.desc())
    ).all() if ids else []
    current = {r.id for r in pricing.current_prices(db, ids).values()}
    by_plan: dict[int, list[PlanPrice]] = {}
    for r in rows:
        by_plan.setdefault(r.plan_id, []).append(r)
    return [plan_admin(p, by_plan.get(p.id, []), current) for p in plans]


@router.get("/plans", response_model=list[PlanAdmin])
def list_plans(
    request: Request, _admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> list[PlanAdmin]:
    return _plans_out(db, subs.list_plans(db, include_inactive=True))


def _apply_plan_fields(plan: SubscriptionPlan, body: PlanUpdate | PlanCreate) -> None:
    data = body.model_dump(
        exclude_unset=True,
        exclude={"clear_promo", "code", "reason", "price_minor", "duration_days", "duration_hours"},
    )
    features = data.pop("features", None)
    for key, value in data.items():
        setattr(plan, key, value.strip() if isinstance(value, str) and key != "currency" else value)
    hours = body.duration_hours or (body.duration_days * 24 if body.duration_days else None)
    if hours:
        plan.duration_hours = hours
        plan.duration_days = subs.days_for_hours(hours)
    if features is not None:
        plan.features_json = json.dumps(features)
    if body.clear_promo:
        plan.promo_price_minor = None
        plan.promo_label = ""
        plan.promo_starts_at = None
        plan.promo_ends_at = None
    if plan.billing_type == "one_time":
        plan.billing_interval_days = None
    elif plan.billing_type == "recurring" and not plan.billing_interval_days:
        plan.billing_interval_days = subs.days_for_hours(subs.plan_hours(plan))
    regular = body.price_minor if body.price_minor is not None else plan.price_minor
    if plan.promo_price_minor is not None and regular is not None and plan.promo_price_minor >= regular:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Promotional price must be below the regular price.")
    starts, ends = subs.aware(plan.promo_starts_at), subs.aware(plan.promo_ends_at)
    if starts and ends and ends <= starts:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Promotion end must be after its start.")


@router.post("/plans", response_model=PlanAdmin, status_code=201)
def create_plan(
    body: PlanCreate,
    request: Request,
    admin: User = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> PlanAdmin:
    hours = body.duration_hours or (body.duration_days or 0) * 24
    plan = SubscriptionPlan(code=body.code, name=body.name, price_minor=body.price_minor,
                            currency=body.currency, duration_hours=hours,
                            duration_days=subs.days_for_hours(hours))
    _apply_plan_fields(plan, body)
    db.add(plan)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A plan with this code already exists.") from exc
    try:
        pricing.set_price(db, admin, plan, currency=plan.currency, amount_minor=body.price_minor,
                          reason=body.reason or "Plan created")
    except subs.SubscriptionError as exc:
        db.rollback()
        raise _bad(exc) from exc
    subs.record_plan_change(db, plan, admin, "plan_create", None, body.reason or "")
    db.commit()
    db.refresh(plan)
    logger.info("Admin %s created plan %s", admin.id, plan.code)
    return _plans_out(db, [plan])[0]


@router.patch("/plans/{plan_id}", response_model=PlanAdmin)
def update_plan(
    plan_id: int,
    body: PlanUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> PlanAdmin:
    plan = db.get(SubscriptionPlan, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found.")
    before = subs.plan_snapshot(plan)
    _apply_plan_fields(plan, body)
    if body.price_minor is not None:
        try:
            pricing.set_price(db, admin, plan, currency=plan.currency, amount_minor=body.price_minor,
                              reason=body.reason or "Plan price edited")
        except subs.SubscriptionError as exc:
            db.rollback()
            raise _bad(exc) from exc
    subs.record_plan_change(db, plan, admin, "plan_update", before, body.reason or "")
    db.commit()
    db.refresh(plan)
    logger.info("Admin %s updated plan %s", admin.id, plan.code)
    return _plans_out(db, [plan])[0]


@router.get("/plans/{plan_id}/prices", response_model=list[PriceOut])
def plan_prices(
    plan_id: int, request: Request, _admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> list[PriceOut]:
    if db.get(SubscriptionPlan, plan_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found.")
    current = {r.id for r in pricing.current_prices(db, [plan_id]).values()}
    return [price_out(r, current) for r in pricing.price_history(db, plan_id)]


@router.put("/plans/{plan_id}/prices", response_model=PlanAdmin)
def set_plan_price(
    plan_id: int,
    body: PriceSet,
    request: Request,
    admin: User = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> PlanAdmin:
    """Set one currency's price. INR and USD are independent — nothing is
    converted. Past payments keep their own amounts."""
    plan = db.get(SubscriptionPlan, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found.")
    try:
        pricing.set_price(db, admin, plan, currency=body.currency, amount_minor=body.amount_minor,
                          is_active=body.is_active, effective_from=body.effective_from,
                          effective_to=body.effective_to, provider_refs=body.provider_refs,
                          reason=body.reason)
    except subs.SubscriptionError as exc:
        db.rollback()
        raise _bad(exc) from exc
    db.commit()
    db.refresh(plan)
    logger.info("Admin %s set %s price of plan %s", admin.id, body.currency, plan.code)
    return _plans_out(db, [plan])[0]


# -------------------------------------------------------------- currencies


def _currencies_out(db: Session) -> list[CurrencyOut]:
    gateways = available_gateways()
    live = pricing.count_prices(db)
    out = []
    for c in pricing.list_currencies(db):
        gws = [g["key"] for g in gateways if c.code in g["currencies"]]
        out.append(CurrencyOut(
            code=c.code, name=c.name, minor_digits=c.minor_digits,
            display_enabled=c.display_enabled, payments_enabled=c.payments_enabled,
            payable_now=bool(c.payments_enabled and gws), gateways=gws,
            sort_order=c.sort_order, live_prices=live.get(c.code, 0),
        ))
    return out


@router.get("/currencies", response_model=list[CurrencyOut])
def list_currencies(
    request: Request, _admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> list[CurrencyOut]:
    return _currencies_out(db)


@router.patch("/currencies/{code}", response_model=list[CurrencyOut])
def update_currency(
    code: str,
    body: CurrencyUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> list[CurrencyOut]:
    cur = pricing.get_currency(db, code)
    if cur is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Currency not found.")
    before = {"display_enabled": cur.display_enabled, "payments_enabled": cur.payments_enabled,
              "name": cur.name, "sort_order": cur.sort_order}
    for key, value in body.model_dump(exclude_unset=True, exclude={"reason"}).items():
        setattr(cur, key, value)
    if cur.code == "INR" and not cur.display_enabled:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "INR is the base currency and must stay visible.")
    after = {"display_enabled": cur.display_enabled, "payments_enabled": cur.payments_enabled,
             "name": cur.name, "sort_order": cur.sort_order}
    subs._audit(db, action="currency_update", actor_user_id=admin.id, reason=body.reason,
                details={"currency": cur.code, "before": before, "after": after})
    db.commit()
    logger.info("Admin %s updated currency %s: %s", admin.id, cur.code, after)
    return _currencies_out(db)


# ---------------------------------------------------------- subscriptions


def _day_start(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


def _range(stmt, column, start: Optional[date], end: Optional[date]):
    """Inclusive UTC date range on a timestamp column."""
    if start:
        stmt = stmt.where(column >= _day_start(start))
    if end:
        stmt = stmt.where(column < _day_start(end + timedelta(days=1)))
    return stmt


def _user_filter(column_id, column_email, q: str):
    """q matches a numeric user id exactly or an email substring."""
    q = q.strip()
    if q.isdigit():
        return or_(column_id == int(q), column_email.ilike(f"%{q}%"))
    return column_email.ilike(f"%{q}%")


_SUB_SORT = {
    "expires_at": UserSubscription.expires_at,
    "starts_at": UserSubscription.starts_at,
    "created_at": UserSubscription.created_at,
    "screenshots_used": UserSubscription.screenshots_used,
    "amount_minor": UserSubscription.amount_minor,
}


@router.get("/subscriptions", response_model=AdminSubscriptionsPage)
def list_subscriptions(
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    q: str = Query(default="", max_length=320, description="user id, email, #subscription id or plan name"),
    plan_id: Optional[int] = Query(default=None, ge=1),
    status_: Optional[SubStatusFilter] = Query(default=None, alias="status"),
    source: Optional[Literal["payment", "manual", "trial"]] = None,
    gateway: Optional[GatewayFilter] = None,
    currency: Optional[str] = Query(default=None, pattern=r"^[A-Za-z]{3}$"),
    payment_status: Optional[PayStatusFilter] = None,
    created_from: Optional[date] = None,
    created_to: Optional[date] = None,
    starts_from: Optional[date] = None,
    starts_to: Optional[date] = None,
    expires_from: Optional[date] = None,
    expires_to: Optional[date] = None,
    sort: Literal["expires_at", "starts_at", "created_at", "screenshots_used", "amount_minor"] = "expires_at",
    order: Order = "desc",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
) -> AdminSubscriptionsPage:
    now = subs.utcnow()
    stmt = select(UserSubscription, Payment.gateway, Payment.status, Payment.gateway_payment_id).outerjoin(
        Payment, Payment.id == UserSubscription.payment_id
    )
    term = q.strip()
    if term.startswith("#") and term[1:].isdigit():
        stmt = stmt.where(UserSubscription.id == int(term[1:]))
    elif term:
        stmt = stmt.where(or_(
            _user_filter(UserSubscription.user_id, UserSubscription.user_email, term),
            UserSubscription.id == (int(term) if term.isdigit() else -1),
            UserSubscription.plan_name.ilike(f"%{term}%"),
        ))
    if plan_id:
        stmt = stmt.where(UserSubscription.plan_id == plan_id)
    if source:
        stmt = stmt.where(UserSubscription.source == source)
    if gateway:
        stmt = stmt.where(Payment.gateway == gateway)
    if currency:
        stmt = stmt.where(UserSubscription.currency == currency.upper())
    if payment_status:
        stmt = stmt.where(Payment.status == payment_status)
    stmt = _range(stmt, UserSubscription.created_at, created_from, created_to)
    stmt = _range(stmt, UserSubscription.starts_at, starts_from, starts_to)
    stmt = _range(stmt, UserSubscription.expires_at, expires_from, expires_to)
    live = UserSubscription.status == SubscriptionStatus.ACTIVE
    if status_ in SubscriptionStatus.TERMINAL:
        stmt = stmt.where(UserSubscription.status == status_)
    elif status_ == "active":
        stmt = stmt.where(live, UserSubscription.starts_at <= now, UserSubscription.expires_at > now)
    elif status_ == "scheduled":
        stmt = stmt.where(live, UserSubscription.starts_at > now)
    elif status_ == "expired":
        stmt = stmt.where(live, UserSubscription.expires_at <= now)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    col = _SUB_SORT[sort]
    rows = db.execute(
        stmt.order_by(col.asc() if order == "asc" else col.desc(), UserSubscription.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    items = [
        AdminSubscriptionRow(**subscription_out(sub, now).model_dump(), gateway=gw,
                             payment_status=pst, gateway_payment_id=gpid)
        for sub, gw, pst, gpid in rows
    ]
    return AdminSubscriptionsPage(total=total, page=page, page_size=page_size, items=items)


# --------------------------------------------------------------- payments


@router.get("/payments", response_model=AdminPaymentsPage)
def list_payments(
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    q: str = Query(default="", max_length=320),
    plan_id: Optional[int] = Query(default=None, ge=1),
    status_: Optional[PayStatusFilter] = Query(default=None, alias="status"),
    gateway: Optional[GatewayFilter] = None,
    currency: Optional[str] = Query(default=None, pattern=r"^[A-Za-z]{3}$"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    sort: Literal["created_at", "paid_at", "amount_minor"] = "created_at",
    order: Order = "desc",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
) -> AdminPaymentsPage:
    stmt = select(Payment)
    if q.strip():
        ref = q.strip()
        stmt = stmt.where(or_(
            _user_filter(Payment.user_id, Payment.user_email, ref),
            Payment.gateway_order_id == ref,
            Payment.gateway_payment_id == ref,
            Payment.receipt == ref,
        ))
    if plan_id:
        stmt = stmt.where(Payment.plan_id == plan_id)
    if status_:
        stmt = stmt.where(Payment.status == status_)
    if gateway:
        stmt = stmt.where(Payment.gateway == gateway)
    if currency:
        stmt = stmt.where(Payment.currency == currency.upper())
    stmt = _range(stmt, Payment.created_at, date_from, date_to)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    col = {"created_at": Payment.created_at, "paid_at": Payment.paid_at, "amount_minor": Payment.amount_minor}[sort]
    rows = db.scalars(
        stmt.order_by(col.asc() if order == "asc" else col.desc(), Payment.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    ids = [p.id for p in rows]
    sub_ids = dict(db.execute(
        select(UserSubscription.payment_id, UserSubscription.id).where(UserSubscription.payment_id.in_(ids))
    ).all()) if ids else {}
    return AdminPaymentsPage(total=total, page=page, page_size=page_size,
                             items=[payment_out(p, sub_ids.get(p.id)) for p in rows])


@router.get("/payments/{payment_id}")
def payment_detail(
    payment_id: int, request: Request, _admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> dict:
    """One payment, its subscription and its webhook events (no raw payloads,
    no secrets; gateways never send card data anyway)."""
    p = db.get(Payment, payment_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found.")
    sub = db.scalar(select(UserSubscription).where(UserSubscription.payment_id == p.id))
    events = db.scalars(
        select(PaymentWebhookEvent).where(PaymentWebhookEvent.payment_id == p.id)
        .order_by(PaymentWebhookEvent.received_at.desc()).limit(50)
    ).all()
    return {
        "payment": payment_out(p, sub.id if sub else None).model_dump(mode="json"),
        "subscription": subscription_out(sub).model_dump(mode="json") if sub else None,
        "webhook_events": [
            {"id": e.id, "event_id": e.event_id, "event_type": e.event_type,
             "received_at": subs.aware(e.received_at), "processed_at": subs.aware(e.processed_at),
             "processing_error": e.processing_error}
            for e in events
        ],
    }


# ------------------------------------------------------------ user detail


@router.get("/users/{user_id}/billing", response_model=AdminUserBilling)
def user_billing(
    user_id: int, request: Request, _admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> AdminUserBilling:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    now = subs.utcnow()
    sub_rows = db.scalars(
        select(UserSubscription).where(UserSubscription.user_id == user_id)
        .order_by(UserSubscription.starts_at.desc(), UserSubscription.id.desc())
    ).all()
    audit = db.scalars(
        select(SubscriptionAuditLog).where(SubscriptionAuditLog.user_id == user_id)
        .order_by(SubscriptionAuditLog.created_at.desc(), SubscriptionAuditLog.id.desc()).limit(200)
    ).all()
    current = subs.current_subscription(db, user_id, now)
    by_payment = {s.payment_id: s.id for s in sub_rows if s.payment_id}
    return AdminUserBilling(
        user_id=user.id, email=user.email, name=user.name or "",
        current=subscription_out(current, now) if current else None,
        access_ends_at=subs.access_ends_at(db, user_id, now),
        subscriptions=[subscription_out(s, now) for s in sub_rows],
        payments=[payment_out(p, by_payment.get(p.id))
                  for p in payment_service.payment_history(db, user_id, limit=500)],
        audit=[audit_out(a) for a in audit],
        usage=usage_out(credits.summary(db, user_id, now)),
    )


@router.post("/users/{user_id}/subscriptions", response_model=SubscriptionOut, status_code=201)
def grant_subscription(
    user_id: int,
    body: GrantRequest,
    request: Request,
    admin: User = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> SubscriptionOut:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    try:
        sub = subs.grant(db, admin, user, plan_id=body.plan_id, days=body.days, hours=body.hours,
                         screenshot_credits=body.screenshot_credits, reason=body.reason)
    except subs.SubscriptionError as exc:
        raise _bad(exc) from exc
    return subscription_out(sub)


def _get_sub(db: Session, sub_id: int) -> UserSubscription:
    sub = db.get(UserSubscription, sub_id)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subscription not found.")
    return sub


@router.post("/subscriptions/{sub_id}/extend", response_model=SubscriptionOut)
def extend_subscription(
    sub_id: int,
    body: ExtendRequest,
    request: Request,
    admin: User = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> SubscriptionOut:
    try:
        sub = subs.extend(db, admin, _get_sub(db, sub_id), days=body.days, hours=body.hours, reason=body.reason)
    except subs.SubscriptionError as exc:
        raise _bad(exc) from exc
    return subscription_out(sub)


@router.post("/subscriptions/{sub_id}/revoke", response_model=SubscriptionOut)
def revoke_subscription(
    sub_id: int,
    body: RevokeRequest,
    request: Request,
    admin: User = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> SubscriptionOut:
    try:
        sub = subs.revoke(db, admin, _get_sub(db, sub_id), reason=body.reason)
    except subs.SubscriptionError as exc:
        raise _bad(exc) from exc
    return subscription_out(sub)


@router.post("/subscriptions/{sub_id}/cancel", response_model=SubscriptionOut)
def cancel_subscription(
    sub_id: int,
    body: RevokeRequest,
    request: Request,
    admin: User = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> SubscriptionOut:
    try:
        sub = subs.cancel(db, admin, _get_sub(db, sub_id), reason=body.reason)
    except subs.SubscriptionError as exc:
        raise _bad(exc) from exc
    return subscription_out(sub)


@router.post("/subscriptions/{sub_id}/credits", response_model=SubscriptionOut)
def adjust_credits(
    sub_id: int,
    body: CreditsRequest,
    request: Request,
    admin: User = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> SubscriptionOut:
    try:
        sub = subs.adjust_credits(db, admin, _get_sub(db, sub_id), allowance=body.allowance, reason=body.reason)
    except subs.SubscriptionError as exc:
        raise _bad(exc) from exc
    return subscription_out(sub)


@router.post("/payments/{payment_id}/reconcile", response_model=VerifyResponse)
async def reconcile_payment(
    payment_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> VerifyResponse:
    try:
        result = await payment_service.reconcile(db, payment_id)
    except payment_service.PaymentError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except PaymentGatewayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc) or "Gateway error.") from exc
    logger.info("Admin %s reconciled payment %s -> %s", admin.id, payment_id, result.status)
    return VerifyResponse(**result.__dict__)


# ------------------------------------------------------------ usage


@router.get("/screenshot-usage", response_model=AdminUsagePage)
def screenshot_usage(
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    q: str = Query(default="", max_length=320, description="user id, email or request id"),
    plan_id: Optional[int] = Query(default=None, ge=1),
    subscription_id: Optional[int] = Query(default=None, ge=1),
    status_: Optional[Literal["reserved", "completed", "failed"]] = Query(default=None, alias="status"),
    retried: bool = Query(default=False, description="only requests submitted more than once"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> AdminUsagePage:
    stmt = select(ScreenshotUsage, UserSubscription.plan_name).outerjoin(
        UserSubscription, UserSubscription.id == ScreenshotUsage.subscription_id
    )
    if q.strip():
        term = q.strip()
        stmt = stmt.where(or_(_user_filter(ScreenshotUsage.user_id, ScreenshotUsage.user_email, term),
                              ScreenshotUsage.request_id == term))
    if plan_id:
        stmt = stmt.where(UserSubscription.plan_id == plan_id)
    if subscription_id:
        stmt = stmt.where(ScreenshotUsage.subscription_id == subscription_id)
    if status_:
        stmt = stmt.where(ScreenshotUsage.status == status_)
    if retried:
        stmt = stmt.where(ScreenshotUsage.attempts > 1)
    stmt = _range(stmt, ScreenshotUsage.created_at, date_from, date_to)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(
        stmt.order_by(ScreenshotUsage.created_at.desc(), ScreenshotUsage.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return AdminUsagePage(total=total, page=page, page_size=page_size,
                          items=[usage_row(u, plan or "") for u, plan in rows])


# --------------------------------------------------------- overview


@router.get("/billing/overview", response_model=BillingOverview)
def billing_overview(
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> BillingOverview:
    """Aggregates computed in SQL. Revenue is grouped BY CURRENCY and never
    summed across currencies. Date range applies to payment creation,
    subscription creation and screenshot requests; subscription state
    counts (active/expired) are as of now."""
    now = subs.utcnow()
    start = _day_start(date_from) if date_from else None
    end = _day_start(date_to + timedelta(days=1)) if date_to else None

    def window(stmt, column):
        if start:
            stmt = stmt.where(column >= start)
        if end:
            stmt = stmt.where(column < end)
        return stmt

    live = UserSubscription.status == SubscriptionStatus.ACTIVE
    sub_counts = db.execute(window(select(
        func.sum(case((live & (UserSubscription.starts_at <= now) & (UserSubscription.expires_at > now), 1), else_=0)),
        func.sum(case((live & (UserSubscription.expires_at <= now), 1), else_=0)),
        func.sum(case((UserSubscription.status.in_(SubscriptionStatus.TERMINAL), 1), else_=0)),
    ), UserSubscription.created_at)).one()
    by_status = dict(db.execute(window(
        select(Payment.status, func.count(Payment.id)).group_by(Payment.status), Payment.created_at
    )).all())

    def money(status_value: str, ts_col) -> list[CurrencyAmount]:
        rows = db.execute(window(
            select(Payment.currency, func.sum(Payment.amount_minor), func.count(Payment.id))
            .where(Payment.status == status_value).group_by(Payment.currency).order_by(Payment.currency),
            ts_col,
        )).all()
        return [CurrencyAmount(currency=c, amount_minor=int(a or 0), count=n) for c, a, n in rows]

    by_plan = dict(db.execute(window(
        select(UserSubscription.plan_name, func.count(UserSubscription.id)).group_by(UserSubscription.plan_name),
        UserSubscription.created_at,
    )).all())
    usage = db.execute(window(select(
        func.count(ScreenshotUsage.id),
        func.coalesce(func.sum(ScreenshotUsage.credits_consumed), 0),
        func.sum(case((ScreenshotUsage.status == UsageStatus.FAILED, 1), else_=0)),
    ), ScreenshotUsage.created_at)).one()
    paid = by_status.get(PaymentStatus.PAID, 0) + by_status.get(PaymentStatus.REFUNDED, 0) \
        + by_status.get(PaymentStatus.DISPUTED, 0)
    failed = by_status.get(PaymentStatus.FAILED, 0)
    return BillingOverview(
        date_from=start, date_to=end,
        total_users=db.scalar(window(select(func.count(User.id)), User.created_at)) or 0,
        active_subscriptions=int(sub_counts[0] or 0),
        expired_subscriptions=int(sub_counts[1] or 0),
        ended_subscriptions=int(sub_counts[2] or 0),
        payments_by_status={k: int(v) for k, v in by_status.items()},
        revenue_by_currency=money(PaymentStatus.PAID, Payment.paid_at),
        refunds_by_currency=money(PaymentStatus.REFUNDED, Payment.refunded_at),
        subscriptions_by_plan={k: int(v) for k, v in by_plan.items()},
        screenshot_requests=int(usage[0] or 0),
        screenshots_analyzed=int(usage[1] or 0),
        screenshot_failures=int(usage[2] or 0),
        payment_success_rate=round(paid / (paid + failed), 4) if (paid + failed) else None,
    )


# ------------------------------------------------------------- audit


@router.get("/subscription-audit", response_model=list[AuditOut])
def audit_log(
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    user_id: Optional[int] = Query(default=None, ge=1),
    action: Optional[str] = Query(default=None, max_length=30),
    actor_user_id: Optional[int] = Query(default=None, ge=1),
    plan_id: Optional[int] = Query(default=None, ge=1),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AuditOut]:
    stmt = select(SubscriptionAuditLog)
    if user_id:
        stmt = stmt.where(SubscriptionAuditLog.user_id == user_id)
    if action:
        stmt = stmt.where(SubscriptionAuditLog.action == action)
    if actor_user_id:
        stmt = stmt.where(SubscriptionAuditLog.actor_user_id == actor_user_id)
    if plan_id:
        stmt = stmt.where(SubscriptionAuditLog.plan_id == plan_id)
    stmt = _range(stmt, SubscriptionAuditLog.created_at, date_from, date_to)
    rows = db.scalars(
        stmt.order_by(SubscriptionAuditLog.created_at.desc(), SubscriptionAuditLog.id.desc()).limit(limit)
    ).all()
    return [audit_out(a) for a in rows]


# ---------------------------------------------- per-user summary for lists


@router.get("/users/billing-summary")
def users_billing_summary(
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    ids: str = Query(default="", max_length=2000, description="Comma-separated user ids"),
) -> dict[str, dict]:
    """Current access end + plan for a page of users (admin user table)."""
    wanted = [int(x) for x in ids.split(",") if x.strip().isdigit()][:200]
    if not wanted:
        return {}
    now = subs.utcnow()
    rows = db.scalars(
        select(UserSubscription).where(
            UserSubscription.user_id.in_(wanted),
            UserSubscription.status == SubscriptionStatus.ACTIVE,
            UserSubscription.expires_at > now,
        )
    ).all()
    out: dict[str, dict] = {}
    for sub in rows:
        key = str(sub.user_id)
        ends = subs.aware(sub.expires_at)
        prev = out.get(key)
        if prev is None or ends > prev["access_ends_at"]:
            out[key] = {"access_ends_at": ends, "plan_name": sub.plan_name,
                        "screenshots_remaining": subs.credits_remaining(sub)}
    # Users with paid history but no live access still show "expired".
    paid = db.execute(
        select(Payment.user_id).where(Payment.user_id.in_(wanted), Payment.status == "paid").distinct()
    ).scalars().all()
    for uid in paid:
        out.setdefault(str(uid), {"access_ends_at": None, "plan_name": ""})
    return out


__all__ = ["router"]
