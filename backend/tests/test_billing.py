"""Subscriptions & payments: plans, checkout, verification, webhooks,
authorization, admin operations, gateway adapters and schema constraints.

No real gateway is contacted: the mock gateway drives end-to-end flows and
Razorpay/Cashfree adapters are exercised against ``httpx.MockTransport``.
"""
import hashlib
import hmac
import json
import time
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect as sa_inspect, select

from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.main import app
from app.models.billing import (
    Payment,
    PaymentWebhookEvent,
    SubscriptionAuditLog,
    SubscriptionPlan,
    UserSubscription,
)
from app.models.user import User
from app.services import subscription_service as subs
from tests.conftest import csrf_headers, register


# ------------------------------------------------------------- helpers


@pytest.fixture(autouse=True)
def _plans_and_mock(monkeypatch):
    monkeypatch.setattr(get_settings(), "payments_mock_enabled", True)
    db = SessionLocal()
    try:
        subs.seed_default_plans(db)
    finally:
        db.close()
    yield


def _user_client(email: str) -> TestClient:
    c = TestClient(app)
    register(c, email)
    return c


def _uid(email: str) -> int:
    db = SessionLocal()
    try:
        return db.scalar(select(User.id).where(User.email == email))
    finally:
        db.close()


def _admin_client(email: str = "boss@example.com") -> TestClient:
    c = _user_client(email)
    db = SessionLocal()
    try:
        db.scalar(select(User).where(User.email == email)).is_admin = True
        db.commit()
    finally:
        db.close()
    return c


def _plan_id(code: str = "1-month") -> int:
    db = SessionLocal()
    try:
        return db.scalar(select(SubscriptionPlan.id).where(SubscriptionPlan.code == code))
    finally:
        db.close()


def _buy_with_mock(client: TestClient, plan_code: str = "1-month", succeed: bool = True) -> dict:
    co = client.post(
        "/api/payments/checkout",
        json={"plan_id": _plan_id(plan_code), "gateway": "mock"},
        headers=csrf_headers(client),
    )
    assert co.status_code == 201, co.text
    order_id = co.json()["order_id"]
    sim = client.post(
        "/api/payments/mock/simulate", json={"order_id": order_id, "succeed": succeed},
        headers=csrf_headers(client),
    ).json()
    return {"checkout": co.json(), "sim": sim, "order_id": order_id}


def _verify(client: TestClient, order_id: str, payload: dict, gateway: str = "mock"):
    return client.post(
        f"/api/payments/{gateway}/verify", json={"order_id": order_id, "payload": payload},
        headers=csrf_headers(client),
    )


def _count(model, **where) -> int:
    db = SessionLocal()
    try:
        stmt = select(model)
        for k, v in where.items():
            stmt = stmt.where(getattr(model, k) == v)
        return len(db.scalars(stmt).all())
    finally:
        db.close()


# ---------------------------------------------------------------- plans


def test_public_plans_are_the_five_2026_10_plans(client):
    plans = client.get("/api/plans").json()
    assert [(p["code"], p["price_minor"], p["duration_hours"], p["screenshot_allowance"],
             p["billing_type"]) for p in plans] == [
        ("1-day", 9900, 24, 8, "one_time"),
        ("3-days", 19900, 72, 24, "one_time"),
        ("7-days", 39900, 168, 56, "one_time"),
        ("1-month", 99900, 720, 240, "recurring"),
        ("3-months", 249000, 2160, 720, "recurring"),
    ]
    assert all(p["currency"] == "INR" for p in plans)
    # Recurring plans are sold prepaid until auto-renewal exists.
    assert not any(p["auto_renew_available"] for p in plans)
    assert not any(p["badge"] or p["is_highlighted"] for p in plans)


def test_seed_is_idempotent_and_never_overwrites_admin_edits():
    db = SessionLocal()
    try:
        plan = db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == "1-month"))
        plan.price_minor = 12345
        db.commit()
        assert subs.seed_default_plans(db) == 0
        db.refresh(plan)
        assert plan.price_minor == 12345
    finally:
        db.close()


