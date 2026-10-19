"""Admin subscription & plan management (admin session required).

GET    /api/admin/plans                         all plans (incl. inactive)
POST   /api/admin/plans                         create a plan
PATCH  /api/admin/plans/{id}                    edit price/duration/promo/features/activate
GET    /api/admin/subscriptions                 filterable list (user id/email, plan,
                                                status, source, gateway, expiry range)
GET    /api/admin/payments                      filterable payment list
GET    /api/admin/users/{id}/billing            one user's full history + audit trail
POST   /api/admin/users/{id}/subscriptions      manual grant (source=manual, no payment)
POST   /api/admin/subscriptions/{id}/extend     extend validity
POST   /api/admin/subscriptions/{id}/revoke     revoke access
POST   /api/admin/payments/{id}/reconcile       re-check a payment with its gateway
GET    /api/admin/subscription-audit            audit log

Every mutation needs the CSRF header and writes a subscription_audit_logs
row naming the admin. Plans are never deleted (deactivate instead) so
historical payments keep a valid plan reference; a price edit never touches
past payments (they store their own amount snapshot).
"""
import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import require_admin, require_admin_rate_limited, require_csrf
from app.db.session import get_db
from app.models.billing import (
    Payment,
    SubscriptionAuditLog,
    SubscriptionPlan,
    SubscriptionStatus,
    UserSubscription,
)
from app.models.user import User
from app.providers.payments.base import PaymentGatewayError
from app.schemas.billing import (
    AdminPaymentsPage,
    AdminSubscriptionRow,
    AdminSubscriptionsPage,
    AdminUserBilling,
    AuditOut,
    ExtendRequest,
    GrantRequest,
    PlanAdmin,
    PlanCreate,
    PlanUpdate,
    RevokeRequest,
    SubscriptionOut,
    VerifyResponse,
)
from app.services import payment_service, subscription_service as subs
from app.services.billing_views import audit_out, payment_out, plan_admin, subscription_out

logger = logging.getLogger(__name__)

# Every /admin route: admin only + per-admin-user rate limit (see
# require_admin_rate_limited). Applied once here so no endpoint can miss it.
router = APIRouter(
    prefix="/admin",
    tags=["admin-billing"],
    dependencies=[Depends(require_admin_rate_limited)],
)

SubStatusFilter = Literal["active", "scheduled", "expired", "revoked"]


# ------------------------------------------------------------------ plans


