"""Subscription & payment API schemas (Pydantic v2).

Amounts are integer minor units (paise / cents) everywhere; the frontend
formats them. Responses expose gateway identifiers but never secrets.
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

CURRENCY_RE = r"^[A-Z]{3}$"
CODE_RE = r"^[a-z0-9][a-z0-9-]{1,48}$"
IDEMPOTENCY_RE = r"^[A-Za-z0-9_.:-]{8,64}$"

BillingTypeLit = Literal["one_time", "recurring"]
DurationUnit = Literal["hours", "days"]


# ------------------------------------------------------------------ plans


class PlanPublic(BaseModel):
    id: int
    code: str
    name: str
    description: str
    currency: str               # currency of price_minor below
    duration_days: int          # legacy whole days (rounded up)
    duration_hours: int         # exact length of one purchase
    duration_unit: str          # "hours" | "days" — how to display it
    screenshot_allowance: int
    billing_type: str           # one_time | recurring
    billing_interval_days: Optional[int] = None
    #: True only when automatic renewal is implemented (it is not yet).
    auto_renew_available: bool = False
    trial_days: int
    #: False when the plan has no live price in the requested currency.
    available: bool = True
    #: True when the price can actually be paid now (currency + gateway).
    payable: bool = True
    price_minor: int            # what checkout charges right now
    regular_price_minor: int    # list price (differs only during a promo)
    is_promo: bool
    promo_label: str = ""
    promo_ends_at: Optional[datetime] = None
    features: list[str] = Field(default_factory=list)
    badge: str = ""
    is_highlighted: bool = False


class PriceOut(BaseModel):
    id: int
    plan_id: int
    currency: str
    amount_minor: int
    is_active: bool
    effective_from: datetime
    effective_to: Optional[datetime] = None
    provider_refs: dict = Field(default_factory=dict)
    is_current: bool = False


class PlanAdmin(PlanPublic):
    is_active: bool
    is_public: bool = True
    sort_order: int
    promo_price_minor: Optional[int] = None
    promo_starts_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    prices: list[PriceOut] = Field(default_factory=list)


class _PlanFields(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    #: Price in the plan's primary currency (INR). Other currencies: /prices.
    price_minor: Optional[int] = Field(default=None, ge=0, le=100_000_000)
    currency: Optional[str] = Field(default=None, pattern=CURRENCY_RE)
    #: Legacy: whole days. Prefer duration_hours.
    duration_days: Optional[int] = Field(default=None, ge=1, le=3660)
    duration_hours: Optional[int] = Field(default=None, ge=1, le=3660 * 24)
    duration_unit: Optional[DurationUnit] = None
    screenshot_allowance: Optional[int] = Field(default=None, ge=0, le=100_000)
    billing_type: Optional[BillingTypeLit] = None
    billing_interval_days: Optional[int] = Field(default=None, ge=1, le=3660)
    trial_days: Optional[int] = Field(default=None, ge=0, le=365)
    promo_price_minor: Optional[int] = Field(default=None, ge=0, le=100_000_000)
    promo_label: Optional[str] = Field(default=None, max_length=60)
    promo_starts_at: Optional[datetime] = None
    promo_ends_at: Optional[datetime] = None
    features: Optional[list[str]] = Field(default=None, max_length=20)
    badge: Optional[str] = Field(default=None, max_length=40)
    is_highlighted: Optional[bool] = None
    is_active: Optional[bool] = None
    is_public: Optional[bool] = None
    sort_order: Optional[int] = Field(default=None, ge=-10_000, le=10_000)
    #: Explicitly remove the promotion (promo fields are otherwise "unchanged").
    clear_promo: bool = False
    #: Optional note stored in the audit log.
    reason: Optional[str] = Field(default=None, max_length=500)

    @field_validator("features")
    @classmethod
    def _clean_features(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return v
        cleaned = [f.strip()[:120] for f in v if f and f.strip()]
        return cleaned

    @model_validator(mode="after")
    def _promo_window(self):
        if self.promo_starts_at and self.promo_ends_at and self.promo_ends_at <= self.promo_starts_at:
            raise ValueError("Promotion end must be after its start.")
        return self


class PlanCreate(_PlanFields):
    code: str = Field(pattern=CODE_RE)
    name: str = Field(min_length=1, max_length=120)
    price_minor: int = Field(ge=0, le=100_000_000)
    currency: str = Field(default="INR", pattern=CURRENCY_RE)

    @model_validator(mode="after")
    def _has_duration(self):
        if not self.duration_hours and not self.duration_days:
            raise ValueError("Provide duration_hours (or duration_days).")
        return self


class PlanUpdate(_PlanFields):
    pass


class PriceSet(BaseModel):
    currency: str = Field(pattern=CURRENCY_RE)
    amount_minor: int = Field(ge=0, le=100_000_000)
    is_active: bool = True
    #: Future start schedules the price; omitted = now.
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    provider_refs: Optional[dict[str, str]] = None
    reason: str = Field(default="", max_length=500)

    @field_validator("provider_refs")
    @classmethod
    def _small_refs(cls, v: Optional[dict[str, str]]) -> Optional[dict[str, str]]:
        if v is not None and (len(v) > 10 or any(len(k) > 50 or len(x) > 120 for k, x in v.items())):
            raise ValueError("Too many / too long provider references.")
        return v


class CurrencyOut(BaseModel):
    code: str
    name: str
    minor_digits: int
    display_enabled: bool
    payments_enabled: bool
    #: payments_enabled AND at least one configured gateway accepts it.
    payable_now: bool = False
    gateways: list[str] = Field(default_factory=list)
    sort_order: int = 0
    live_prices: int = 0


class CurrencyUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=60)
    display_enabled: Optional[bool] = None
    payments_enabled: Optional[bool] = None
    sort_order: Optional[int] = Field(default=None, ge=-10_000, le=10_000)
    reason: str = Field(default="", max_length=500)


# ---------------------------------------------------------- subscriptions


class SubscriptionOut(BaseModel):
    id: int
    user_id: int
    user_email: str
    plan_id: Optional[int]
    plan_code: str
    plan_name: str
    status: str               # derived: active | scheduled | expired | revoked | cancelled | refunded
    source: str               # payment | manual | trial
    starts_at: datetime
    expires_at: datetime
    amount_minor: int
    currency: str
    is_renewal: bool
    payment_id: Optional[int]
    screenshot_allowance: int = 0
    screenshots_used: int = 0
    screenshots_remaining: int = 0
    billing_type: str = "one_time"
    auto_renew: bool = False
    notes: str = ""
    revoked_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class PaymentOut(BaseModel):
    id: int
    user_id: int
    user_email: str
    plan_id: int
    plan_code: str
    plan_name: str
    duration_days: int
    duration_hours: int = 0
    screenshot_allowance: int = 0
    amount_minor: int
    currency: str
    gateway: str
    receipt: str
    gateway_order_id: str
    gateway_payment_id: Optional[str]
    status: str
    method: str
    failure_reason: str
    verified_via: str
    is_renewal: bool
    created_at: Optional[datetime]
    paid_at: Optional[datetime]
    refunded_at: Optional[datetime] = None
    refund_reference: str = ""
    disputed_at: Optional[datetime] = None
    dispute_status: str = ""
    dispute_respond_by: Optional[datetime] = None
    subscription_id: Optional[int] = None


class UsageOut(BaseModel):
    """The applicable subscription's screenshot credits."""

    subscription_id: Optional[int]
    plan_name: str
    status: str
    allowance: int
    used: int
    remaining: int
    expires_at: Optional[datetime]
    total_remaining: int


