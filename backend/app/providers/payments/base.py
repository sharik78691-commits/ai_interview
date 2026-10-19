"""Payment-gateway abstraction.

Each gateway (Razorpay, Cashfree, mock) is an independent module behind this
interface; business logic (``app.services.payment_service``) never imports a
concrete gateway. Gateways are pure adapters: they talk HTTP to their
provider, verify signatures and translate responses — they never touch the
database or grant access.

Security contract every implementation must keep:
* ``verify_client_payment`` must NOT trust the browser: a valid checkout
  signature alone is not enough — the gateway's own API is queried for the
  final status and amount.
* ``parse_webhook`` must verify the signature over the RAW request body
  before reading anything from it.
* Secrets are read from settings, never logged, never returned.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal, Mapping


class PaymentGatewayError(Exception):
    """Gateway unreachable or returned an error (safe message for users)."""


class GatewayNotConfigured(PaymentGatewayError):
    """Credentials for this gateway are not set."""


class WebhookSignatureError(Exception):
    """Webhook signature missing/invalid (or replay window exceeded)."""


@dataclass(frozen=True)
class CustomerInfo:
    user_id: int
    email: str
    name: str = ""
    phone: str = ""


@dataclass(frozen=True)
class GatewayOrder:
    """Result of creating an order at the gateway."""

    order_id: str
    #: Non-secret data the browser needs to open the gateway checkout
    #: (public key id / payment session id / amount). Never secrets.
    checkout: dict = field(default_factory=dict)


PaymentOutcome = Literal["paid", "failed", "pending"]


@dataclass(frozen=True)
class VerifiedPayment:
    """Server-side truth about one order, as reported by the gateway API."""

    outcome: PaymentOutcome
    gateway_payment_id: str | None = None
    amount_minor: int | None = None
    currency: str | None = None
    method: str = ""
    failure_reason: str = ""


WebhookOutcome = Literal["paid", "failed", "ignored"]


@dataclass(frozen=True)
class WebhookEvent:
    """A signature-verified webhook, normalised across gateways."""

    event_id: str
    event_type: str
    outcome: WebhookOutcome
    order_id: str = ""
    gateway_payment_id: str | None = None
    amount_minor: int | None = None
    currency: str | None = None
    method: str = ""
    failure_reason: str = ""


class PaymentGateway(ABC):
    #: Registry key and value stored in ``payments.gateway``.
    name: str = "base"
    display_name: str = "Base"

    @property
    @abstractmethod
    def configured(self) -> bool:
        """True when credentials are present and the gateway can be offered."""

    @abstractmethod
    async def create_order(
        self,
        *,
        receipt: str,
        amount_minor: int,
        currency: str,
        customer: CustomerInfo,
        description: str,
    ) -> GatewayOrder:
        """Create an order for exactly ``amount_minor`` (server-computed)."""

    @abstractmethod
    async def verify_client_payment(self, *, order_id: str, payload: Mapping[str, object]) -> VerifiedPayment:
        """Verify what the browser reported AND confirm it with the gateway API."""

    @abstractmethod
    async def fetch_order(self, order_id: str) -> VerifiedPayment:
        """Query the gateway for the current state of an order (reconciliation)."""

    @abstractmethod
    def parse_webhook(self, headers: Mapping[str, str], raw_body: bytes) -> WebhookEvent:
        """Verify the signature over ``raw_body`` and normalise the event.

        Raises :class:`WebhookSignatureError` when verification fails.
        """


def to_minor(amount: object) -> int:
    """Convert a major-unit amount (e.g. 150.00 rupees) to minor units exactly."""
    from decimal import ROUND_HALF_UP, Decimal

    return int((Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def to_major_str(amount_minor: int) -> str:
    """Minor units -> '150.00' (exact, no float rounding)."""
    sign = "-" if amount_minor < 0 else ""
    v = abs(amount_minor)
    return f"{sign}{v // 100}.{v % 100:02d}"


def header(headers: Mapping[str, str], name: str) -> str:
    """Case-insensitive header lookup."""
    lowered = name.lower()
    for k, v in headers.items():
        if k.lower() == lowered:
            return v
    return ""
