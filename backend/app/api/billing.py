"""Public pricing, user billing, checkout/verification and gateway webhooks.

GET  /api/plans                          public, database-driven pricing
GET  /api/payments/gateways              payment methods available right now
GET  /api/billing/me                     the caller's subscription + history
POST /api/billing/trial                  start the one-time free trial (if offered)
POST /api/payments/checkout              create a gateway order (server-priced)
POST /api/payments/{gateway}/verify      confirm a checkout server-side
POST /api/payments/webhooks/{gateway}    gateway → us (signature-verified)
POST /api/payments/mock/simulate         DEV ONLY: simulate the mock checkout

Users only ever see and verify their OWN records (queries are scoped by the
session user id). Webhooks carry no session: they are authenticated by the
gateway signature over the raw body and exempt from CSRF by design.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import require_user, require_user_csrf
from app.core.config import get_settings
from app.core.ratelimit import rate_limit, rate_limit_key
from app.db.session import get_db
from app.models.billing import SubscriptionSource, UserSubscription
from app.models.user import User
from app.providers.payments.base import (
    GatewayNotConfigured,
    PaymentGatewayError,
    WebhookSignatureError,
)
from app.providers.payments.mock import MockGateway
from app.providers.payments.registry import available_gateways, get_gateway
from app.schemas.billing import (
    CheckoutRequest,
    CheckoutResponse,
    GatewayOut,
    MockSimulateRequest,
    MyBilling,
    PlanPublic,
    SubscriptionOut,
    TrialRequest,
    VerifyRequest,
    VerifyResponse,
)
from app.services import payment_service, subscription_service as subs
from app.services.billing_views import payment_out, plan_public, subscription_out

logger = logging.getLogger(__name__)

router = APIRouter(tags=["billing"])

#: Webhook bodies are small JSON; refuse anything larger before hashing it.
_MAX_WEBHOOK_BYTES = 256 * 1024


def _gateway_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, GatewayNotConfigured):
        return HTTPException(status.HTTP_400_BAD_REQUEST, "This payment method is not available.")
    return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc) or "Payment gateway error.")


# ---------------------------------------------------------------- public


@router.get("/plans", response_model=list[PlanPublic])
def public_plans(db: Session = Depends(get_db)) -> list[PlanPublic]:
    """Active plans with the price checkout will charge right now."""
    return [plan_public(p) for p in subs.list_plans(db)]


@router.get("/payments/gateways", response_model=list[GatewayOut])
def gateways() -> list[GatewayOut]:
    return [GatewayOut(**g) for g in available_gateways()]


# ------------------------------------------------------------------ user


@router.get("/billing/me", response_model=MyBilling)
def my_billing(user: User = Depends(require_user), db: Session = Depends(get_db)) -> MyBilling:
    now = subs.utcnow()
    rows = db.scalars(
        select(UserSubscription)
        .where(UserSubscription.user_id == user.id)
        .order_by(UserSubscription.starts_at.desc(), UserSubscription.id.desc())
    ).all()
    current = subs.current_subscription(db, user.id, now)
    trial_used = bool(
        db.scalar(
            select(func.count(UserSubscription.id)).where(
                UserSubscription.user_id == user.id,
                UserSubscription.source == SubscriptionSource.TRIAL,
            )
        )
    )
    return MyBilling(
        current=subscription_out(current, now) if current else None,
        access_ends_at=subs.access_ends_at(db, user.id, now),
        subscriptions=[subscription_out(s, now) for s in rows],
        payments=[payment_out(p) for p in payment_service.payment_history(db, user.id)],
        trial_used=trial_used,
    )


@router.post("/billing/trial", response_model=SubscriptionOut, status_code=201)
def start_trial(
    body: TrialRequest, user: User = Depends(require_user_csrf), db: Session = Depends(get_db)
) -> SubscriptionOut:
    try:
        sub = subs.start_trial(db, user, body.plan_id)
    except subs.SubscriptionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return subscription_out(sub)


# -------------------------------------------------------------- checkout


@router.post("/payments/checkout", response_model=CheckoutResponse, status_code=201)
async def checkout(
    body: CheckoutRequest, user: User = Depends(require_user_csrf), db: Session = Depends(get_db)
) -> CheckoutResponse:
    rate_limit_key("checkout", str(user.id), 10)
    try:
        result = await payment_service.create_checkout(
            db, user, plan_id=body.plan_id, gateway_name=body.gateway, phone=body.phone
        )
    except (subs.SubscriptionError, payment_service.PaymentError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except PaymentGatewayError as exc:
        raise _gateway_http_error(exc) from exc
    return CheckoutResponse(**result.__dict__)


@router.post("/payments/{gateway}/verify", response_model=VerifyResponse)
async def verify(
    gateway: str,
    body: VerifyRequest,
    user: User = Depends(require_user_csrf),
    db: Session = Depends(get_db),
) -> VerifyResponse:
    rate_limit_key("verify", str(user.id), 30)
    try:
        result = await payment_service.verify_client_payment(
            db, user, gateway_name=gateway, order_id=body.order_id, payload=body.payload
        )
    except payment_service.PaymentError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except PaymentGatewayError as exc:
        raise _gateway_http_error(exc) from exc
    return VerifyResponse(**result.__dict__)


# -------------------------------------------------------------- webhooks


@router.post("/payments/webhooks/{gateway}")
async def webhook(gateway: str, request: Request, db: Session = Depends(get_db)) -> dict:
    rate_limit(request, "payment-webhook", 600)
    raw = await request.body()
    if len(raw) > _MAX_WEBHOOK_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Payload too large.")
    headers = dict(request.headers)
    try:
        result = await run_in_threadpool(payment_service.handle_webhook, db, gateway, headers, raw)
    except GatewayNotConfigured as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown webhook.") from exc
    except WebhookSignatureError as exc:
        logger.warning("Rejected %s webhook: %s", gateway, exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid signature.") from exc
    # 2xx tells the gateway to stop retrying (also for duplicates/unknown orders).
    return {"status": result.status}


# ------------------------------------------------------- dev-only mock


@router.post("/payments/mock/simulate")
def mock_simulate(body: MockSimulateRequest, user: User = Depends(require_user_csrf), db: Session = Depends(get_db)) -> dict:
    """Stand-in for a gateway popup during development. 404 when disabled."""
    if not get_settings().payments_mock_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    # Only the order's owner may simulate paying it.
    try:
        payment_service.get_owned_payment(db, user.id, "mock", body.order_id)
    except payment_service.PaymentError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    gateway = get_gateway("mock")
    assert isinstance(gateway, MockGateway)
    try:
        return gateway.simulate(body.order_id, body.succeed)
    except PaymentGatewayError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
