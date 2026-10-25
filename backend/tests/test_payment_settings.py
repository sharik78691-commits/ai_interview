"""Admin payment settings: site-wide on/off switch, per-gateway on/off and the default gateway.

Checkout, the public gateway list and the pricing currencies must all follow
the admin's choice; orders that already exist must still verify.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.main import app
from app.models.billing import SubscriptionAuditLog, SubscriptionPlan
from app.models.user import User
from app.providers.payments.base import GatewayOrder
from app.providers.payments.registry import get_gateway
from app.services import subscription_service as subs
from tests.conftest import csrf_headers, register


@pytest.fixture(autouse=True)
def _plans_and_mock(monkeypatch):
    monkeypatch.setattr(get_settings(), "payments_mock_enabled", True)
    db = SessionLocal()
    try:
        subs.seed_default_plans(db)
    finally:
        db.close()
    yield


@pytest.fixture
def both_gateways(monkeypatch):
    """Cashfree and Razorpay 'configured' with fake keys; no network is used."""
    s = get_settings()
    monkeypatch.setattr(s, "razorpay_key_id", "rzp_test_key")
    monkeypatch.setattr(s, "razorpay_key_secret", "rzp_test_secret")
    monkeypatch.setattr(s, "cashfree_client_id", "cf_id")
    monkeypatch.setattr(s, "cashfree_client_secret", "cf_secret")
    for name in ("razorpay", "cashfree"):
        async def fake_order(*, receipt, amount_minor, currency, customer, description, _n=name):
            return GatewayOrder(order_id=f"{_n}_order_{receipt}", checkout={"gateway": _n})
        monkeypatch.setattr(get_gateway(name), "create_order", fake_order)
    return s


def _user(email: str) -> TestClient:
    c = TestClient(app)
    register(c, email)
    return c


def _admin(email: str = "pay-admin@example.com") -> TestClient:
    c = _user(email)
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


def _set(admin: TestClient, **body):
    return admin.patch("/api/admin/payment-settings", json=body, headers=csrf_headers(admin))


def _checkout(c: TestClient, gateway: str):
    return c.post("/api/payments/checkout", json={"plan_id": _plan_id(), "gateway": gateway, "phone": "9876543210"},
                  headers=csrf_headers(c))


# ------------------------------------------------------------ defaults


def test_defaults_keep_cashfree_active_and_payments_on(client, both_gateways):
    st = client.get("/api/payments/status").json()
    assert st["payments_enabled"] is True and st["active_gateway"] == "cashfree"
    assert st["available"] is True and st["active_gateway_name"] == "Cashfree"
    keys = [g["key"] for g in client.get("/api/payments/gateways").json()]
    assert "cashfree" in keys and "razorpay" not in keys  # Razorpay starts switched off


# ---------------------------------------------------- admin-only API


def test_payment_settings_api_is_admin_only_and_needs_csrf(client):
    assert client.get("/api/admin/payment-settings").status_code == 401
    user = _user("not-admin@example.com")
    assert user.get("/api/admin/payment-settings").status_code == 403
    assert _set(user, payments_enabled=False).status_code == 403
    admin = _admin()
    assert admin.patch("/api/admin/payment-settings", json={"payments_enabled": False}).status_code == 403
    assert admin.get("/api/payments/status").json()["payments_enabled"] is True


def test_admin_view_lists_both_gateways_with_configured_flag(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "razorpay_key_id", "")
    monkeypatch.setattr(s, "razorpay_key_secret", "")
    admin = _admin()
    body = admin.get("/api/admin/payment-settings").json()
    assert [g["key"] for g in body["gateways"]] == ["cashfree", "razorpay"]
    rzp = {g["key"]: g for g in body["gateways"]}["razorpay"]
    assert rzp["configured"] is False and rzp["enabled"] is False and rzp["is_default"] is False
    # Never leaks secrets.
    assert "secret" not in str(body).lower() and "rzp_" not in str(body)


def test_razorpay_can_be_enabled_and_made_default_before_keys_exist(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "razorpay_key_id", "")
    monkeypatch.setattr(s, "razorpay_key_secret", "")
    admin = _admin()
    r = _set(admin, gateways_enabled={"razorpay": True})
    assert r.status_code == 200
    rzp = {g["key"]: g for g in r.json()["gateways"]}["razorpay"]
    assert rzp["enabled"] is True and rzp["configured"] is False
    r = _set(admin, active_gateway="razorpay")
    assert r.status_code == 200 and r.json()["active_gateway"] == "razorpay"
    # Not offered at checkout until its keys are set — no broken checkout.
    assert "razorpay" not in [g["key"] for g in admin.get("/api/payments/gateways").json()]
    assert _set(admin, active_gateway="mock").status_code == 422  # dev-only, never selectable


def test_last_gateway_cannot_be_switched_off():
    admin = _admin()
    r = _set(admin, gateways_enabled={"cashfree": False})
    assert r.status_code == 422 and "at least one gateway" in r.json()["detail"]
    assert admin.get("/api/admin/payment-settings").json()["gateways"][0]["enabled"] is True


# --------------------------------------------------- gateway selection


def test_both_gateways_on_customer_can_pay_with_either(both_gateways):
    admin = _admin()
    buyer = _user("buyer-both@example.com")
    out = _set(admin, gateways_enabled={"razorpay": True})
    assert out.status_code == 200
    # Default first, so the pricing page pre-selects it.
    keys = [g["key"] for g in buyer.get("/api/payments/gateways").json()]
    assert keys[:2] == ["cashfree", "razorpay"]
    assert buyer.get("/api/payments/status").json()["gateway_names"] == ["Cashfree", "Razorpay"]
    assert _checkout(buyer, "cashfree").json()["gateway"] == "cashfree"
    assert _checkout(buyer, "razorpay").json()["gateway"] == "razorpay"

    assert _set(admin, active_gateway="razorpay").status_code == 200
    keys = [g["key"] for g in buyer.get("/api/payments/gateways").json()]
    assert keys[:2] == ["razorpay", "cashfree"]

    # Switching the default off moves the default to the other enabled gateway.
    out = _set(admin, gateways_enabled={"razorpay": False}).json()
    assert out["active_gateway"] == "cashfree"
    assert _checkout(buyer, "razorpay").status_code == 400


def test_checkout_follows_the_selected_gateway(both_gateways):
    admin = _admin()
    buyer = _user("buyer-gw@example.com")

    r = _checkout(buyer, "cashfree")
    assert r.status_code == 201 and r.json()["gateway"] == "cashfree"
    r = _checkout(buyer, "razorpay")
    assert r.status_code == 400 and "not available" in r.json()["detail"]

    out = _set(admin, active_gateway="razorpay", gateways_enabled={"cashfree": False}, reason="switch for test")
    assert out.status_code == 200 and out.json()["active_gateway"] == "razorpay"
    keys = [g["key"] for g in buyer.get("/api/payments/gateways").json()]
    assert "razorpay" in keys and "cashfree" not in keys
    assert buyer.get("/api/payments/status").json()["active_gateway_name"] == "Razorpay"

    r = _checkout(buyer, "razorpay")
    assert r.status_code == 201 and r.json()["gateway"] == "razorpay"
    assert r.json()["order_id"].startswith("razorpay_order_")
    assert _checkout(buyer, "cashfree").status_code == 400

    db = SessionLocal()
    try:
        log = db.scalar(select(SubscriptionAuditLog).where(SubscriptionAuditLog.action == "payment_settings"))
        assert log is not None and log.reason == "switch for test"
    finally:
        db.close()


# ------------------------------------------------------ payments off


def test_payments_off_blocks_checkout_with_clear_message(both_gateways):
    admin = _admin()
    buyer = _user("buyer-off@example.com")
    assert _set(admin, payments_enabled=False).status_code == 200

    st = buyer.get("/api/payments/status").json()
    assert st["payments_enabled"] is False and st["available"] is False
    assert "temporarily unavailable" in st["message"]
    assert buyer.get("/api/payments/gateways").json() == []

    for gw in ("cashfree", "razorpay", "mock"):
        r = _checkout(buyer, gw)
        assert r.status_code == 503, (gw, r.text)
        assert "temporarily unavailable" in r.json()["detail"]

    # Pricing still shows plans, but nothing is payable.
    assert all(not p["payable"] for p in buyer.get("/api/plans").json())
    inr = next(c for c in buyer.get("/api/pricing/currencies").json() if c["code"] == "INR")
    assert inr["payable_now"] is False and inr["gateways"] == []

    assert _set(admin, payments_enabled=True).status_code == 200
    assert _checkout(buyer, "cashfree").status_code == 201


def test_existing_order_still_verifies_after_payments_are_switched_off():
    admin = _admin()
    buyer = _user("buyer-late@example.com")
    co = _checkout(buyer, "mock")
    assert co.status_code == 201
    order_id = co.json()["order_id"]
    sim = buyer.post("/api/payments/mock/simulate", json={"order_id": order_id, "succeed": True},
                     headers=csrf_headers(buyer)).json()

    assert _set(admin, payments_enabled=False).status_code == 200
    r = buyer.post("/api/payments/mock/verify", json={"order_id": order_id, "payload": sim},
                   headers=csrf_headers(buyer))
    assert r.status_code == 200 and r.json()["status"] == "paid"
