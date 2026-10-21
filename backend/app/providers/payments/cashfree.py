"""Cashfree Payments gateway (PG Orders API + JS SDK checkout + webhooks).

Flow
----
1. ``create_order`` → ``POST /pg/orders`` with OUR ``order_id`` (the receipt),
   the amount in rupees (exact 2-decimal string from minor units) and customer
   details. The browser opens the Cashfree JS SDK with the returned
   ``payment_session_id``.
2. After checkout Cashfree redirects back to ``return_url``. The browser only
   tells us the ``order_id``; ``verify_client_payment`` ignores everything
   else and asks Cashfree (``GET /pg/orders/{id}`` + ``/payments``) whether the
   order is PAID and for how much.
3. Webhooks are verified with
   ``base64(HMAC_SHA256(x-webhook-timestamp + raw_body, client_secret))`` from
   ``x-webhook-signature``; old timestamps are rejected (replay protection).

Configure: ``CASHFREE_CLIENT_ID``, ``CASHFREE_CLIENT_SECRET``,
``CASHFREE_ENV=sandbox|production``, optional ``CASHFREE_API_VERSION``.

Note: Cashfree requires ``customer_phone`` on every order; the checkout form
asks for it when Cashfree is selected. It is passed through, not stored.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
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
    to_major_str,
    to_minor,
)

logger = logging.getLogger(__name__)

HOSTS = {
    "sandbox": "https://sandbox.cashfree.com/pg",
    "production": "https://api.cashfree.com/pg",
}
_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
#: Webhooks older than this are rejected as possible replays.
WEBHOOK_TOLERANCE_SECONDS = 10 * 60


class CashfreeGateway(PaymentGateway):
    name = "cashfree"
    display_name = "Cashfree"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    # --------------------------------------------------------------- config
    @property
    def configured(self) -> bool:
        return get_settings().cashfree_configured

    @property
    def mode(self) -> str:
        return "production" if get_settings().cashfree_env == "production" else "sandbox"

    def _headers(self) -> dict[str, str]:
        s = get_settings()
        if not s.cashfree_configured:
            raise GatewayNotConfigured("Cashfree is not configured.")
        return {
            "x-client-id": s.cashfree_client_id,
            "x-client-secret": s.cashfree_client_secret,
            "x-api-version": s.cashfree_api_version,
            "Accept": "application/json",
        }

    def _http(self) -> httpx.AsyncClient:
        return self._client or shared_client()

    async def _request(self, method: str, path: str, **kwargs) -> dict | list:
        url = f"{HOSTS[self.mode]}{path}"
        try:
            resp = await self._http().request(
                method, url, headers=self._headers(), timeout=_TIMEOUT, **kwargs
            )
        except httpx.HTTPError as exc:
            logger.warning("Cashfree %s %s failed: %s", method, path, type(exc).__name__)
            raise PaymentGatewayError("Payment gateway is unreachable. Please try again.") from exc
        if resp.status_code >= 400:
            code = ""
            try:
                code = str(resp.json().get("code", ""))
            except (ValueError, AttributeError):
                pass
            logger.warning("Cashfree %s %s -> HTTP %s %s", method, path, resp.status_code, code)
            raise PaymentGatewayError("Payment gateway rejected the request.")
        return resp.json()

    # --------------------------------------------------------------- orders
    async def create_order(
        self, *, receipt: str, amount_minor: int, currency: str,
        customer: CustomerInfo, description: str,
    ) -> GatewayOrder:
        phone = "".join(ch for ch in customer.phone if ch.isdigit())[-10:]
        if len(phone) != 10:
            raise PaymentGatewayError("A valid 10-digit phone number is required for Cashfree.")
        s = get_settings()
        frontend = s.frontend_url.rstrip("/")
        backend = s.backend_url.rstrip("/")
        data = await self._request(
            "POST",
            "/orders",
            json={
                "order_id": receipt,
                "order_amount": to_major_str(amount_minor),
                "order_currency": currency,
                "customer_details": {
                    "customer_id": f"user_{customer.user_id}",
                    "customer_email": customer.email,
                    "customer_phone": phone,
                    **({"customer_name": customer.name[:100]} if customer.name else {}),
                },
                "order_meta": {
                    "return_url": f"{frontend}/billing?gateway=cashfree&order_id={receipt}",
                    "notify_url": f"{backend}/api/payments/webhooks/cashfree",
                },
                "order_note": description[:200],
            },
        )
        assert isinstance(data, dict)
        session_id = str(data.get("payment_session_id") or "")
        if str(data.get("order_id")) != receipt or not session_id:
            raise PaymentGatewayError("Payment gateway returned an unexpected order.")
        if to_minor(data.get("order_amount", -1)) != amount_minor:
            raise PaymentGatewayError("Payment gateway returned an unexpected amount.")
        return GatewayOrder(
            order_id=receipt,
            checkout={
                "order_id": receipt,
                "payment_session_id": session_id,  # single-order token, not a secret key
                "mode": self.mode,
                "amount": amount_minor,
                "currency": currency,
            },
        )

    async def verify_client_payment(self, *, order_id: str, payload: Mapping[str, object]) -> VerifiedPayment:
        # Nothing the browser sends is trusted beyond WHICH order to check.
        claimed = str(payload.get("order_id") or order_id)
        if claimed != order_id:
            return VerifiedPayment(outcome="failed", failure_reason="Order mismatch.")
        return await self.fetch_order(order_id)

    async def fetch_order(self, order_id: str) -> VerifiedPayment:
        order = await self._request("GET", f"/orders/{order_id}")
        assert isinstance(order, dict)
        status = str(order.get("order_status") or "").upper()
        if status != "PAID":
            if status in ("EXPIRED", "TERMINATED"):
                return VerifiedPayment(outcome="failed", failure_reason=f"Order {status.lower()}.")
            return VerifiedPayment(outcome="pending")
        payments = await self._request("GET", f"/orders/{order_id}/payments")
        success = [
            p for p in (payments if isinstance(payments, list) else [])
            if str(p.get("payment_status") or "").upper() == "SUCCESS"
        ]
        p = success[0] if success else {}
        return VerifiedPayment(
            outcome="paid",
            gateway_payment_id=str(p.get("cf_payment_id") or "") or None,
            amount_minor=to_minor(p.get("payment_amount", order.get("order_amount", 0))),
            currency=str(p.get("payment_currency") or order.get("order_currency") or ""),
            method=str(p.get("payment_group") or "")[:30],
        )

    # ------------------------------------------------------------- webhooks
    @staticmethod
    def webhook_signature(timestamp: str, raw_body: bytes, secret: str) -> str:
        digest = hmac.new(secret.encode(), timestamp.encode() + raw_body, hashlib.sha256).digest()
        return base64.b64encode(digest).decode()

    def parse_webhook(self, headers: Mapping[str, str], raw_body: bytes) -> WebhookEvent:
        secret = get_settings().cashfree_client_secret
        signature = header(headers, "x-webhook-signature")
        timestamp = header(headers, "x-webhook-timestamp")
        if not secret:
            raise WebhookSignatureError("Cashfree is not configured.")
        if not signature or not timestamp:
            raise WebhookSignatureError("Missing Cashfree webhook signature headers.")
        expected = self.webhook_signature(timestamp, raw_body, secret)
        if not hmac.compare_digest(expected, signature):
            raise WebhookSignatureError("Invalid Cashfree webhook signature.")
        try:
            raw_ts = int(timestamp)
        except ValueError as exc:
            raise WebhookSignatureError("Invalid webhook timestamp.") from exc
        ts_seconds = raw_ts / 1000 if raw_ts > 10**12 else float(raw_ts)  # ms or s
        if abs(time.time() - ts_seconds) > WEBHOOK_TOLERANCE_SECONDS:
            raise WebhookSignatureError("Webhook timestamp outside the allowed window.")
        try:
            body = json.loads(raw_body)
        except ValueError as exc:
            raise WebhookSignatureError("Malformed webhook body.") from exc

        event_type = str(body.get("type") or "")
        data = body.get("data") or {}
        # Refund webhook: type REFUND_STATUS_WEBHOOK, data.refund {cf_refund_id,
        # order_id, refund_amount, refund_currency, refund_status} — Cashfree docs.
        refund = data.get("refund") or {}
        if event_type == "REFUND_STATUS_WEBHOOK" and refund:
            cf_refund_id = str(refund.get("cf_refund_id") or "")
            event_id = header(headers, "x-idempotency-key") or (
                f"refund:{cf_refund_id}" if cf_refund_id else hashlib.sha256(raw_body).hexdigest()
            )
            ok = str(refund.get("refund_status") or "").upper() == "SUCCESS"
            return WebhookEvent(
                event_id=event_id[:128], event_type=event_type[:60],
                outcome="refunded" if ok else "ignored",
                order_id=str(refund.get("order_id") or ""),
                amount_minor=to_minor(refund["refund_amount"]) if "refund_amount" in refund else None,
                currency=str(refund.get("refund_currency") or "") or None,
                reference=cf_refund_id[:100],
            )
        order = data.get("order") or {}
        payment = data.get("payment") or {}
        cf_payment_id = str(payment.get("cf_payment_id") or "")
        event_id = (
            header(headers, "x-idempotency-key")
            or (f"{event_type}:{cf_payment_id}" if cf_payment_id else "")
            or hashlib.sha256(raw_body).hexdigest()
        )
        base = WebhookEvent(
            event_id=event_id[:128],
            event_type=event_type[:60],
            outcome="ignored",
            order_id=str(order.get("order_id") or ""),
            gateway_payment_id=cf_payment_id or None,
            amount_minor=to_minor(payment["payment_amount"]) if "payment_amount" in payment else None,
            currency=str(payment.get("payment_currency") or "") or None,
            method=str(payment.get("payment_group") or "")[:30],
        )
        status = str(payment.get("payment_status") or "").upper()
        if event_type.startswith("PAYMENT_SUCCESS") or status == "SUCCESS":
            return replace(base, outcome="paid")
        if event_type.startswith(("PAYMENT_FAILED", "PAYMENT_USER_DROPPED")) or status in (
            "FAILED", "USER_DROPPED",
        ):
            reason = str(payment.get("payment_message") or "Payment failed.")[:300]
            return replace(base, outcome="failed", failure_reason=reason)
        return base


__all__ = ["CashfreeGateway"]