@router.get("/plans", response_model=list[PlanAdmin])
def list_plans(
    request: Request, _admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> list[PlanAdmin]:
    return [plan_admin(p) for p in subs.list_plans(db, include_inactive=True)]


def _apply_plan_fields(plan: SubscriptionPlan, body: PlanUpdate | PlanCreate) -> None:
    data = body.model_dump(exclude_unset=True, exclude={"clear_promo", "code"})
    features = data.pop("features", None)
    for key, value in data.items():
        setattr(plan, key, value.strip() if isinstance(value, str) and key != "currency" else value)
    if features is not None:
        plan.features_json = json.dumps(features)
    if body.clear_promo:
        plan.promo_price_minor = None
        plan.promo_label = ""
        plan.promo_starts_at = None
        plan.promo_ends_at = None
    if plan.promo_price_minor is not None and plan.promo_price_minor >= plan.price_minor:
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
    plan = SubscriptionPlan(code=body.code, name=body.name, price_minor=body.price_minor,
                            currency=body.currency, duration_days=body.duration_days)
    _apply_plan_fields(plan, body)
    db.add(plan)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A plan with this code already exists.") from exc
    subs.record_plan_change(db, plan, admin, "plan_create", None)
    db.commit()
    db.refresh(plan)
    logger.info("Admin %s created plan %s", admin.id, plan.code)
    return plan_admin(plan)


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
    subs.record_plan_change(db, plan, admin, "plan_update", before)
    db.commit()
    db.refresh(plan)
    logger.info("Admin %s updated plan %s", admin.id, plan.code)
    return plan_admin(plan)


# ---------------------------------------------------------- subscriptions


def _day_start(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


def _user_filter(column_id, column_email, q: str):
    """q matches a numeric user id exactly or an email substring."""
    q = q.strip()
    if q.isdigit():
        return or_(column_id == int(q), column_email.ilike(f"%{q}%"))
    return column_email.ilike(f"%{q}%")


@router.get("/subscriptions", response_model=AdminSubscriptionsPage)
def list_subscriptions(
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    q: str = Query(default="", max_length=320),
    plan_id: Optional[int] = Query(default=None, ge=1),
    status_: Optional[SubStatusFilter] = Query(default=None, alias="status"),
    source: Optional[Literal["payment", "manual", "trial"]] = None,
    gateway: Optional[Literal["razorpay", "cashfree", "mock"]] = None,
    expires_from: Optional[date] = None,
    expires_to: Optional[date] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
) -> AdminSubscriptionsPage:
    now = subs.utcnow()
    stmt = select(UserSubscription, Payment.gateway).outerjoin(
        Payment, Payment.id == UserSubscription.payment_id
    )
    if q.strip():
        stmt = stmt.where(_user_filter(UserSubscription.user_id, UserSubscription.user_email, q))
    if plan_id:
        stmt = stmt.where(UserSubscription.plan_id == plan_id)
    if source:
        stmt = stmt.where(UserSubscription.source == source)
    if gateway:
        stmt = stmt.where(Payment.gateway == gateway)
    if expires_from:
        stmt = stmt.where(UserSubscription.expires_at >= _day_start(expires_from))
    if expires_to:
        # Inclusive end date: everything before the NEXT day's midnight.
        stmt = stmt.where(UserSubscription.expires_at < _day_start(expires_to + timedelta(days=1)))
    live = UserSubscription.status == SubscriptionStatus.ACTIVE
    if status_ == "revoked":
        stmt = stmt.where(UserSubscription.status == SubscriptionStatus.REVOKED)
    elif status_ == "active":
        stmt = stmt.where(live, UserSubscription.starts_at <= now, UserSubscription.expires_at > now)
    elif status_ == "scheduled":
        stmt = stmt.where(live, UserSubscription.starts_at > now)
    elif status_ == "expired":
        stmt = stmt.where(live, UserSubscription.expires_at <= now)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(
        stmt.order_by(UserSubscription.expires_at.desc(), UserSubscription.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    items = [
        AdminSubscriptionRow(**subscription_out(sub, now).model_dump(), gateway=gw)
        for sub, gw in rows
    ]
    return AdminSubscriptionsPage(total=total, page=page, page_size=page_size, items=items)


@router.get("/payments", response_model=AdminPaymentsPage)
def list_payments(
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    q: str = Query(default="", max_length=320),
    plan_id: Optional[int] = Query(default=None, ge=1),
    status_: Optional[Literal["created", "paid", "failed", "cancelled"]] = Query(default=None, alias="status"),
    gateway: Optional[Literal["razorpay", "cashfree", "mock"]] = None,
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
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Payment.created_at.desc(), Payment.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return AdminPaymentsPage(total=total, page=page, page_size=page_size,
                             items=[payment_out(p) for p in rows])


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
    return AdminUserBilling(
        user_id=user.id, email=user.email, name=user.name or "",
        current=subscription_out(current, now) if current else None,
        access_ends_at=subs.access_ends_at(db, user_id, now),
        subscriptions=[subscription_out(s, now) for s in sub_rows],
        payments=[payment_out(p) for p in payment_service.payment_history(db, user_id, limit=500)],
        audit=[audit_out(a) for a in audit],
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
        sub = subs.grant(db, admin, user, plan_id=body.plan_id, days=body.days, reason=body.reason)
    except subs.SubscriptionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
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
        sub = subs.extend(db, admin, _get_sub(db, sub_id), days=body.days, reason=body.reason)
    except subs.SubscriptionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
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
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
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


@router.get("/subscription-audit", response_model=list[AuditOut])
def audit_log(
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    user_id: Optional[int] = Query(default=None, ge=1),
    action: Optional[str] = Query(default=None, max_length=30),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AuditOut]:
    stmt = select(SubscriptionAuditLog)
    if user_id:
        stmt = stmt.where(SubscriptionAuditLog.user_id == user_id)
    if action:
        stmt = stmt.where(SubscriptionAuditLog.action == action)
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
            out[key] = {"access_ends_at": ends, "plan_name": sub.plan_name}
    # Users with paid history but no live access still show "expired".
    paid = db.execute(
        select(Payment.user_id).where(Payment.user_id.in_(wanted), Payment.status == "paid").distinct()
    ).scalars().all()
    for uid in paid:
        out.setdefault(str(uid), {"access_ends_at": None, "plan_name": ""})
    return out


__all__ = ["router"]
