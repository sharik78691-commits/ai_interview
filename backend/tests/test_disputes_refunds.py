"""Chargebacks and refunds: Cashfree dispute webhooks, admin one-click
refunds, the chargeback ratio on the admin overview and the dispute
evidence pack.

No real gateway is contacted: webhooks are signed locally and the Cashfree
refund API runs against ``httpx.MockTransport``.
"""
import json
import time
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect as sa_inspect, select, text

from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.main import app
from app.models.activity_log import ANALYZE, LOGIN, ActivityLog
from app.models.billing import Payment, SubscriptionAuditLog, SubscriptionPlan, UserSubscription
from app.models.user import User
from app.providers.payments.cashfree import CashfreeGateway, _dispute_outcome
from app.services import payment_service
from app.services import subscription_service as subs
from tests.conftest import csrf_headers, register

ORDER = "oi_cf_1"


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "cashfree_client_id", "cf_id")
    monkeypatch.setattr(s, "cashfree_client_secret", "cf_secret")
    with SessionLocal() as db:
        subs.seed_default_plans(db)
    yield


def _client(email: str, admin: bool = False) -> TestClient:
    c = TestClient(app)
    register(c, email)
    if admin:
        with SessionLocal() as db:
            db.scalar(select(User).where(User.email == email)).is_admin = True
            db.commit()
    return c


def _cf_post(c: TestClient, body: dict, event_id: str = ""):
    raw = json.dumps(body).encode()
    ts = str(int(time.time() * 1000))
    headers = {"x-webhook-signature": CashfreeGateway.webhook_signature(ts, raw, "cf_secret"),
               "x-webhook-timestamp": ts, "Content-Type": "application/json"}
    if event_id:
        headers["x-idempotency-key"] = event_id
    return c.post("/api/payments/webhooks/cashfree", content=raw, headers=headers)


def _paid_cashfree(c: TestClient, email: str, order: str = ORDER) -> int:
    """A 1-month Cashfree payment, confirmed by a signed success webhook."""
    with SessionLocal() as db:
        u = db.scalar(select(User).where(User.email == email))
        plan = db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == "1-month"))
        p = Payment(user_id=u.id, user_email=email, plan_id=plan.id, plan_code=plan.code,
                    plan_name=plan.name, duration_days=plan.duration_days, duration_hours=plan.duration_hours,
                    screenshot_allowance=plan.screenshot_allowance, amount_minor=plan.price_minor,
                    currency="INR", gateway="cashfree", receipt=order, gateway_order_id=order)
        db.add(p)
        db.commit()
        pid, amount = p.id, plan.price_minor
    r = _cf_post(c, {"type": "PAYMENT_SUCCESS_WEBHOOK", "data": {
        "order": {"order_id": order},
        "payment": {"cf_payment_id": 9001, "payment_status": "SUCCESS", "payment_amount": amount / 100,
                    "payment_currency": "INR", "payment_group": "upi"}}})
    assert r.json() == {"status": "processed"}
    return pid


def _dispute(c: TestClient, kind: str, status: str, order: str = ORDER, dispute_id: str = "d_1"):
    return _cf_post(c, {"type": kind, "data": {
        "dispute": {"dispute_id": dispute_id, "dispute_type": "CHARGEBACK", "dispute_status": status,
                    "dispute_amount": 999.0, "reason_code": "4855",
                    "reason_description": "Goods or Services Not Provided",
                    "respond_by": "2026-11-05T23:59:59+05:30"},
        "order_details": {"order_id": order, "order_currency": "INR", "order_amount": 999.0,
                          "cf_payment_id": 9001}}})


def _me(c: TestClient) -> dict:
    return c.get("/api/billing/me").json()


# ------------------------------------------------------- dispute webhooks


