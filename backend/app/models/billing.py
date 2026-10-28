"""Subscription & payment ORM models.

Tables
------
``subscription_plans``       admin-managed catalogue (prices are the CURRENT offer)
``plan_prices``              per-currency price of each plan (INR, USD, ...)
``billing_currencies``       which currencies are shown / payable
``payment_settings``         site-wide on/off switch + the active checkout gateway
``payments``                 one row per checkout attempt; amounts are SNAPSHOTS
``user_subscriptions``       access periods per user (paid, manual grant, or trial)
``screenshot_usage``         one row per screenshot-analysis request (credits)
``payment_webhook_events``   every gateway webhook, deduplicated per gateway
``subscription_audit_logs``  who changed which subscription, when, and why
``schema_migrations``        one-time data migrations already applied

Design rules
------------
* Money is stored in integer **minor units** (paise for INR, cents for USD)
  plus an ISO-4217 currency code — never floats.
* A payment row copies the plan's code/name/duration/allowance/amount at
  checkout time, so editing a plan never rewrites history.
* Plans are never hard-deleted (``is_active`` toggles visibility); FK
  ``ON DELETE RESTRICT`` keeps historical rows valid.
* Deleting a user cascades to their billing rows (account erasure), matching
  the existing resume/role tables.
* No card number, CVV, UPI PIN or bank credential is ever stored — only
  gateway identifiers (order id, payment id) returned by the gateway.
* Every table with ``user_id`` also has ``user_email``: a SNAPSHOT of the
  owner's email taken when the row is created (``app.db.email_snapshot``).
  ``user_id`` stays the relational key; the email is never rewritten when a
  user changes their address, so historical records stay traceable.
* Durations are exact: ``duration_hours`` is added to the activation instant
  (UTC), never rounded to calendar days or months.
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
    CREATED = "created"      # order created at the gateway, not paid yet (pending)
    PAID = "paid"            # verified server-side (signature + gateway status)
    FAILED = "failed"        # gateway reported failure / verification failed
    CANCELLED = "cancelled"  # superseded or abandoned before payment
    REFUNDED = "refunded"    # gateway reported a refund (access revoked)
    DISPUTED = "disputed"    # chargeback opened at the gateway (flagged for review)


class SubscriptionStatus:
    ACTIVE = "active"
    REVOKED = "revoked"      # admin removed access
    CANCELLED = "cancelled"  # admin cancelled (e.g. on the customer's request)
    REFUNDED = "refunded"    # the payment was refunded / chargeback lost
    # "expired" / "scheduled" are DERIVED from dates (see services), never
    # stored, so no background job is needed to keep them correct. Pending /
    # failed payments never create a subscription row at all.

    #: Stored states that end access for good (no transition out of them).
    TERMINAL = ("revoked", "cancelled", "refunded")


class SubscriptionSource:
    PAYMENT = "payment"      # created by a verified payment
    MANUAL = "manual"        # granted by an admin (never linked to a payment)
    TRIAL = "trial"          # free trial period of a plan


class BillingType:
    ONE_TIME = "one_time"    # a prepaid pass
    RECURRING = "recurring"  # intended to renew; sold prepaid until auto-renew exists


class UsageStatus:
    RESERVED = "reserved"    # credits held while the analysis runs
    COMPLETED = "completed"  # analysis succeeded, credits consumed
    FAILED = "failed"        # analysis failed after it started, credits released


GATEWAYS = ("razorpay", "cashfree", "mock")
#: Gateways an admin can make the site's active checkout gateway (mock is dev-only).
SELECTABLE_GATEWAYS = ("cashfree", "razorpay")


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
    # Mirror of the plan's price in its primary ``currency`` (INR). The
    # authority for every currency is ``plan_prices``; this column is kept in
    # sync for older readers and is the base for the INR promotion.
    price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    # Legacy whole-day length, kept in sync with duration_hours (rounded up)
    # for older readers. duration_hours is the authority for every period.
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_hours: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # How the duration is shown/edited: "hours" (24 hours) or "days" (7 days).
    duration_unit: Mapped[str] = mapped_column(String(10), default="days", nullable=False)
    # Screenshot analyses included in one purchase of this plan.
    screenshot_allowance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    billing_type: Mapped[str] = mapped_column(String(20), default=BillingType.ONE_TIME, nullable=False)
    # Intended renewal interval for recurring plans (exact days, not months).
    billing_interval_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Hidden plans are not listed on the public pricing page.
    is_public: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
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


class BillingCurrency(Base):
    """A currency the catalogue can be priced in.

    ``display_enabled`` shows prices on the pricing page; ``payments_enabled``
    allows checkout. A currency can be shown before payments in it are
    switched on (the page then says so instead of offering checkout).
    """

    __tablename__ = "billing_currencies"

    code: Mapped[str] = mapped_column(String(3), primary_key=True)
    name: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    # Number of minor-unit digits (2 for INR paise and USD cents).
    minor_digits: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    display_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    payments_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class PlanPrice(Base):
    """Price of a plan in one currency for a period of time.

    Prices are versioned rather than edited in place: changing an amount ends
    the current row (``effective_to``) and adds a new one, so the history of
    what was on offer is kept. Payments still snapshot their own amount.
    """

    __tablename__ = "plan_prices"
    __table_args__ = (
        CheckConstraint("amount_minor >= 0", name="ck_plan_prices_amount_nonneg"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from", name="ck_plan_prices_window"
        ),
        Index("ix_plan_prices_plan_currency", "plan_id", "currency", "effective_from"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("subscription_plans.id", ondelete="RESTRICT"), nullable=False
    )
    currency: Mapped[str] = mapped_column(
        ForeignKey("billing_currencies.code", ondelete="RESTRICT"), nullable=False
    )
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Optional gateway-side ids, e.g. {"razorpay_plan_id": "plan_..."} — never secrets.
    provider_refs_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("gateway", "gateway_order_id", name="uq_payments_gateway_order"),
        UniqueConstraint("gateway", "gateway_payment_id", name="uq_payments_gateway_payment"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_payments_user_idempotency"),
        CheckConstraint("amount_minor >= 0", name="ck_payments_amount_nonneg"),
        Index("ix_payments_user_created", "user_id", "created_at"),
        Index("ix_payments_status_created", "status", "created_at"),
        Index("ix_payments_currency_status", "currency", "status"),
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
    duration_hours: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    screenshot_allowance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    gateway: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    # Client-supplied key: a retried checkout returns the same open order.
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Gateway refund / dispute id, when the gateway reported one.
    refund_reference: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    # First time a chargeback/dispute was opened. Kept after the dispute is won
    # or lost so the chargeback ratio counts every disputed payment.
    disputed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Latest gateway dispute status (e.g. CHARGEBACK_CREATED, DISPUTE_MERCHANT_WON).
    dispute_status: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    # Deadline to upload evidence at the gateway, when it sent one.
    dispute_respond_by: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class UserSubscription(Base):
    __tablename__ = "user_subscriptions"
    __table_args__ = (
        CheckConstraint("expires_at > starts_at", name="ck_user_subscriptions_period"),
        CheckConstraint(
            "screenshots_used >= 0 AND screenshots_used <= screenshot_allowance",
            name="ck_user_subscriptions_credits",
        ),
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
    # Snapshot of the plan's allowance at activation; the counter below is
    # only ever changed by atomic UPDATEs (screenshot_credit_service).
    screenshot_allowance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    screenshots_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    billing_type: Mapped[str] = mapped_column(String(20), default=BillingType.ONE_TIME, nullable=False)
    # Always False until a recurring-billing workflow exists (see PAYMENTS.md).
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    granted_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class ScreenshotUsage(Base):
    """One screenshot-analysis request and the credits it used.

    ``(user_id, request_id)`` is unique, so a retried request can never be
    charged twice. Rows are never deleted or rewritten by admins; manual
    credit corrections change the subscription's allowance (audited).
    """

    __tablename__ = "screenshot_usage"
    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="uq_screenshot_usage_user_request"),
        CheckConstraint("credits >= 0", name="ck_screenshot_usage_credits_nonneg"),
        Index("ix_screenshot_usage_user_created", "user_id", "created_at"),
        Index("ix_screenshot_usage_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    user_email: Mapped[str] = mapped_column(String(320), index=True, default="", nullable=False)
    subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_subscriptions.id", ondelete="SET NULL"), index=True, nullable=True
    )
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=UsageStatus.RESERVED, nullable=False)
    # Screenshots in the request (= credits held while it runs).
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    # Credits actually consumed (= credits when completed, 0 otherwise).
    credits_consumed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Times this request id was submitted — >1 means client retries.
    attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    failure_code: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    # Snapshot of the affected user's email ('' for catalogue changes).
    user_email: Mapped[str] = mapped_column(String(320), index=True, default="", nullable=False)
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
    # purchase | grant | extend | revoke | cancel | refund | credits_adjust |
    # plan_create | plan_update | price_set | currency_update | catalogue_migration
    action: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    # JSON with before/after values (dates, plan, amount) — never secrets.
    details_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True, nullable=False
    )


class PaymentSettings(Base):
    """Site-wide payment switches (a single row, ``id = 1``, created lazily).

    ``payments_enabled`` turns online checkout on/off for the whole site;
    ``cashfree_enabled`` / ``razorpay_enabled`` choose which gateways new
    checkouts may use (both at once is fine — the customer picks one) and
    ``active_gateway`` is the DEFAULT, pre-selected at checkout. Gateway keys
    and secrets stay in env vars — never here. Orders already created keep
    verifying / receiving webhooks through their own gateway either way.
    """

    __tablename__ = "payment_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payments_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    active_gateway: Mapped[str] = mapped_column(String(20), default="cashfree", nullable=False)
    cashfree_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    razorpay_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class SchemaMigration(Base):
    """Marker for one-time data migrations (so they never run twice)."""

    __tablename__ = "schema_migrations"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class FreeTrialUsage(Base):
    """Free trial allowance used by one user (one row per user, created lazily).

    Every account gets the same one-time free allowance (limits come from
    settings, so they can change without a migration): live-interview seconds
    on the website, separate live-interview seconds in the desktop app, and
    screenshot-analysis credits. Counters only ever grow; CHECK constraints
    keep them non-negative. Used only while the user has no live plan.
    """

    __tablename__ = "free_trial_usage"
    __table_args__ = (
        CheckConstraint("web_seconds_used >= 0", name="ck_free_trial_web_nonneg"),
        CheckConstraint("desktop_seconds_used >= 0", name="ck_free_trial_desktop_nonneg"),
        CheckConstraint("screenshots_used >= 0", name="ck_free_trial_screens_nonneg"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    user_email: Mapped[str] = mapped_column(String(320), index=True, default="", nullable=False)
    web_seconds_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    desktop_seconds_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    screenshots_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


__all__ = [
    "BillingCurrency",
    "BillingType",
    "GATEWAYS",
    "Payment",
    "PaymentSettings",
    "PaymentStatus",
    "PaymentWebhookEvent",
    "PlanPrice",
    "SELECTABLE_GATEWAYS",
    "SchemaMigration",
    "ScreenshotUsage",
    "SubscriptionAuditLog",
    "SubscriptionPlan",
    "SubscriptionSource",
    "SubscriptionStatus",
    "UsageStatus",
    "UserSubscription",
    "FreeTrialUsage",
]