def test_promo_price_applies_only_inside_window(client):
    db = SessionLocal()
    try:
        plan = db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == "7-days"))
        now = subs.utcnow()
        plan.promo_price_minor, plan.promo_label = 20000, "Launch offer"
        plan.promo_starts_at, plan.promo_ends_at = now - timedelta(days=1), now + timedelta(days=1)
        db.commit()
        p = next(x for x in client.get("/api/plans").json() if x["code"] == "7-days")
        assert (p["price_minor"], p["regular_price_minor"], p["is_promo"]) == (20000, 39900, True)
        plan.promo_ends_at = now - timedelta(hours=1)
        db.commit()
        p = next(x for x in client.get("/api/plans").json() if x["code"] == "7-days")
        assert (p["price_minor"], p["is_promo"]) == (39900, False)
    finally:
        db.close()


# ---------------------------------------------------- checkout + verify


def test_full_mock_purchase_activates_subscription_once():
    c = _user_client("buyer@example.com")
    flow = _buy_with_mock(c)
    assert flow["checkout"]["amount_minor"] == 99900  # server-priced
    r1 = _verify(c, flow["order_id"], flow["sim"])
    assert r1.json()["status"] == "paid"
    r2 = _verify(c, flow["order_id"], flow["sim"])  # replay
    assert r2.json()["status"] == "paid"
    assert r1.json()["subscription_id"] == r2.json()["subscription_id"]
    assert _count(UserSubscription, user_id=_uid("buyer@example.com")) == 1

    me = c.get("/api/billing/me").json()
    assert me["current"]["plan_code"] == "1-month"
    assert me["current"]["status"] == "active"
    assert me["payments"][0]["status"] == "paid"
    assert me["payments"][0]["verified_via"] == "client_verify"


def test_frontend_success_claim_without_valid_signature_grants_nothing():
    c = _user_client("forger@example.com")
    co = c.post("/api/payments/checkout", json={"plan_id": _plan_id(), "gateway": "mock"},
                headers=csrf_headers(c)).json()
    forged = {"order_id": co["order_id"], "payment_id": "mock_pay_x", "signature": "0" * 64}
    assert _verify(c, co["order_id"], forged).json()["status"] == "failed"
    assert _count(UserSubscription, user_id=_uid("forger@example.com")) == 0
    # Signed but not actually paid at the gateway -> still pending, no access.
    co2 = c.post("/api/payments/checkout", json={"plan_id": _plan_id(), "gateway": "mock"},
                 headers=csrf_headers(c)).json()
    from app.providers.payments.mock import MockGateway
    sig = MockGateway.sign(co2["order_id"], "mock_pay_unpaid")
    r = _verify(c, co2["order_id"], {"order_id": co2["order_id"], "payment_id": "mock_pay_unpaid",
                                       "signature": sig})
    assert r.json()["status"] == "pending"
    assert _count(UserSubscription, user_id=_uid("forger@example.com")) == 0


def test_declined_payment_is_failed_without_access():
    c = _user_client("declined@example.com")
    flow = _buy_with_mock(c, succeed=False)
    assert _verify(c, flow["order_id"], flow["sim"]).json()["status"] == "failed"
    assert c.get("/api/billing/me").json()["current"] is None


def test_checkout_requires_csrf_and_login(client):
    assert client.post("/api/payments/checkout", json={"plan_id": 1, "gateway": "mock"}).status_code == 401
    c = _user_client("csrf-buy@example.com")
    r = c.post("/api/payments/checkout", json={"plan_id": _plan_id(), "gateway": "mock"})
    assert r.status_code == 403


def test_unconfigured_gateway_and_inactive_plan_rejected():
    c = _user_client("nogw@example.com")
    r = c.post("/api/payments/checkout", json={"plan_id": _plan_id(), "gateway": "razorpay"},
               headers=csrf_headers(c))
    assert r.status_code == 400 and "not available" in r.json()["detail"]
    db = SessionLocal()
    try:
        db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == "3-days")).is_active = False
        db.commit()
    finally:
        db.close()
    r = c.post("/api/payments/checkout", json={"plan_id": _plan_id("3-days"), "gateway": "mock"},
               headers=csrf_headers(c))
    assert r.status_code == 400


