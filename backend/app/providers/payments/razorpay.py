"""Razorpay gateway (Orders API + Standard Checkout + webhooks).

Flow
----
1. ``create_order`` → ``POST /v1/orders`` (amount in paise). The browser opens
   Razorpay Checkout with the returned ``order_id`` and the PUBLIC key id.
2. Checkout returns ``razorpay_order_id``, ``razorpay_payment_id`` and
   ``razorpay_signature``. ``verify_client_payment`` checks
   ``HMAC_SHA256(order_id|payment_id, key_secret)`` and then fetches the
   order's payments from Razorpay to confirm ``captured`` + amount.
3. Webhooks (``payment.captured``, ``order.paid``, ``payment.failed``) are
   verified with ``HMAC_SHA256(raw_body, webhook_secret)`` from the
   ``X-Razorpay-Signature`` header; ``X-Razorpay-Event-Id`` is the idempotency key.

Configure: ``RAZORPAY_KEY_ID``, ``RAZORPAY_KEY_SECRET``, ``RAZORPAY_WEBHOOK_SECRET``.
Test-mode keys (``rzp_test_…``) use the same API host.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import replace
from typing import Mapping

import httpx

from app.core.config import get_settings
from app.core.http import shared_client
from app.providers.payments.base import (
    CustomerInfo,
    GatewayNotConfigured,
    GatewayOrder,
    PaymentGateway,
    PaymentGatewayError,
    VerifiedPayment,
    WebhookEvent,
    WebhookSignatureError,
    header,
)

logger = logging.getLogger(__name__)

API_BASE = "https://api.razorpay.com/v1"
_TIMEOUT = httpx.Timeout(15.0, connect=10.0)


class RazorpayGateway(PaymentGateway):
    name = "razorpay"
    display_name = "Razorpay"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    # --------------------------------------------------------------- config
    @property
    def configured(self) -> bool:
        return get_settings().razorpay_configured

    def _auth(self) -> tuple[str, str]:
        s = get_settings()
        if not s.razorpay_configured:
            raise GatewayNotConfigured("Razorpay is not configured.")
        return s.razorpay_key_id, s.razorpay_key_secret

    def _http(self) -> httpx.AsyncClient:
        return self._client or shared_client()

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            resp = await self._http().request(
                method, f"{API_BASE}{path}", auth=self._auth(), timeout=_TIMEOUT, **kwargs
            )
        except httpx.HTTPError as exc:
            logger.warning("Razorpay %s %s failed: %s", method, path, type(exc).__name__)
            raise PaymentGatewayError("Payment gateway is unreachable. Please try again.") from exc
        if resp.status_code >= 400:
            # Log the gateway's error code only (no request bodies, no secrets).
            code = ""
            try:
                code = (resp.json().get("error") or {}).get("code", "")
            except ValueError:
                pass
            logger.warning("Razorpay %s %s -> HTTP %s %s", method, path, resp.status_code, code)
            raise PaymentGatewayError("Payment gateway rejected the request.")
        return resp.json()

    # --------------------------------------------------------------- orders
    async def create_order(
        self, *, receipt: str, amount_minor: int, currency: str,
        customer: CustomerInfo, description: str,
    ) -> GatewayOrder:
        data = await self._request(
            "POST",
            "/orders",
            json={
                "amount": amount_minor,
                "currency": currency,
                "receipt": receipt[:40],  # Razorpay limit
                "notes": {"user_id": str(customer.user_id), "plan": description[:200]},
            },
        )
        order_id = str(data.get("id") or "")
        if not order_id or int(data.get("amount", -1)) != amount_minor:
            raise PaymentGatewayError("Payment gateway returned an unexpected order.")
        s = get_settings()
        return GatewayOrder(
            order_id=order_id,
            checkout={
                "key_id": s.razorpay_key_id,  # PUBLIC key id — safe for the browser
                "order_id": order_id,
                "amount": amount_minor,
                "currency": currency,
                "name": s.payment_merchant_name,
                "description": description[:255],
                "prefill": {"email": customer.email, "name": customer.name},
            },
        )

    @staticmethod
    def checkout_signature(order_id: str, payment_id: str, secret: str) -> str:
        msg = f"{order_id}|{payment_id}".encode()
        return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()

    async def verify_client_payment(self, *, order_id: str, payload: Mapping[str, object]) -> VerifiedPayment:
        _, secret = self._auth()
        rp_order = str(payload.get("razorpay_order_id") or "")
        rp_payment = str(payload.get("razorpay_payment_id") or "")
        rp_sig = str(payload.get("razorpay_signature") or "")
        if not (rp_order and rp_payment and rp_sig) or rp_order != order_id:
            return VerifiedPayment(outcome="failed", failure_reason="Incomplete payment confirmation.")
        expected = self.checkout_signature(rp_order, rp_payment, secret)
        if not hmac.compare_digest(expected, rp_sig):
            logger.warning("Razorpay checkout signature mismatch for order %s", order_id)
            return VerifiedPayment(outcome="failed", failure_reason="Payment signature is invalid.")
        # Signature proves the browser didn't invent the ids; the API call
        # proves the money was actually captured, and for how much.
        return await self._payment_status(order_id, prefer_payment_id=rp_payment)

    async def fetch_order(self, order_id: str) -> VerifiedPayment:
        return await self._payment_status(order_id)

    async def _payment_status(self, order_id: str, prefer_payment_id: str | None = None) -> VerifiedPayment:
        data = await self._request("GET", f"/orders/{order_id}/payments")
        items = data.get("items") or []
        captured = [p for p in items if p.get("status") == "captured"]
        if prefer_payment_id:
            captured = [p for p in captured if p.get("id") == prefer_payment_id] or captured
        if captured:
            p = captured[0]
            return VerifiedPayment(
                outcome="paid",
                gateway_payment_id=str(p.get("id")),
                amount_minor=int(p.get("amount", 0)),
                currency=str(p.get("currency") or ""),
                method=str(p.get("method") or "")[:30],
            )
        if items and all(p.get("status") == "failed" for p in items):
            last = items[0]
            return VerifiedPayment(
                outcome="failed",
                gateway_payment_id=str(last.get("id") or "") or None,
                failure_reason=str(last.get("error_description") or "Payment failed.")[:300],
            )
        # "created" / "authorized" (awaiting capture) — the webhook finishes it.
        return VerifiedPayment(outcome="pending")

    # ------------------------------------------------------------- webhooks
    def parse_webhook(self, headers: Mapping[str, str], raw_body: bytes) -> WebhookEvent:
        secret = get_settings().razorpay_webhook_secret
        signature = header(headers, "x-razorpay-signature")
        if not secret:
            raise WebhookSignatureError("Razorpay webhook secret is not configured.")
        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        if not signature or not hmac.compare_digest(expected, signature):
            raise WebhookSignatureError("Invalid Razorpay webhook signature.")
        try:
            body = json.loads(raw_body)
        except ValueError as exc:
            raise WebhookSignatureError("Malformed webhook body.") from exc

        event_type = str(body.get("event") or "")
        event_id = header(headers, "x-razorpay-event-id") or hashlib.sha256(raw_body).hexdigest()
        payload = body.get("payload") or {}
        payment = (payload.get("payment") or {}).get("entity") or {}
        order = (payload.get("order") or {}).get("entity") or {}
        order_id = str(payment.get("order_id") or order.get("id") or "")
        base = WebhookEvent(
            event_id=event_id,
            event_type=event_type,
            outcome="ignored",
            order_id=order_id,
            gateway_payment_id=str(payment.get("id") or "") or None,
            amount_minor=int(payment["amount"]) if "amount" in payment else None,
            currency=str(payment.get("currency") or "") or None,
            method=str(payment.get("method") or "")[:30],
        )
        if event_type in ("payment.captured", "order.paid"):
            return replace(base, outcome="paid")
        if event_type == "payment.failed":
            reason = str(payment.get("error_description") or "Payment failed.")[:300]
            return replace(base, outcome="failed", failure_reason=reason)
        # refund.processed: payload.refund.entity {id, amount, currency, payment_id}
        # plus payload.payment.entity (with order_id) — Razorpay webhook docs.
        if event_type == "refund.processed":
            refund = (payload.get("refund") or {}).get("entity") or {}
            return replace(
                base, outcome="refunded", reference=str(refund.get("id") or "")[:100],
                amount_minor=int(refund["amount"]) if "amount" in refund else None,
                currency=str(refund.get("currency") or "") or base.currency,
            )
        if event_type in ("payment.dispute.created", "payment.dispute.lost"):
            dispute = (payload.get("dispute") or {}).get("entity") or {}
            return replace(
                base, outcome="disputed" if event_type.endswith("created") else "dispute_lost",
                reference=str(dispute.get("id") or "")[:100],
                failure_reason=str(dispute.get("reason_code") or "")[:300],
            )
        return base


__all__ = ["RazorpayGateway"]
