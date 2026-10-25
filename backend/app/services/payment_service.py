"""Payment orchestration: checkout → verification → subscription activation.

Rules enforced here (independent of which gateway is used):

* The amount is computed on the server from the plan (promo-aware) and
  snapshotted on the ``payments`` row; the browser never supplies a price.
* A subscription is activated ONLY after the gateway's own API (client
  verify / reconcile) or a signature-verified webhook says the order is paid
  for the expected amount and currency. A browser "success" callback by
  itself proves nothing.
* Activation is idempotent and race-safe: the payment row is locked
  (``SELECT … FOR UPDATE`` on PostgreSQL), an already-paid payment is
  returned as-is, and ``user_subscriptions.payment_id`` is unique.
* Webhooks are deduplicated by ``(gateway, event_id)``; a retried event is
  acknowledged without re-processing.
* Network calls to the gateway happen OUTSIDE the DB transaction/lock.

Async functions call gateways; DB work runs in a worker thread via
``asyncio.to_thread`` so the event loop (live interviews) never blocks.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass
from typing import Mapping

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.billing import (
    Payment,
    PaymentStatus,
    PaymentWebhookEvent,
    SubscriptionPlan,
    SubscriptionStatus,
)
from app.models.user import User
from app.providers.payments.base import (
    CustomerInfo,
    PaymentGateway,
    VerifiedPayment,
    WebhookEvent,
)
from app.providers.payments.registry import gateway_supports, get_gateway
from app.services import payment_settings_service as payment_settings
from app.services import pricing_service as pricing
from app.services import subscription_service as subs

logger = logging.getLogger(__name__)


class PaymentError(ValueError):
    """Business-rule failure safe to show to the user."""


class DuplicateCheckout(PaymentError):
    """The same idempotency key was already used for a checkout."""


class PaymentsUnavailable(PaymentError):
    """An admin switched online payments off for the whole site."""


@dataclass(frozen=True)
class CheckoutResult:
    payment_id: int
    gateway: str
    order_id: str
    amount_minor: int
    currency: str
    checkout: dict


@dataclass(frozen=True)
class VerifyResult:
    status: str  # paid | pending | failed
    payment_id: int
    subscription_id: int | None = None
    message: str = ""


# ----------------------------------------------------------------- checkout


def _new_receipt(user_id: int) -> str:
    # <=40 chars (Razorpay receipt limit), [A-Za-z0-9_] (Cashfree order_id rules).
    return f"oi_{user_id}_{secrets.token_hex(8)}"


def _cancel_stale_orders(db: Session, user_id: int, plan_id: int) -> None:
    """Older unpaid orders for the same plan are superseded locally.

    A superseded order that is somehow paid later is STILL honoured by the
    webhook (status moves cancelled → paid), so no customer money is lost.
    """
    db.execute(
        update(Payment)
        .where(
            Payment.user_id == user_id,
            Payment.plan_id == plan_id,
            Payment.status == PaymentStatus.CREATED,
        )
        .values(status=PaymentStatus.CANCELLED, failure_reason="Superseded by a newer checkout.")
        .execution_options(synchronize_session=False)
    )


def _quote_for_checkout(
    db: Session, user_id: int, plan_id: int, currency: str, gateway: str, idempotency_key: str | None
) -> tuple[SubscriptionPlan, pricing.Quote]:
    """Every check that must pass BEFORE an order is created at the gateway."""
    try:
        payment_settings.assert_checkout_allowed(db, gateway)
    except payment_settings.PaymentsDisabled as exc:
        raise PaymentsUnavailable(str(exc)) from exc
    except payment_settings.GatewayNotSelectable as exc:
        raise PaymentError(str(exc)) from exc
    if idempotency_key and db.scalar(select(Payment.id).where(
        Payment.user_id == user_id, Payment.idempotency_key == idempotency_key
    )):
        raise DuplicateCheckout("This checkout was already started. Refresh and try again.")
    plan = subs.get_active_plan(db, plan_id)
    cur = pricing.get_currency(db, currency)
    if cur is None or not cur.display_enabled or not cur.payments_enabled:
        raise PaymentError(f"Payments in {currency} are not available yet.")
    if not gateway_supports(gateway, currency):
        raise PaymentError(f"This payment method does not accept {currency}.")
    quote = pricing.quote(plan, currency, pricing.current_prices(db, [plan.id]))
    if quote is None:
        raise PaymentError(f"This plan is not available in {currency} yet.")
    if quote.amount_minor <= 0:
        raise PaymentError("This plan cannot be purchased online.")
    return plan, quote


async def create_checkout(
    db: Session, user: User, *, plan_id: int, gateway_name: str, phone: str = "",
    currency: str = "INR", idempotency_key: str | None = None,
) -> CheckoutResult:
    """Create a gateway order for a plan at the SERVER's price in ``currency``.

    The browser chooses only the plan, currency and gateway; the amount comes
    from ``plan_prices``. The currency must be enabled for payments and
    supported by the chosen gateway — it is never silently switched.
    """
    gateway = get_gateway(gateway_name)  # raises GatewayNotConfigured
    currency = (currency or "INR").upper()
    plan, price = await asyncio.to_thread(
        _quote_for_checkout, db, user.id, plan_id, currency, gateway.name, idempotency_key
    )
    receipt = _new_receipt(user.id)
    order = await gateway.create_order(
        receipt=receipt,
        amount_minor=price.amount_minor,
        currency=price.currency,
        customer=CustomerInfo(user_id=user.id, email=user.email, name=user.name or "", phone=phone),
        description=f"OyeInterview {plan.name}",
    )

    def _persist() -> Payment:
        _cancel_stale_orders(db, user.id, plan.id)
        payment = Payment(
            user_id=user.id,
            user_email=user.email,
            plan_id=plan.id,
            plan_code=plan.code,
            plan_name=plan.name,
            duration_days=plan.duration_days,
            duration_hours=subs.plan_hours(plan),
            screenshot_allowance=plan.screenshot_allowance,
            amount_minor=price.amount_minor,
            currency=price.currency,
            gateway=gateway.name,
            idempotency_key=idempotency_key or None,
            receipt=receipt,
            gateway_order_id=order.order_id,
            status=PaymentStatus.CREATED,
        )
        db.add(payment)
        try:
            db.commit()
        except IntegrityError as exc:  # same key raced in from a second tab/click
            db.rollback()
            raise DuplicateCheckout("This checkout was already started. Refresh and try again.") from exc
        db.refresh(payment)
        return payment

    payment = await asyncio.to_thread(_persist)
    logger.info(
        "Checkout created: payment=%s user=%s plan=%s gateway=%s amount=%s %s",
        payment.id, user.id, plan.code, gateway.name, price.amount_minor, price.currency,
    )
    return CheckoutResult(
        payment_id=payment.id, gateway=gateway.name, order_id=order.order_id,
        amount_minor=price.amount_minor, currency=price.currency, checkout=order.checkout,
    )


# --------------------------------------------------------- state changes


def _lock_payment(db: Session, payment_id: int) -> Payment:
    payment = db.scalar(select(Payment).where(Payment.id == payment_id).with_for_update())
    if payment is None:  # pragma: no cover - row existed a moment ago
        raise PaymentError("Payment not found.")
    return payment


def mark_paid(
    db: Session,
    payment_id: int,
    *,
    gateway_payment_id: str | None,
    amount_minor: int | None,
    currency: str | None,
    method: str,
    via: str,
) -> VerifyResult:
    """Record a verified payment and activate its subscription atomically."""
    payment = _lock_payment(db, payment_id)
    if payment.status == PaymentStatus.PAID:
        sub = subs.activate_from_payment(db, payment)  # returns the existing period
        db.commit()
        return VerifyResult("paid", payment.id, sub.id, "Payment already confirmed.")
    if payment.status in (PaymentStatus.REFUNDED, PaymentStatus.DISPUTED):
        # A late/replayed "paid" event must never re-grant refunded access.
        db.commit()
        return VerifyResult("failed", payment.id, None, f"Payment is {payment.status}.")
    if (amount_minor is not None and amount_minor != payment.amount_minor) or (
        currency and currency.upper() != payment.currency.upper()
    ):
        # Never activate on a mismatched amount — flag for manual review.
        payment.status = PaymentStatus.FAILED
        payment.failure_reason = (
            f"Amount mismatch: expected {payment.amount_minor} {payment.currency}, "
            f"gateway reported {amount_minor} {currency}."
        )[:300]
        db.commit()
        logger.error("Payment %s amount mismatch (%s) — NOT activated", payment.id, payment.failure_reason)
        return VerifyResult("failed", payment.id, None, "Payment amount did not match. Contact support.")
    payment.status = PaymentStatus.PAID
    payment.gateway_payment_id = gateway_payment_id or payment.gateway_payment_id
    payment.method = (method or "")[:30]
    payment.verified_via = via
    payment.paid_at = subs.utcnow()
    payment.failure_reason = ""
    try:
        sub = subs.activate_from_payment(db, payment)
        db.commit()
    except IntegrityError:
        # Either a concurrent path (webhook vs. client verify) already
        # recorded THIS payment, or the gateway payment id is already attached
        # to a DIFFERENT payment row. Only the first case may succeed.
        db.rollback()
        payment = _lock_payment(db, payment_id)
        if payment.status != PaymentStatus.PAID:
            payment.status = PaymentStatus.FAILED
            payment.failure_reason = "Gateway payment reference already used by another order."
            db.commit()
            logger.error("Payment %s: duplicate gateway payment id — NOT activated", payment_id)
            return VerifyResult("failed", payment_id, None, "Duplicate payment reference. Contact support.")
        sub = subs.activate_from_payment(db, payment)
        db.commit()
    logger.info("Payment %s PAID via %s -> subscription %s", payment.id, via, sub.id)
    return VerifyResult("paid", payment.id, sub.id, "Payment confirmed. Your plan is active.")


def mark_failed(db: Session, payment_id: int, reason: str) -> VerifyResult:
    payment = _lock_payment(db, payment_id)
    if payment.status == PaymentStatus.PAID:
        db.commit()
        return VerifyResult("paid", payment.id, None, "Payment already confirmed.")
    if payment.status in (PaymentStatus.REFUNDED, PaymentStatus.DISPUTED):
        db.commit()
        return VerifyResult(payment.status, payment.id, None, f"Payment is {payment.status}.")
    payment.status = PaymentStatus.FAILED
    payment.failure_reason = (reason or "Payment failed.")[:300]
    db.commit()
    return VerifyResult("failed", payment.id, None, payment.failure_reason)


def mark_refunded(
    db: Session, payment_id: int, *, amount_minor: int | None, currency: str | None,
    reference: str, reason: str = "Refunded at the payment gateway.",
) -> VerifyResult:
    """Refund policy: a FULL refund (or a lost chargeback) ends the access the
    payment bought; a partial refund is recorded and access is kept. Usage
    history is never deleted."""
    payment = _lock_payment(db, payment_id)
    if payment.status not in (PaymentStatus.PAID, PaymentStatus.DISPUTED, PaymentStatus.REFUNDED):
        db.commit()
        return VerifyResult(payment.status, payment.id, None, "Payment was not captured; nothing to refund.")
    if currency and currency.upper() != payment.currency.upper():
        logger.error("Refund currency %s != payment %s currency %s", currency, payment.id, payment.currency)
    partial = amount_minor is not None and 0 < amount_minor < payment.amount_minor
    payment.refund_reference = (reference or payment.refund_reference)[:100]
    if partial:
        payment.failure_reason = f"Partial refund of {amount_minor} {payment.currency} (access kept)."[:300]
        db.commit()
        logger.warning("Payment %s partially refunded (%s)", payment.id, amount_minor)
        return VerifyResult(payment.status, payment.id, None, "Partial refund recorded.")
    payment.status = PaymentStatus.REFUNDED
    payment.refunded_at = payment.refunded_at or subs.utcnow()
    sub = subs.end_for_payment(db, payment, status=SubscriptionStatus.REFUNDED, reason=reason)
    db.commit()
    logger.warning("Payment %s refunded -> subscription %s ended", payment.id, sub.id if sub else None)
    return VerifyResult("refunded", payment.id, sub.id if sub else None, "Refund recorded; access ended.")


def mark_disputed(db: Session, payment_id: int, *, reference: str, reason: str) -> VerifyResult:
    """Chargeback opened: flag the payment for review; access is kept until
    the dispute is lost (then ``mark_refunded``) or an admin revokes it."""
    payment = _lock_payment(db, payment_id)
    if payment.status == PaymentStatus.PAID:
        payment.status = PaymentStatus.DISPUTED
        payment.refund_reference = (reference or "")[:100]
        payment.failure_reason = f"Dispute opened {reason}".strip()[:300]
    db.commit()
    return VerifyResult(payment.status, payment.id, None, "Dispute recorded.")


def _apply(db: Session, payment_id: int, verified: VerifiedPayment, via: str) -> VerifyResult:
    if verified.outcome == "paid":
        return mark_paid(
            db, payment_id,
            gateway_payment_id=verified.gateway_payment_id,
            amount_minor=verified.amount_minor,
            currency=verified.currency,
            method=verified.method,
            via=via,
        )
    if verified.outcome == "failed":
        return mark_failed(db, payment_id, verified.failure_reason)
    return VerifyResult("pending", payment_id, None, "Payment is still processing.")


# ------------------------------------------------------- client verify


def get_owned_payment(db: Session, user_id: int, gateway: str, order_id: str) -> Payment:
    payment = db.scalar(
        select(Payment).where(
            Payment.gateway == gateway,
            Payment.gateway_order_id == order_id,
            Payment.user_id == user_id,  # users can only verify their own orders
        )
    )
    if payment is None:
        raise PaymentError("Payment not found.")
    return payment


async def verify_client_payment(
    db: Session, user: User, *, gateway_name: str, order_id: str, payload: Mapping[str, object]
) -> VerifyResult:
    gateway: PaymentGateway = get_gateway(gateway_name)
    payment = await asyncio.to_thread(get_owned_payment, db, user.id, gateway.name, order_id)
    if payment.status == PaymentStatus.PAID:
        return await asyncio.to_thread(_apply, db, payment.id, VerifiedPayment(
            outcome="paid", gateway_payment_id=payment.gateway_payment_id,
            amount_minor=payment.amount_minor, currency=payment.currency, method=payment.method,
        ), payment.verified_via or "client_verify")
    verified = await gateway.verify_client_payment(order_id=order_id, payload=payload)
    return await asyncio.to_thread(_apply, db, payment.id, verified, "client_verify")


async def reconcile(db: Session, payment_id: int) -> VerifyResult:
    """Ask the gateway for the truth about one payment (admin / support)."""
    payment = await asyncio.to_thread(db.get, Payment, payment_id)
    if payment is None:
        raise PaymentError("Payment not found.")
    gateway = get_gateway(payment.gateway)
    verified = await gateway.fetch_order(payment.gateway_order_id)
    return await asyncio.to_thread(_apply, db, payment.id, verified, "reconcile")


# ------------------------------------------------------------- webhooks


@dataclass(frozen=True)
class WebhookResult:
    status: str  # processed | duplicate | ignored | unknown_order
    event_id: str


def _record_event(db: Session, gateway: str, event: WebhookEvent, raw_body: bytes) -> int | None:
    """Insert the event; None when this (gateway, event_id) was already stored."""
    row = PaymentWebhookEvent(
        gateway=gateway,
        event_id=event.event_id,
        event_type=event.event_type,
        gateway_order_id=event.order_id,
        payload=raw_body.decode("utf-8", errors="replace")[:65_000],
        signature_valid=True,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    return row.id


def _event_row(db: Session, row_id: int) -> PaymentWebhookEvent:
    row = db.get(PaymentWebhookEvent, row_id)
    if row is None:  # pragma: no cover - inserted moments ago in this flow
        raise PaymentError("Webhook event row disappeared.")
    return row


def _process_event(db: Session, gateway: str, event_row_id: int, event: WebhookEvent) -> WebhookResult:
    row = _event_row(db, event_row_id)
    payment = None
    if event.order_id:
        payment = db.scalar(
            select(Payment).where(Payment.gateway == gateway, Payment.gateway_order_id == event.order_id)
        )
    if payment is None:
        row.processing_error = "Unknown order." if event.order_id else "No order reference."
        row.processed_at = subs.utcnow()
        db.commit()
        logger.warning("Webhook %s/%s for unknown order %r", gateway, event.event_id, event.order_id)
        return WebhookResult("unknown_order", event.event_id)
    row.payment_id = payment.id
    db.commit()
    status = "ignored"
    try:
        if event.outcome == "paid":
            mark_paid(
                db, payment.id,
                gateway_payment_id=event.gateway_payment_id,
                amount_minor=event.amount_minor,
                currency=event.currency,
                method=event.method,
                via="webhook",
            )
            status = "processed"
        elif event.outcome == "failed":
            mark_failed(db, payment.id, event.failure_reason)
            status = "processed"
        elif event.outcome == "refunded":
            mark_refunded(db, payment.id, amount_minor=event.amount_minor,
                          currency=event.currency, reference=event.reference)
            status = "processed"
        elif event.outcome == "disputed":
            mark_disputed(db, payment.id, reference=event.reference, reason=event.failure_reason)
            status = "processed"
        elif event.outcome == "dispute_lost":
            mark_refunded(db, payment.id, amount_minor=None, currency=None,
                          reference=event.reference, reason="Chargeback lost at the payment gateway.")
            status = "processed"
    except Exception as exc:  # keep the event row; gateways retry on 5xx
        db.rollback()
        row = _event_row(db, event_row_id)
        row.processing_error = f"{type(exc).__name__}"[:300]
        db.commit()
        raise
    row = _event_row(db, event_row_id)
    row.processed_at = subs.utcnow()
    db.commit()
    return WebhookResult(status, event.event_id)


def handle_webhook(db: Session, gateway_name: str, headers: Mapping[str, str], raw_body: bytes) -> WebhookResult:
    """Verify → deduplicate → apply. Raises WebhookSignatureError on bad signatures.

    Synchronous on purpose (pure CPU + DB); call it from a threadpool.
    """
    gateway = get_gateway(gateway_name)
    event = gateway.parse_webhook(headers, raw_body)  # signature checked FIRST
    row_id = _record_event(db, gateway.name, event, raw_body)
    if row_id is None:
        logger.info("Duplicate webhook %s/%s acknowledged", gateway.name, event.event_id)
        return WebhookResult("duplicate", event.event_id)
    return _process_event(db, gateway.name, row_id, event)


# --------------------------------------------------------- user history


def payment_history(db: Session, user_id: int, limit: int = 100) -> list[Payment]:
    return list(
        db.scalars(
            select(Payment).where(Payment.user_id == user_id)
            .order_by(Payment.created_at.desc(), Payment.id.desc()).limit(limit)
        ).all()
    )


__all__ = [
    "CheckoutResult",
    "DuplicateCheckout",
    "PaymentError",
    "mark_disputed",
    "mark_refunded",
    "VerifyResult",
    "WebhookResult",
    "create_checkout",
    "get_owned_payment",
    "handle_webhook",
    "mark_failed",
    "mark_paid",
    "payment_history",
    "reconcile",
    "verify_client_payment",
]