class MyBilling(BaseModel):
    current: Optional[SubscriptionOut]
    access_ends_at: Optional[datetime]
    subscriptions: list[SubscriptionOut]
    payments: list[PaymentOut]
    trial_used: bool
    usage: Optional[UsageOut] = None


class GatewayOut(BaseModel):
    key: str
    display_name: str
    currencies: list[str] = Field(default_factory=list)


class PaymentStatusOut(BaseModel):
    """Public: can a new checkout start right now, and through which gateway."""

    payments_enabled: bool
    active_gateway: str
    active_gateway_name: str = ""
    gateway_names: list[str] = Field(default_factory=list)
    available: bool
    message: str = ""


class AdminGatewayOption(BaseModel):
    key: str
    display_name: str
    enabled: bool
    is_default: bool
    configured: bool
    currencies: list[str] = Field(default_factory=list)


class PaymentSettingsOut(BaseModel):
    payments_enabled: bool
    active_gateway: str
    checkout_available: bool
    updated_at: Optional[datetime] = None
    updated_by_user_id: Optional[int] = None
    gateways: list[AdminGatewayOption]


class PaymentSettingsUpdate(BaseModel):
    payments_enabled: Optional[bool] = None
    #: The default gateway (pre-selected at checkout); also switches it on.
    active_gateway: Optional[Literal["cashfree", "razorpay"]] = None
    #: Turn individual gateways on/off, e.g. {"razorpay": true}.
    gateways_enabled: Optional[dict[Literal["cashfree", "razorpay"], bool]] = None
    reason: str = Field(default="", max_length=500)


# --------------------------------------------------------------- checkout


class CheckoutRequest(BaseModel):
    plan_id: int = Field(ge=1)
    gateway: Literal["razorpay", "cashfree", "mock"]
    currency: str = Field(default="INR", pattern=CURRENCY_RE)
    #: Client-generated per checkout attempt; a retry with the same key never
    #: opens a second order.
    idempotency_key: Optional[str] = Field(default=None, pattern=IDEMPOTENCY_RE)
    # Required by Cashfree only; passed to the gateway, never stored.
    phone: str = Field(default="", max_length=20)

    @field_validator("phone")
    @classmethod
    def _digits(cls, v: str) -> str:
        v = v.strip()
        if v and not all(ch.isdigit() or ch in "+- " for ch in v):
            raise ValueError("Phone may contain digits, spaces, + and - only.")
        return v