def test_new_checkout_supersedes_older_unpaid_order():
    c = _user_client("twice@example.com")
    for _ in range(2):
        c.post("/api/payments/checkout", json={"plan_id": _plan_id(), "gateway": "mock"},
               headers=csrf_headers(c))
    uid = _uid("twice@example.com")
    assert _count(Payment, user_id=uid, status="created") == 1
    assert _count(Payment, user_id=uid, status="cancelled") == 1


def test_repurchase_starts_immediately_as_a_separate_subscription():
    """Policy 2026-10: a second purchase is its own row, active right away,
    with its own expiry and credits (nothing merged, nothing reset)."""
    c = _user_client("stack@example.com")
    for code in ("1-month", "7-days"):
        f = _buy_with_mock(c, code)
        _verify(c, f["order_id"], f["sim"])
    me = c.get("/api/billing/me").json()
    newest, first = me["subscriptions"][0], me["subscriptions"][1]
    assert newest["is_renewal"] is True and newest["status"] == "active"
    assert first["status"] == "active"
    assert (newest["screenshot_allowance"], first["screenshot_allowance"]) == (56, 240)
    # Access ends with the longer (30-day) plan, not 30 + 7 days.
    ends = subs.aware(__import__("datetime").datetime.fromisoformat(me["access_ends_at"]))
    assert abs((ends - subs.utcnow()) - timedelta(days=30)) < timedelta(minutes=1)
    # Credits are drawn from the soonest-expiring plan first.
    assert me["usage"]["subscription_id"] == newest["id"]
    assert me["usage"]["total_remaining"] == 296


def test_plan_price_change_never_changes_history():
    c = _user_client("history@example.com")
    f = _buy_with_mock(c)
    _verify(c, f["order_id"], f["sim"])
    admin = _admin_client()
    r = admin.patch(f"/api/admin/plans/{_plan_id()}", json={"price_minor": 129900},
                    headers=csrf_headers(admin))
    assert r.status_code == 200 and r.json()["price_minor"] == 129900
    pay = c.get("/api/billing/me").json()["payments"][0]
    assert pay["amount_minor"] == 99900
    assert next(p for p in c.get("/api/plans").json() if p["code"] == "1-month")["price_minor"] == 129900


# --------------------------------------------------------- authorization


def test_users_cannot_touch_other_users_orders_or_admin_endpoints():
    alice = _user_client("alice@example.com")
    bob = _user_client("bob@example.com")
    f = _buy_with_mock(alice)
    assert _verify(bob, f["order_id"], f["sim"]).status_code == 404
    r = bob.post("/api/payments/mock/simulate", json={"order_id": f["order_id"]}, headers=csrf_headers(bob))
    assert r.status_code == 404
    assert bob.get("/api/billing/me").json()["payments"] == []
    for path in ("/api/admin/plans", "/api/admin/subscriptions", "/api/admin/payments",
                 f"/api/admin/users/{_uid('alice@example.com')}/billing"):
        assert bob.get(path).status_code == 403
    r = bob.post(f"/api/admin/users/{_uid('bob@example.com')}/subscriptions",
                 json={"days": 30, "reason": "self grant"}, headers=csrf_headers(bob))
    assert r.status_code == 403


def test_mock_gateway_hidden_and_disabled_when_off(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "payments_mock_enabled", False)
    assert all(g["key"] != "mock" for g in client.get("/api/payments/gateways").json())
    c = _user_client("nomock@example.com")
    r = c.post("/api/payments/mock/simulate", json={"order_id": "x"}, headers=csrf_headers(c))
    assert r.status_code == 404


# -------------------------------------------------------------- webhooks


def _rzp_payment(email: str, order_id: str = "order_TEST1", amount: int = 90000) -> int:
    uid = _uid(email)
    db = SessionLocal()
    try:
        plan = db.get(SubscriptionPlan, _plan_id())
        p = Payment(user_id=uid, user_email=email, plan_id=plan.id, plan_code=plan.code,
                    plan_name=plan.name, duration_days=plan.duration_days, amount_minor=amount,
                    currency="INR", gateway="razorpay", receipt=f"oi_{uid}_{order_id}",
                    gateway_order_id=order_id, status="created")
        db.add(p)
        db.commit()
        return p.id
    finally:
        db.close()


