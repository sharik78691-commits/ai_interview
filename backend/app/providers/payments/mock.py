"""Development-only simulated gateway.

Lets the full checkout → verify → subscription flow run before real
credentials exist. It behaves like a real gateway (orders, HMAC-signed
confirmations, signed webhooks), so the SAME verification code paths are
exercised. The signing secret is random per process and never leaves the
backend; the browser obtains a signed confirmation only through the
dev-only ``/api/payments/mock/simulate`` endpoint, which is disabled
whenever mock payments are disabled — always in production.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import Mapping

from app.core.config import get_settings
from app.providers.payments.base import (
    CustomerInfo,
    GatewayOrder,
    PaymentGateway,
    PaymentGatewayError,
    VerifiedPayment,
    WebhookEvent,
    WebhookSignatureError,
    header,
)

_SECRET = secrets.token_hex(32)


class MockGateway(PaymentGateway):
    name = "mock"
    display_name = "Test payment (development only)"

    def __init__(self) -> None:
        # order_id -> {"amount": int, "currency": str, "status": str, "payment_id": str}
        self._orders: dict[str, dict] = {}

    @property
    def configured(self) -> bool:
        return get_settings().payments_mock_enabled

    def _require_enabled(self) -> None:
        if not self.configured:
            raise PaymentGatewayError("Test payments are disabled.")

    async def create_order(
        self, *, receipt: str, amount_minor: int, currency: str,
        customer: CustomerInfo, description: str,
    ) -> GatewayOrder:
        self._require_enabled()
        order_id = f"mock_order_{receipt}"
        self._orders[order_id] = {"amount": amount_minor, "currency": currency, "status": "created"}
        return GatewayOrder(
            order_id=order_id,
            checkout={"order_id": order_id, "amount": amount_minor, "currency": currency, "mock": True},
        )

    @staticmethod
    def sign(order_id: str, payment_id: str) -> str:
        return hmac.new(_SECRET.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()

    def simulate(self, order_id: str, succeed: bool = True) -> dict:
        """What a real checkout popup would hand the browser (dev only)."""
        self._require_enabled()
        order = self._orders.get(order_id)
        if order is None:
            raise PaymentGatewayError("Unknown test order.")
        payment_id = f"mock_pay_{secrets.token_hex(6)}"
        order["status"] = "paid" if succeed else "failed"
        order["payment_id"] = payment_id
        return {"order_id": order_id, "payment_id": payment_id, "signature": self.sign(order_id, payment_id)}

    async def verify_client_payment(self, *, order_id: str, payload: Mapping[str, object]) -> VerifiedPayment:
        self._require_enabled()
        payment_id = str(payload.get("payment_id") or "")
        signature = str(payload.get("signature") or "")
        if str(payload.get("order_id") or "") != order_id or not payment_id:
            return VerifiedPayment(outcome="failed", failure_reason="Incomplete payment confirmation.")
        if not hmac.compare_digest(self.sign(order_id, payment_id), signature):
            return VerifiedPayment(outcome="failed", failure_reason="Payment signature is invalid.")
        return await self.fetch_order(order_id)

    async def fetch_order(self, order_id: str) -> VerifiedPayment:
        self._require_enabled()
        order = self._orders.get(order_id)
        if order is None:
            return VerifiedPayment(outcome="pending")
        if order["status"] == "paid":
            return VerifiedPayment(
                outcome="paid", gateway_payment_id=order["payment_id"],
                amount_minor=order["amount"], currency=order["currency"], method="test",
            )
        if order["status"] == "failed":
            return VerifiedPayment(outcome="failed", failure_reason="Test payment declined.")
        return VerifiedPayment(outcome="pending")

    @staticmethod
    def webhook_signature(raw_body: bytes) -> str:
        return hmac.new(_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()

    def parse_webhook(self, headers: Mapping[str, str], raw_body: bytes) -> WebhookEvent:
        self._require_enabled()
        if not hmac.compare_digest(self.webhook_signature(raw_body), header(headers, "x-mock-signature")):
            raise WebhookSignatureError("Invalid mock webhook signature.")
        body = json.loads(raw_body)
        outcome = body.get("outcome", "ignored")
        return WebhookEvent(
            event_id=str(body.get("event_id")),
            event_type=str(body.get("type", "mock")),
            outcome=outcome if outcome in ("paid", "failed", "refunded", "disputed", "dispute_lost") else "ignored",
            order_id=str(body.get("order_id", "")),
            gateway_payment_id=body.get("payment_id"),
            amount_minor=body.get("amount_minor"),
            currency=body.get("currency"),
            method="test",
            reference=str(body.get("reference", "")),
        )


__all__ = ["MockGateway"]
