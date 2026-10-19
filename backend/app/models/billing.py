"""Subscription & payment ORM models.

Tables
------
``subscription_plans``       admin-managed catalogue (prices are the CURRENT offer)
``payments``                 one row per checkout attempt; amounts are SNAPSHOTS
``user_subscriptions``       access periods per user (paid, manual grant, or trial)
``payment_webhook_events``   every gateway webhook, deduplicated per gateway
``subscription_audit_logs``  who changed which subscription, when, and why

Design rules
------------
* Money is stored in integer **minor units** (paise for INR) plus an ISO-4217
  currency code — never floats.
* A payment row copies the plan's code/name/duration/amount at checkout time,
  so editing a plan's price never rewrites history.
* Plans are never hard-deleted (``is_active`` toggles visibility); FK
  ``ON DELETE RESTRICT`` keeps historical rows valid.
* Deleting a user cascades to their billing rows (account erasure), matching
  the existing resume/role tables.
* No card number, CVV, UPI PIN or bank credential is ever stored — only
  gateway identifiers (order id, payment id) returned by the gateway.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- statuses
class PaymentStatus:
    CREATED = "created"      # order created at the gateway, not paid yet
    PAID = "paid"            # verified server-side (signature + gateway status)
    FAILED = "failed"        # gateway reported failure / verification failed
    CANCELLED = "cancelled"  # superseded or abandoned before payment


class SubscriptionStatus:
    ACTIVE = "active"
    REVOKED = "revoked"
    # "expired" / "scheduled" are DERIVED from dates (see services), never
    # stored, so no background job is needed to keep them correct.


class SubscriptionSource:
    PAYMENT = "payment"      # created by a verified payment
    MANUAL = "manual"        # granted by an admin (never linked to a payment)
    TRIAL = "trial"          # free trial period of a plan


GATEWAYS = ("razorpay", "cashfree", "mock")


class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"
    __table_args__ = (
        CheckConstraint("price_minor >= 0", name="ck_subscription_plans_price_nonneg"),
        CheckConstraint("duration_days > 0", name="ck_subscription_plans_duration_pos"),
        CheckConstraint("trial_days >= 0", name="ck_subscription_plans_trial_nonneg"),
        CheckConstraint(
            "promo_price_minor IS NULL OR promo_price_minor >= 0",
            name="ck_subscription_plans_promo_nonneg",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Stable slug used in URLs/analytics, e.g. "month". Unique, never reused.
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    trial_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Optional promotional price, applied only inside [promo_starts_at, promo_ends_at).
    promo_price_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    promo_label: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    promo_starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    promo_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # JSON array of short feature strings shown on the pricing page.
    features_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    badge: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    is_highlighted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("gateway", "gateway_order_id", name="uq_payments_gateway_order"),
        UniqueConstraint("gateway", "gateway_payment_id", name="uq_payments_gateway_payment"),
        CheckConstraint("amount_minor >= 0", name="ck_payments_amount_nonneg"),
        Index("ix_payments_user_created", "user_id", "created_at"),
        Index("ix_payments_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalised for admin search/export (same pattern as resumes.user_email).
    user_email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("subscription_plans.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    # Snapshot of what was bought — immune to later plan edits.
    plan_code: Mapped[str] = mapped_column(String(50), nullable=False)
    plan_name: Mapped[str] = mapped_column(String(120), nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    gateway: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    # Our own reference sent to the gateway (receipt / order_id). Unique.
    receipt: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    gateway_order_id: Mapped[str] = mapped_column(String(100), nullable=False)
    gateway_payment_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=PaymentStatus.CREATED, nullable=False)
    # Gateway-provided method label ("upi", "card") — never card data.
    method: Mapped[str] = mapped_column(String(30), default="", nullable=False)
    failure_reason: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    # "webhook" | "client_verify" | "reconcile" — how payment was confirmed.
    verified_via: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    is_renewal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class UserSubscription(Base):
    __tablename__ = "user_subscriptions"
    __table_args__ = (
        CheckConstraint("expires_at > starts_at", name="ck_user_subscriptions_period"),
        # One subscription per payment: a replayed verification can never
        # grant the same purchase twice.
        UniqueConstraint("payment_id", name="uq_user_subscriptions_payment"),
        Index("ix_user_subscriptions_user_expires", "user_id", "expires_at"),
        Index("ix_user_subscriptions_status_expires", "status", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    user_email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("subscription_plans.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    plan_code: Mapped[str] = mapped_column(String(50), nullable=False)
    plan_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=SubscriptionStatus.ACTIVE, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Set only for source="payment". Manual grants are never tied to money.
    payment_id: Mapped[int | None] = mapped_column(
        ForeignKey("payments.id", ondelete="SET NULL"), nullable=True
    )
    amount_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    is_renewal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    granted_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class PaymentWebhookEvent(Base):
    __tablename__ = "payment_webhook_events"
    __table_args__ = (
        # Idempotency: a gateway retrying the same event is stored once.
        UniqueConstraint("gateway", "event_id", name="uq_payment_webhook_events_gateway_event"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gateway: Mapped[str] = mapped_column(String(20), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    gateway_order_id: Mapped[str] = mapped_column(String(100), default="", index=True, nullable=False)
    payment_id: Mapped[int | None] = mapped_column(
        ForeignKey("payments.id", ondelete="SET NULL"), index=True, nullable=True
    )
    # Raw JSON body for audit/replay. Gateways never send card numbers/CVV.
    payload: Mapped[str] = mapped_column(Text, default="", nullable=False)
    signature_valid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_error: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True, nullable=False
    )


class SubscriptionAuditLog(Base):
    __tablename__ = "subscription_audit_logs"
    __table_args__ = (Index("ix_subscription_audit_logs_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # The subscriber affected; NULL for plan-catalogue changes.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("subscription_plans.id", ondelete="SET NULL"), nullable=True
    )
    subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_subscriptions.id", ondelete="SET NULL"), index=True, nullable=True
    )
    payment_id: Mapped[int | None] = mapped_column(
        ForeignKey("payments.id", ondelete="SET NULL"), nullable=True
    )
    # NULL = system (verified payment / webhook); set = the admin who acted.
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # purchase | grant | extend | revoke | plan_create | plan_update
    action: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    # JSON with before/after values (dates, plan, amount) — never secrets.
    details_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True, nullable=False
    )


__all__ = [
    "GATEWAYS",
    "Payment",
    "PaymentStatus",
    "PaymentWebhookEvent",
    "SubscriptionAuditLog",
    "SubscriptionPlan",
    "SubscriptionSource",
    "SubscriptionStatus",
    "UserSubscription",
]