@pytest.fixture
def rzp(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "razorpay_key_id", "rzp_test_key")
    monkeypatch.setattr(s, "razorpay_key_secret", "rzp_test_secret")
    monkeypatch.setattr(s, "razorpay_webhook_secret", "whsec_test")
    return s


def _rzp_event(order_id: str, amount: int = 90000, event: str = "payment.captured",
               pay_id: str = "pay_TEST1") -> bytes:
    return json.dumps({
        "event": event,
        "payload": {"payment": {"entity": {
            "id": pay_id, "order_id": order_id, "amount": amount, "currency": "INR",
            "method": "upi", "status": "captured" if event != "payment.failed" else "failed",
            "error_description": "Bank declined" if event == "payment.failed" else None,
        }}},
    }).encode()


def _rzp_post(client, body: bytes, event_id: str = "evt_1", secret: str = "whsec_test"):
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/api/payments/webhooks/razorpay", content=body, headers={
        "X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": event_id, "Content-Type": "application/json"})


def test_razorpay_webhook_activates_once_and_dedupes(rzp):
    c = _user_client("hook@example.com")
    pid = _rzp_payment("hook@example.com")
    body = _rzp_event("order_TEST1")
    assert _rzp_post(c, body).json() == {"status": "processed"}
    assert _rzp_post(c, body).json() == {"status": "duplicate"}
    assert _rzp_post(c, body, event_id="evt_2").json() == {"status": "processed"}  # same payment, new event
    assert _count(UserSubscription, payment_id=pid) == 1
    assert _count(PaymentWebhookEvent, gateway="razorpay") == 2
    me = c.get("/api/billing/me").json()
    assert me["current"]["source"] == "payment"
    assert me["payments"][0]["verified_via"] == "webhook"


def test_razorpay_webhook_bad_signature_rejected_and_not_stored(rzp):
    c = _user_client("badsig@example.com")
    _rzp_payment("badsig@example.com")
    r = _rzp_post(c, _rzp_event("order_TEST1"), secret="wrong")
    assert r.status_code == 400
    assert _count(PaymentWebhookEvent) == 0
    assert c.get("/api/billing/me").json()["current"] is None


def test_webhook_amount_mismatch_never_activates(rzp):
    c = _user_client("mismatch@example.com")
    pid = _rzp_payment("mismatch@example.com")
    _rzp_post(c, _rzp_event("order_TEST1", amount=100))
    assert _count(UserSubscription, payment_id=pid) == 0
    assert c.get("/api/billing/me").json()["payments"][0]["status"] == "failed"


def test_failed_webhook_then_success_still_activates(rzp):
    c = _user_client("retry@example.com")
    pid = _rzp_payment("retry@example.com")
    _rzp_post(c, _rzp_event("order_TEST1", event="payment.failed", pay_id="pay_F"), event_id="e1")
    assert c.get("/api/billing/me").json()["payments"][0]["status"] == "failed"
    _rzp_post(c, _rzp_event("order_TEST1", pay_id="pay_OK"), event_id="e2")
    assert _count(UserSubscription, payment_id=pid) == 1


def test_webhook_for_unknown_order_is_acknowledged(rzp):
    c = _user_client("unknown@example.com")
    assert _rzp_post(c, _rzp_event("order_NOPE")).json() == {"status": "unknown_order"}