def test_cashfree_dispute_won_keeps_access_and_counts_in_chargeback_ratio():
    c = _client("cb_won@example.com")
    pid = _paid_cashfree(c, "cb_won@example.com")

    assert _dispute(c, "DISPUTE_CREATED", "CHARGEBACK_CREATED").json() == {"status": "processed"}
    me = _me(c)
    assert me["payments"][0]["status"] == "disputed"
    assert me["current"] is not None  # access kept while the dispute is open
    with SessionLocal() as db:
        p = db.get(Payment, pid)
        assert p.disputed_at is not None and p.dispute_status == "CHARGEBACK_CREATED"
        assert p.refund_reference == "d_1"
        assert subs.aware(p.dispute_respond_by).isoformat() == "2026-11-05T18:29:59+00:00"  # 23:59:59 IST

    # Same status again is a retry of the same event; a new status is processed.
    assert _dispute(c, "DISPUTE_UPDATED", "CHARGEBACK_CREATED").json() == {"status": "duplicate"}
    assert _dispute(c, "DISPUTE_CLOSED", "CHARGEBACK_MERCHANT_WON").json() == {"status": "processed"}
    me = _me(c)
    assert me["payments"][0]["status"] == "paid" and me["current"] is not None
    with SessionLocal() as db:
        p = db.get(Payment, pid)
        assert p.disputed_at is not None and p.dispute_status == "CHARGEBACK_MERCHANT_WON"

    o = _client("cb_boss@example.com", admin=True).get("/api/admin/billing/overview").json()
    assert o["disputes_count"] == 1 and o["dispute_rate"] == 1.0 and o["dispute_value_rate_inr"] == 1.0
    assert o["open_disputes"] == 0


def test_cashfree_dispute_lost_ends_access_and_late_paid_event_cannot_regrant():
    c = _client("cb_lost@example.com")
    pid = _paid_cashfree(c, "cb_lost@example.com")
    _dispute(c, "DISPUTE_CREATED", "CHARGEBACK_CREATED")
    assert _dispute(c, "DISPUTE_CLOSED", "CHARGEBACK_MERCHANT_LOST").json() == {"status": "processed"}
    me = _me(c)
    assert me["current"] is None
    assert me["payments"][0]["status"] == "refunded"
    assert me["subscriptions"][0]["status"] == "refunded"
    with SessionLocal() as db:
        assert db.get(Payment, pid).dispute_status == "CHARGEBACK_MERCHANT_LOST"
    _cf_post(c, {"type": "PAYMENT_SUCCESS_WEBHOOK", "data": {
        "order": {"order_id": ORDER},
        "payment": {"cf_payment_id": 9001, "payment_status": "SUCCESS", "payment_amount": 999.0,
                    "payment_currency": "INR"}}}, event_id="late_paid")
    assert _me(c)["current"] is None


def test_open_disputes_and_deadline_on_overview():
    c = _client("cb_open@example.com")
    _paid_cashfree(c, "cb_open@example.com")
    _dispute(c, "DISPUTE_CREATED", "DISPUTE_CREATED")
    o = _client("cb_boss2@example.com", admin=True).get("/api/admin/billing/overview").json()
    assert o["open_disputes"] == 1
    assert o["next_dispute_deadline"].startswith("2026-11-05T18:29:59")


@pytest.mark.parametrize("status,outcome", [
    ("DISPUTE_CREATED", "disputed"),
    ("CHARGEBACK_CREATED", "disputed"),
    ("DISPUTE_DOCS_RECEIVED", "disputed"),
    ("DISPUTE_INSUFFICIENT_EVIDENCE", "disputed"),  # more documents asked; not final
    ("RETRIEVAL_MERCHANT_LOST", "disputed"),        # document request; no money moved
    ("DISPUTE_MERCHANT_WON", "dispute_won"),
    ("CHARGEBACK_MERCHANT_WON", "dispute_won"),
    ("PRE_ARBITRATION_MERCHANT_WON", "dispute_won"),
    ("CHARGEBACK_MERCHANT_LOST", "dispute_lost"),
    ("CHARGEBACK_MERCHANT_ACCEPTED", "dispute_lost"),
    ("ARBITRATION_MERCHANT_LOST", "dispute_lost"),
])
def test_cashfree_dispute_status_mapping(status, outcome):
    assert _dispute_outcome(status) == outcome


