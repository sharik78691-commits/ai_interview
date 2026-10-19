"""Subscription & payment API schemas (Pydantic v2).

Amounts are integer minor units (paise) everywhere; the frontend formats
them. Responses expose gateway identifiers but never secrets.
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

CURRENCY_RE = r"^[A-Z]{3}$"
CODE_RE = r"^[a-z0-9][a-z0-9-]{1,48}$"


# ------------------------------------------------------------------ plans


class PlanPublic(BaseModel):
    id: int
    code: str
    name: str
    description: str
    currency: str
    duration_days: int
    trial_days: int
    price_minor: int            # what checkout charges right now
    regular_price_minor: int    # list price (differs only during a promo)
    is_promo: bool
    promo_label: str = ""
    promo_ends_at: Optional[datetime] = None
    features: list[str] = Field(default_factory=list)
    badge: str = ""
    is_highlighted: bool = False


class PlanAdmin(PlanPublic):
    is_active: bool
    sort_order: int
    promo_price_minor: Optional[int] = None
    promo_starts_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class _PlanFields(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    price_minor: Optional[int] = Field(default=None, ge=0, le=100_000_000)
    currency: Optional[str] = Field(default=None, pattern=CURRENCY_RE)
    duration_days: Optional[int] = Field(default=None, ge=1, le=3660)
    trial_days: Optional[int] = Field(default=None, ge=0, le=365)
    promo_price_minor: Optional[int] = Field(default=None, ge=0, le=100_000_000)
    promo_label: Optional[str] = Field(default=None, max_length=60)
    promo_starts_at: Optional[datetime] = None
    promo_ends_at: Optional[datetime] = None
    features: Optional[list[str]] = Field(default=None, max_length=20)
    badge: Optional[str] = Field(default=None, max_length=40)
    is_highlighted: Optional[bool] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = Field(default=None, ge=-10_000, le=10_000)
    #: Explicitly remove the promotion (promo fields are otherwise "unchanged").
    clear_promo: bool = False

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
    duration_days: int = Field(ge=1, le=3660)


class PlanUpdate(_PlanFields):
    pass


# ---------------------------------------------------------- subscriptions


class SubscriptionOut(BaseModel):
    id: int
    user_id: int
    user_email: str
    plan_id: Optional[int]
    plan_code: str
    plan_name: str
    status: str               # derived: active | scheduled | expired | revoked
    source: str               # payment | manual | trial
    starts_at: datetime
    expires_at: datetime
    amount_minor: int
    currency: str
    is_renewal: bool
    payment_id: Optional[int]
    notes: str = ""
    revoked_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class PaymentOut(BaseModel):
    id: int
    user_id: int
    user_email: str
    plan_id: int
    plan_code: str
    plan_name: str
    duration_days: int
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


class MyBilling(BaseModel):
    current: Optional[SubscriptionOut]
    access_ends_at: Optional[datetime]
    subscriptions: list[SubscriptionOut]
    payments: list[PaymentOut]
    trial_used: bool


class GatewayOut(BaseModel):
    key: str
    display_name: str


# --------------------------------------------------------------- checkout


class CheckoutRequest(BaseModel):
    plan_id: int = Field(ge=1)
    gateway: Literal["razorpay", "cashfree", "mock"]
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
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def _one_of(self):
        if not self.plan_id and not self.days:
            raise ValueError("Provide a plan or a number of days.")
        return self


class ExtendRequest(BaseModel):
    days: int = Field(ge=1, le=3660)
    reason: str = Field(min_length=3, max_length=500)


class RevokeRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class AdminSubscriptionRow(SubscriptionOut):
    gateway: Optional[str] = None


class Page(BaseModel):
    total: int
    page: int
    page_size: int


class AdminSubscriptionsPage(Page):
    items: list[AdminSubscriptionRow]


class AdminPaymentsPage(Page):
    items: list[PaymentOut]


class AuditOut(BaseModel):
    id: int
    user_id: Optional[int]
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