def test_cashfree_webhook_signature_and_replay_window(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "cashfree_client_id", "cf_id")
    monkeypatch.setattr(s, "cashfree_client_secret", "cf_secret")
    from app.providers.payments.base import WebhookSignatureError
    from app.providers.payments.cashfree import CashfreeGateway

    gw = CashfreeGateway()
    body = json.dumps({"type": "PAYMENT_SUCCESS_WEBHOOK", "data": {
        "order": {"order_id": "oi_1_abc"},
        "payment": {"cf_payment_id": 555, "payment_status": "SUCCESS", "payment_amount": 900.0,
                    "payment_currency": "INR", "payment_group": "upi"}}}).encode()
    ts = str(int(time.time() * 1000))
    sig = CashfreeGateway.webhook_signature(ts, body, "cf_secret")
    ev = gw.parse_webhook({"x-webhook-signature": sig, "x-webhook-timestamp": ts}, body)
    assert (ev.outcome, ev.order_id, ev.amount_minor, ev.gateway_payment_id) == ("paid", "oi_1_abc", 90000, "555")
    with pytest.raises(WebhookSignatureError):
        gw.parse_webhook({"x-webhook-signature": "AAAA", "x-webhook-timestamp": ts}, body)
    old = str(int((time.time() - 3600) * 1000))
    with pytest.raises(WebhookSignatureError):
        gw.parse_webhook({"x-webhook-signature": CashfreeGateway.webhook_signature(old, body, "cf_secret"),
                          "x-webhook-timestamp": old}, body)


# ------------------------------------------------------- gateway adapters