class CheckoutResponse(BaseModel):
    payment_id: int
    gateway: str
    order_id: str
    amount_minor: int
    currency: str
    checkout: dict


class VerifyRequest(BaseModel):
    order_id: str = Field(min_length=1, max_length=100)
    # Gateway-specific confirmation fields (e.g. razorpay_payment_id,
    # razorpay_signature). Treated as untrusted input.
    payload: dict[str, str] = Field(default_factory=dict)

    @field_validator("payload")
    @classmethod
    def _bounded(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 10 or any(len(k) > 50 or len(str(x)) > 300 for k, x in v.items()):
            raise ValueError("Payment confirmation payload is too large.")
        return v


class VerifyResponse(BaseModel):
    status: str
    payment_id: int
    subscription_id: Optional[int] = None
    message: str = ""


class TrialRequest(BaseModel):
    plan_id: int = Field(ge=1)


class MockSimulateRequest(BaseModel):
    order_id: str = Field(min_length=1, max_length=100)
    succeed: bool = True


# ------------------------------------------------------------ admin ops


class GrantRequest(BaseModel):
    plan_id: Optional[int] = Field(default=None, ge=1)
    days: Optional[int] = Field(default=None, ge=1, le=3660)
    hours: Optional[int] = Field(default=None, ge=1, le=3660 * 24)
    #: Defaults to the plan's allowance (0 without a plan).
    screenshot_credits: Optional[int] = Field(default=None, ge=0, le=100_000)
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def _one_of(self):
        if not self.plan_id and not self.days and not self.hours:
            raise ValueError("Provide a plan, a number of days or a number of hours.")
        return self


class ExtendRequest(BaseModel):
    days: int = Field(default=0, ge=0, le=3660)
    hours: int = Field(default=0, ge=0, le=3660 * 24)
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def _some(self):
        if not self.days and not self.hours:
            raise ValueError("Provide days or hours.")
        return self


class RevokeRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class RefundRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class CreditsRequest(BaseModel):
    allowance: int = Field(ge=0, le=100_000)
    reason: str = Field(min_length=3, max_length=500)


class AdminSubscriptionRow(SubscriptionOut):
    gateway: Optional[str] = None
    payment_status: Optional[str] = None
    gateway_payment_id: Optional[str] = None


class Page(BaseModel):
    total: int
    page: int
    page_size: int


class AdminSubscriptionsPage(Page):
    items: list[AdminSubscriptionRow]


class AdminPaymentsPage(Page):
    items: list[PaymentOut]


class UsageRow(BaseModel):
    id: int
    user_id: int
    user_email: str
    subscription_id: Optional[int]
    plan_name: str = ""
    request_id: str
    status: str
    credits: int
    credits_consumed: int
    attempts: int
    failure_code: str
    created_at: Optional[datetime]
    completed_at: Optional[datetime]


class AdminUsagePage(Page):
    items: list[UsageRow]


class AuditOut(BaseModel):
    id: int
    user_id: Optional[int]
    user_email: str = ""
    plan_id: Optional[int]
    subscription_id: Optional[int]
    payment_id: Optional[int]
    actor_user_id: Optional[int]
    action: str
    reason: str
    details: dict
    created_at: Optional[datetime]


class AdminUserBilling(BaseModel):
    user_id: int
    email: str
    name: str
    current: Optional[SubscriptionOut]
    access_ends_at: Optional[datetime]
    subscriptions: list[SubscriptionOut]
    payments: list[PaymentOut]
    audit: list[AuditOut]
    usage: Optional[UsageOut] = None


class CurrencyAmount(BaseModel):
    currency: str
    amount_minor: int
    count: int


class BillingOverview(BaseModel):
    """Admin dashboard billing metrics. Revenue is PER CURRENCY — never summed
    across currencies (no exchange-rate methodology is configured)."""

    date_from: Optional[datetime]
    date_to: Optional[datetime]
    total_users: int
    active_subscriptions: int
    expired_subscriptions: int
    ended_subscriptions: int          # revoked / cancelled / refunded
    payments_by_status: dict[str, int]
    revenue_by_currency: list[CurrencyAmount]
    refunds_by_currency: list[CurrencyAmount]
    subscriptions_by_plan: dict[str, int]
    screenshot_requests: int
    screenshots_analyzed: int
    screenshot_failures: int
    payment_success_rate: Optional[float]
    #: Chargebacks: payments created in the range that were EVER disputed
    #: (won, lost or open), as a share of captured payments. Cashfree may end
    #: the merchant account above 1% (by value).
    disputes_count: int = 0
    dispute_rate: Optional[float] = None
    #: Same ratio by INR value (Cashfree's measure), None without INR sales.
    dispute_value_rate_inr: Optional[float] = None
    #: Disputes still open right now (all time) and the nearest evidence deadline.
    open_disputes: int = 0
    next_dispute_deadline: Optional[datetime] = None