def test_razorpay_dispute_won_restores_paid(monkeypatch):
    from app.providers.payments.razorpay import RazorpayGateway
    import hashlib
    import hmac

    monkeypatch.setattr(get_settings(), "razorpay_webhook_secret", "whsec_test")
    body = json.dumps({"event": "payment.dispute.won", "payload": {
        "payment": {"entity": {"id": "pay_1", "order_id": "order_1", "amount": 9900, "currency": "INR"}},
        "dispute": {"entity": {"id": "disp_1"}}}}).encode()
    sig = hmac.new(b"whsec_test", body, hashlib.sha256).hexdigest()
    ev = RazorpayGateway().parse_webhook({"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": "e"}, body)
    assert (ev.outcome, ev.reference, ev.dispute_status) == ("dispute_won", "disp_1", "WON")


# ------------------------------------------------------------ admin refund


def _refund_api(monkeypatch, handler):
    gw = CashfreeGateway(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(payment_service, "get_gateway", lambda name: gw)


def test_admin_refund_calls_cashfree_once_and_ends_access(monkeypatch):
    c = _client("rf_user@example.com")
    pid = _paid_cashfree(c, "rf_user@example.com")
    admin = _client("rf_admin@example.com", admin=True)
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        body = json.loads(req.content)
        assert req.url.path == f"/pg/orders/{ORDER}/refunds"
        assert body["refund_amount"] == 999.0 and body["refund_id"] == f"rf_{ORDER}"
        return httpx.Response(200, json={"refund_id": body["refund_id"], "cf_refund_id": "cfr_77",
                                         "refund_amount": 999.0, "refund_status": "PENDING"})

    _refund_api(monkeypatch, handler)
    url = f"/api/admin/payments/{pid}/refund"
    assert admin.post(url, json={"reason": "Customer asked"}).status_code == 403  # CSRF required
    assert c.post(url, json={"reason": "x" * 5}, headers=csrf_headers(c)).status_code == 403  # not admin
    r = admin.post(url, json={"reason": "Customer asked"}, headers=csrf_headers(admin))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "refunded"
    me = _me(c)
    assert me["current"] is None and me["payments"][0]["refund_reference"] == "cfr_77"
    with SessionLocal() as db:
        audit = db.scalars(select(SubscriptionAuditLog).where(SubscriptionAuditLog.action == "refund")).all()
        admin_id = db.scalar(select(User.id).where(User.email == "rf_admin@example.com"))
        assert len(audit) == 1 and audit[0].actor_user_id == admin_id and "Customer asked" in audit[0].reason

    # A second click is refused before the gateway is called again.
    again = admin.post(url, json={"reason": "Customer asked"}, headers=csrf_headers(admin))
    assert again.status_code == 409
    assert calls == [("POST", f"/pg/orders/{ORDER}/refunds")]

    # The refund webhook that follows is idempotent.
    assert _cf_post(c, {"type": "REFUND_STATUS_WEBHOOK", "data": {"refund": {
        "cf_refund_id": "cfr_77", "order_id": ORDER, "refund_amount": 999.0,
        "refund_currency": "INR", "refund_status": "SUCCESS"}}}).json() == {"status": "processed"}
    assert _me(c)["payments"][0]["status"] == "refunded"


def test_admin_refund_refused_for_disputed_and_old_payments(monkeypatch):
    c = _client("rf_block@example.com")
    pid = _paid_cashfree(c, "rf_block@example.com")
    admin = _client("rf_admin2@example.com", admin=True)
    _refund_api(monkeypatch, lambda req: pytest.fail("gateway must not be called"))
    _dispute(c, "DISPUTE_CREATED", "CHARGEBACK_CREATED")
    r = admin.post(f"/api/admin/payments/{pid}/refund", json={"reason": "Refund"}, headers=csrf_headers(admin))
    assert r.status_code == 409 and "chargeback" in r.json()["detail"]

    _dispute(c, "DISPUTE_CLOSED", "CHARGEBACK_MERCHANT_WON")
    with SessionLocal() as db:
        db.get(Payment, pid).paid_at = subs.utcnow() - timedelta(days=181)
        db.commit()
    r = admin.post(f"/api/admin/payments/{pid}/refund", json={"reason": "Refund"}, headers=csrf_headers(admin))
    assert r.status_code == 409 and "180 days" in r.json()["detail"]
    assert admin.post("/api/admin/payments/99999/refund", json={"reason": "Refund"},
                      headers=csrf_headers(admin)).status_code == 404
    assert _me(c)["current"] is not None


async def test_cashfree_refund_retry_returns_the_existing_refund():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(409, json={"code": "refund_already_exists"})
        assert req.url.path == f"/pg/orders/{ORDER}/refunds/rf_{ORDER}"
        return httpx.Response(200, json={"refund_id": f"rf_{ORDER}", "cf_refund_id": "cfr_1",
                                         "refund_amount": 999.0, "refund_status": "SUCCESS"})

    gw = CashfreeGateway(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    r = await gw.create_refund(order_id=ORDER, amount_minor=99900, refund_id=f"rf_{ORDER}", note="n")
    assert (r.status, r.reference) == ("success", "cfr_1")


# ------------------------------------------------------- evidence pack


def test_evidence_pack_lists_usage_in_the_paid_period():
    c = _client("ev_user@example.com")
    pid = _paid_cashfree(c, "ev_user@example.com")
    with SessionLocal() as db:
        uid = db.scalar(select(User.id).where(User.email == "ev_user@example.com"))
        db.query(ActivityLog).filter(ActivityLog.user_id == uid).delete()
        now = subs.utcnow()
        # The plan started two hours ago; usage since then is evidence.
        sub = db.scalar(select(UserSubscription).where(UserSubscription.payment_id == pid))
        sub.starts_at = now - timedelta(hours=2)
        db.add_all([ActivityLog(kind=LOGIN, user_id=uid, created_at=now - timedelta(minutes=90)),
                    ActivityLog(kind=LOGIN, user_id=uid, created_at=now - timedelta(minutes=60)),
                    ActivityLog(kind=ANALYZE, user_id=uid, created_at=now - timedelta(minutes=30)),
                    # Before the purchase: not evidence for this payment.
                    ActivityLog(kind=LOGIN, user_id=uid, created_at=now - timedelta(days=3))])
        db.commit()
    url = f"/api/admin/payments/{pid}/evidence"
    assert c.get(url).status_code == 403
    admin = _client("ev_admin@example.com", admin=True)
    d = admin.get(url).json()
    assert d["payment"]["order_id"] == ORDER and d["customer"]["email"] == "ev_user@example.com"
    assert d["service"]["subscription_status"] == "active"
    assert (d["service"]["sign_ins"], d["service"]["questions_answered"]) == (2, 1)
    assert len(d["service"]["sign_in_times"]) == 2
    assert d["policies"]["refund_policy"].endswith("/refund-policy")
    txt = d["summary_text"]
    assert ORDER in txt and "INR 999.00" in txt and "No physical goods" in txt
    assert "Sign-ins: 2" in txt and "Interview questions answered: 1" in txt
    assert admin.get("/api/admin/payments/99999/evidence").status_code == 404


# ------------------------------------------------------------- migration


def test_dispute_columns_added_to_an_existing_payments_table():
    from app.db.session import _ensure_billing_columns

    with engine.begin() as conn:
        for col in ("disputed_at", "dispute_status", "dispute_respond_by"):
            conn.execute(text(f"ALTER TABLE payments DROP COLUMN {col}"))
    assert "disputed_at" not in {c["name"] for c in sa_inspect(engine).get_columns("payments")}
    _ensure_billing_columns()
    _ensure_billing_columns()  # idempotent
    cols = {c["name"] for c in sa_inspect(engine).get_columns("payments")}
    assert {"disputed_at", "dispute_status", "dispute_respond_by"} <= cols