async def test_razorpay_adapter_creates_order_and_verifies_against_api(rzp):
    from app.providers.payments.base import CustomerInfo
    from app.providers.payments.razorpay import RazorpayGateway

    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path))
        assert req.headers["authorization"].startswith("Basic ")
        if req.method == "POST" and req.url.path == "/v1/orders":
            body = json.loads(req.content)
            return httpx.Response(200, json={"id": "order_X", "amount": body["amount"], "currency": "INR"})
        if req.url.path == "/v1/orders/order_X/payments":
            return httpx.Response(200, json={"items": [
                {"id": "pay_X", "status": "captured", "amount": 90000, "currency": "INR", "method": "card"}]})
        return httpx.Response(404)

    gw = RazorpayGateway(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    order = await gw.create_order(receipt="oi_1_x", amount_minor=90000, currency="INR",
                                  customer=CustomerInfo(1, "a@b.c"), description="1 Month")
    assert order.order_id == "order_X"
    assert order.checkout["key_id"] == "rzp_test_key" and "secret" not in json.dumps(order.checkout)
    sig = RazorpayGateway.checkout_signature("order_X", "pay_X", "rzp_test_secret")
    v = await gw.verify_client_payment(order_id="order_X", payload={
        "razorpay_order_id": "order_X", "razorpay_payment_id": "pay_X", "razorpay_signature": sig})
    assert (v.outcome, v.gateway_payment_id, v.amount_minor) == ("paid", "pay_X", 90000)
    bad = await gw.verify_client_payment(order_id="order_X", payload={
        "razorpay_order_id": "order_X", "razorpay_payment_id": "pay_X", "razorpay_signature": "bad"})
    assert bad.outcome == "failed"
    assert ("GET", "/v1/orders/order_X/payments") in seen


async def test_cashfree_adapter_creates_order_and_fetches_status(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "cashfree_client_id", "cf_id")
    monkeypatch.setattr(s, "cashfree_client_secret", "cf_secret")
    from app.providers.payments.base import CustomerInfo, PaymentGatewayError
    from app.providers.payments.cashfree import CashfreeGateway

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "sandbox.cashfree.com"
        assert req.headers["x-client-id"] == "cf_id" and req.headers["x-api-version"]
        if req.method == "POST":
            body = json.loads(req.content)
            assert body["order_amount"] == "900.00" and body["customer_details"]["customer_phone"] == "9876543210"
            return httpx.Response(200, json={"order_id": body["order_id"], "order_amount": 900.0,
                                             "payment_session_id": "session_abc"})
        if req.url.path.endswith("/payments"):
            return httpx.Response(200, json=[{"cf_payment_id": 77, "payment_status": "SUCCESS",
                                              "payment_amount": 900.0, "payment_currency": "INR",
                                              "payment_group": "upi"}])
        return httpx.Response(200, json={"order_status": "PAID", "order_amount": 900.0, "order_currency": "INR"})

    gw = CashfreeGateway(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(PaymentGatewayError):
        await gw.create_order(receipt="oi_1_a", amount_minor=90000, currency="INR",
                              customer=CustomerInfo(1, "a@b.c"), description="x")
    order = await gw.create_order(receipt="oi_1_a", amount_minor=90000, currency="INR",
                                  customer=CustomerInfo(1, "a@b.c", phone="+91 98765 43210"), description="x")
    assert order.checkout == {"order_id": "oi_1_a", "payment_session_id": "session_abc", "mode": "sandbox",
                              "amount": 90000, "currency": "INR"}
    v = await gw.verify_client_payment(order_id="oi_1_a", payload={"order_id": "oi_1_a"})
    assert (v.outcome, v.gateway_payment_id, v.amount_minor, v.method) == ("paid", "77", 90000, "upi")


# ------------------------------------------------------------ admin ops


def test_admin_grant_extend_revoke_are_audited_and_separate_from_payments():
    admin = _admin_client()
    _user_client("vip@example.com")
    uid = _uid("vip@example.com")
    r = admin.post(f"/api/admin/users/{uid}/subscriptions", json={"days": 10, "reason": "Support goodwill"},
                   headers=csrf_headers(admin))
    assert r.status_code == 201, r.text
    sub = r.json()
    assert sub["source"] == "manual" and sub["payment_id"] is None and sub["amount_minor"] == 0
    assert admin.post(f"/api/admin/users/{uid}/subscriptions", json={"days": 10, "reason": ""},
                      headers=csrf_headers(admin)).status_code == 422
    r = admin.post(f"/api/admin/subscriptions/{sub['id']}/extend", json={"days": 5, "reason": "Outage"},
                   headers=csrf_headers(admin))
    assert r.status_code == 200
    r = admin.post(f"/api/admin/subscriptions/{sub['id']}/revoke", json={"reason": "Abuse report"},
                   headers=csrf_headers(admin))
    assert r.json()["status"] == "revoked"
    assert _count(Payment, user_id=uid) == 0
    detail = admin.get(f"/api/admin/users/{uid}/billing").json()
    assert [a["action"] for a in detail["audit"]] == ["revoke", "extend", "grant"]
    assert all(a["actor_user_id"] == _uid("boss@example.com") for a in detail["audit"])
    assert detail["current"] is None


def test_admin_mutations_need_csrf():
    admin = _admin_client()
    _user_client("nocsrf@example.com")
    uid = _uid("nocsrf@example.com")
    assert admin.post(f"/api/admin/users/{uid}/subscriptions", json={"days": 3, "reason": "x y z"}).status_code == 403
    assert admin.post("/api/admin/plans", json={"code": "x-plan", "name": "X", "price_minor": 100,
                                                "duration_days": 1}).status_code == 403


def test_admin_plan_crud_and_validation():
    admin = _admin_client()
    h = csrf_headers(admin)
    r = admin.post("/api/admin/plans", json={"code": "6-months", "name": "6 Months", "price_minor": 350000,
                                             "duration_days": 180, "features": ["A", " ", "B"],
                                             "trial_days": 1}, headers=h)
    assert r.status_code == 201 and r.json()["features"] == ["A", "B"]
    pid = r.json()["id"]
    assert admin.post("/api/admin/plans", json={"code": "6-months", "name": "dup", "price_minor": 1,
                                                "duration_days": 1}, headers=h).status_code == 409
    assert admin.patch(f"/api/admin/plans/{pid}", json={"promo_price_minor": 400000}, headers=h).status_code == 422
    assert admin.patch(f"/api/admin/plans/{pid}", json={"is_active": False}, headers=h).json()["is_active"] is False
    public_codes = [p["code"] for p in admin.get("/api/plans").json()]
    assert "6-months" not in public_codes
    assert "6-months" in [p["code"] for p in admin.get("/api/admin/plans").json()]
    assert _count(SubscriptionAuditLog, action="plan_create") == 1
    # The rejected promo edit (422) was neither saved nor audited.
    assert _count(SubscriptionAuditLog, action="plan_update") == 1
    assert next(p for p in admin.get("/api/admin/plans").json() if p["id"] == pid)["promo_price_minor"] is None


def test_admin_filters_search_by_id_email_status_gateway():
    admin = _admin_client()
    c = _user_client("findme@example.com")
    f = _buy_with_mock(c)
    _verify(c, f["order_id"], f["sim"])
    uid = _uid("findme@example.com")
    by_email = admin.get("/api/admin/subscriptions", params={"q": "findme"}).json()
    by_id = admin.get("/api/admin/subscriptions", params={"q": str(uid)}).json()
    assert by_email["total"] == by_id["total"] == 1
    assert by_email["items"][0]["gateway"] == "mock"
    assert admin.get("/api/admin/subscriptions", params={"status": "expired"}).json()["total"] == 0
    assert admin.get("/api/admin/subscriptions", params={"gateway": "razorpay"}).json()["total"] == 0
    pays = admin.get("/api/admin/payments", params={"q": f["order_id"]}).json()
    assert pays["total"] == 1 and pays["items"][0]["status"] == "paid"
    summary = admin.get("/api/admin/users/billing-summary", params={"ids": str(uid)}).json()
    assert summary[str(uid)]["plan_name"] == "Monthly Plan"


def test_trial_only_once_and_only_when_offered():
    c = _user_client("trial@example.com")
    pid = _plan_id("3-days")
    assert c.post("/api/billing/trial", json={"plan_id": pid}, headers=csrf_headers(c)).status_code == 400
    db = SessionLocal()
    try:
        db.get(SubscriptionPlan, pid).trial_days = 1
        db.commit()
    finally:
        db.close()
    r = c.post("/api/billing/trial", json={"plan_id": pid}, headers=csrf_headers(c))
    assert r.status_code == 201 and r.json()["source"] == "trial"
    assert c.post("/api/billing/trial", json={"plan_id": pid}, headers=csrf_headers(c)).status_code == 400
    assert c.get("/api/billing/me").json()["trial_used"] is True


# ------------------------------------------------------------ schema


def test_billing_schema_constraints_and_indexes():
    insp = sa_inspect(engine)
    tables = set(insp.get_table_names())
    assert {"subscription_plans", "user_subscriptions", "payments", "payment_webhook_events",
            "subscription_audit_logs"} <= tables
    uniques = {u["name"] for u in insp.get_unique_constraints("payments")}
    assert {"uq_payments_gateway_order", "uq_payments_gateway_payment"} <= uniques
    assert "uq_payment_webhook_events_gateway_event" in {
        u["name"] for u in insp.get_unique_constraints("payment_webhook_events")}
    fks = {(fk["referred_table"], tuple(fk["constrained_columns"])) for fk in insp.get_foreign_keys("user_subscriptions")}
    assert ("users", ("user_id",)) in fks and ("payments", ("payment_id",)) in fks
    idx = {i["name"] for i in insp.get_indexes("user_subscriptions")}
    assert "ix_user_subscriptions_user_expires" in idx


def test_negative_price_rejected_by_database():
    from sqlalchemy.exc import IntegrityError

    db = SessionLocal()
    try:
        db.add(SubscriptionPlan(code="neg", name="neg", price_minor=-1, duration_days=1))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_secret_values_never_in_public_responses(client, rzp):
    text = json.dumps(client.get("/api/payments/gateways").json()) + json.dumps(client.get("/api/plans").json())
    assert "rzp_test_secret" not in text and "whsec_test" not in text


def test_startup_migration_is_idempotent_on_existing_database():
    """init_db() + seeding run on every start: twice in a row must be a no-op
    that keeps existing billing data (works on SQLite and PostgreSQL)."""
    from app.db.session import init_db

    c = _user_client("keep@example.com")
    f = _buy_with_mock(c)
    _verify(c, f["order_id"], f["sim"])
    before = (_count(Payment), _count(UserSubscription), _count(SubscriptionPlan))
    for _ in range(2):
        init_db()
        db = SessionLocal()
        try:
            assert subs.seed_default_plans(db) == 0
        finally:
            db.close()
    assert (_count(Payment), _count(UserSubscription), _count(SubscriptionPlan)) == before
    assert "sessions_revoked_at" in {col["name"] for col in sa_inspect(engine).get_columns("users")}
