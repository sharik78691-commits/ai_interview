"""2026-10 pricing: five plans, INR/USD prices, exact expiry, screenshot
credits, refunds, the user_id/user_email schema rule, the one-time catalogue
migration and admin authorization.

Timestamps are fixed (no wall-clock dependence) wherever expiry is checked.
"""
import base64
import hashlib
import hmac
import json
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect as sa_inspect, select, text

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import SessionLocal, engine, init_db, tables_missing_user_email
from app.main import app
from app.models.activity_log import ActivityLog
from app.models.billing import (
    Payment,
    PlanPrice,
    SchemaMigration,
    ScreenshotUsage,
    SubscriptionAuditLog,
    SubscriptionPlan,
    UserSubscription,
)
from app.models.user import PasswordResetToken, User
from app.services import payment_service, pricing_service, screenshot_credit_service as credits
from app.services import screenshot_service as svc
from app.services import subscription_service as subs
from tests.conftest import csrf_headers, register

IST = ZoneInfo("Asia/Kolkata")
JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 200).decode()


@pytest.fixture(autouse=True)
def _catalogue(monkeypatch):
    monkeypatch.setattr(get_settings(), "payments_mock_enabled", True)
    db = SessionLocal()
    try:
        subs.seed_default_plans(db)
    finally:
        db.close()
    yield


# ---------------------------------------------------------------- helpers


def _client(email: str) -> TestClient:
    c = TestClient(app)
    register(c, email)
    return c


def _admin(email: str = "admin2@example.com") -> TestClient:
    c = _client(email)
    with SessionLocal() as db:
        db.scalar(select(User).where(User.email == email)).is_admin = True
        db.commit()
    return c


def _user(email: str) -> User:
    with SessionLocal() as db:
        u = db.scalar(select(User).where(User.email == email))
        db.expunge(u)
        return u


def _plan(code: str) -> SubscriptionPlan:
    with SessionLocal() as db:
        p = db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == code))
        db.expunge(p)
        return p


def _payment(email: str, code: str, *, status: str = "created", currency: str = "INR",
             amount: int | None = None, order: str = "order_X") -> int:
    u, plan = _user(email), _plan(code)
    with SessionLocal() as db:
        p = Payment(user_id=u.id, plan_id=plan.id, plan_code=plan.code, plan_name=plan.name,
                    duration_days=plan.duration_days, duration_hours=plan.duration_hours,
                    screenshot_allowance=plan.screenshot_allowance,
                    amount_minor=plan.price_minor if amount is None else amount, currency=currency,
                    gateway="mock", receipt=f"oi_{u.id}_{order}", gateway_order_id=order, status=status)
        db.add(p)
        db.commit()
        return p.id


def _buy(c: TestClient, code: str, currency: str = "INR", **extra):
    co = c.post("/api/payments/checkout",
                json={"plan_id": _plan(code).id, "gateway": "mock", "currency": currency, **extra},
                headers=csrf_headers(c))
    if co.status_code != 201:
        return co, None
    order = co.json()["order_id"]
    sim = c.post("/api/payments/mock/simulate", json={"order_id": order, "succeed": True},
                 headers=csrf_headers(c)).json()
    v = c.post("/api/payments/mock/verify", json={"order_id": order, "payload": sim}, headers=csrf_headers(c))
    return co, v


def _analyze(c: TestClient, n: int = 1, key: str | None = None):
    headers = csrf_headers(c)
    if key:
        headers["Idempotency-Key"] = key
    return c.post("/api/screenshots/analyze",
                  json={"images": [{"mimeType": "image/jpeg", "data": JPEG}] * n}, headers=headers)


@pytest.fixture
def gemini(monkeypatch):
    """Fake AI: returns a valid answer unless ``state['fail']`` is set."""
    monkeypatch.setattr(get_settings(), "gemini_api_key", "AIzaFAKEfakeFAKEfakeFAKEfake1234")
    state = {"fail": None, "calls": 0}

    async def fake_analyze(images, client=None):
        state["calls"] += 1
        if state["fail"]:
            raise svc.ScreenshotAnalysisError(state["fail"], "boom", 502)
        return svc.normalize_result({"questions": ["Q"], "directAnswer": "A"})

    monkeypatch.setattr(svc, "analyze", fake_analyze)
    return state


# ---------------------------------------------------------------- pricing


def test_five_default_plans_in_db_with_inr_prices_and_no_usd(client):
    with SessionLocal() as db:
        rows = db.scalars(select(PlanPrice)).all()
    assert {r.currency for r in rows} == {"INR"}
    assert sorted(r.amount_minor for r in rows) == [9900, 19900, 39900, 99900, 249000]
    usd = client.get("/api/plans", params={"currency": "USD"}).json()
    assert len(usd) == 5 and not any(p["available"] or p["payable"] for p in usd)
    cur = {c["code"]: c for c in client.get("/api/pricing/currencies").json()}
    assert cur["INR"]["payable_now"] is True and cur["USD"]["payable_now"] is False
    assert client.get("/api/plans", params={"currency": "EUR"}).status_code == 404


def test_usd_price_is_independent_and_checkout_charges_exact_server_amount():
    admin = _admin()
    pid = _plan("1-day").id
    r = admin.put(f"/api/admin/plans/{pid}/prices", json={"currency": "USD", "amount_minor": 199},
                  headers=csrf_headers(admin))
    assert r.status_code == 200
    c = _client("usd@example.com")
    # Shown but not payable until USD payments are switched on.
    p = next(x for x in c.get("/api/plans", params={"currency": "USD"}).json() if x["code"] == "1-day")
    assert (p["available"], p["payable"], p["price_minor"], p["currency"]) == (True, False, 199, "USD")
    co, _ = _buy(c, "1-day", "USD")
    assert co.status_code == 400 and "USD" in co.json()["detail"]
    admin.patch("/api/admin/currencies/USD", json={"payments_enabled": True, "reason": "go live"},
                headers=csrf_headers(admin))
    # A browser-sent amount is ignored; the server price is charged.
    co, v = _buy(c, "1-day", "USD", amount_minor=1)
    assert co.status_code == 201 and (co.json()["amount_minor"], co.json()["currency"]) == (199, "USD")
    assert v.json()["status"] == "paid"
    # INR price untouched by the USD edit.
    inr = next(x for x in c.get("/api/plans").json() if x["code"] == "1-day")
    assert inr["price_minor"] == 9900
    with SessionLocal() as db:
        assert db.scalar(select(SubscriptionAuditLog).where(SubscriptionAuditLog.action == "currency_update"))


def test_usd_checkout_refused_when_gateway_does_not_support_it(monkeypatch):
    """Display currency never forces an unsupported payment currency."""
    admin = _admin()
    admin.put(f"/api/admin/plans/{_plan('1-day').id}/prices", json={"currency": "USD", "amount_minor": 199},
              headers=csrf_headers(admin))
    admin.patch("/api/admin/currencies/USD", json={"payments_enabled": True}, headers=csrf_headers(admin))
    from app.providers.payments import registry

    monkeypatch.setattr(registry, "gateway_currencies", lambda name: frozenset({"INR"}))
    c = _client("nousd@example.com")
    co, _ = _buy(c, "1-day", "USD")
    assert co.status_code == 400 and "does not accept USD" in co.json()["detail"]


def test_inactive_plan_cannot_be_bought_and_hidden_plan_not_listed(client):
    admin = _admin()
    pid = _plan("3-days").id
    admin.patch(f"/api/admin/plans/{pid}", json={"is_active": False}, headers=csrf_headers(admin))
    c = _client("inactive@example.com")
    co, _ = _buy(c, "3-days")
    assert co.status_code == 400
    admin.patch(f"/api/admin/plans/{_plan('7-days').id}", json={"is_public": False}, headers=csrf_headers(admin))
    codes = [p["code"] for p in client.get("/api/plans").json()]
    assert "3-days" not in codes and "7-days" not in codes


def test_price_change_is_versioned_and_never_changes_past_payments():
    c = _client("hist@example.com")
    _buy(c, "1-day")
    admin = _admin()
    pid = _plan("1-day").id
    admin.put(f"/api/admin/plans/{pid}/prices", json={"currency": "INR", "amount_minor": 12900},
              headers=csrf_headers(admin))
    history = admin.get(f"/api/admin/plans/{pid}/prices").json()
    inr = [h for h in history if h["currency"] == "INR"]
    assert [h["amount_minor"] for h in inr] == [12900, 9900]
    assert inr[0]["is_current"] and inr[1]["effective_to"] is not None
    assert c.get("/api/billing/me").json()["payments"][0]["amount_minor"] == 9900
    assert next(p for p in c.get("/api/plans").json() if p["code"] == "1-day")["price_minor"] == 12900


def test_admin_plan_edit_changes_duration_credits_and_billing_type():
    admin = _admin()
    pid = _plan("1-day").id
    r = admin.patch(f"/api/admin/plans/{pid}", json={
        "duration_hours": 36, "screenshot_allowance": 12, "billing_type": "one_time",
        "description": "Edited", "is_highlighted": True, "sort_order": 1}, headers=csrf_headers(admin))
    body = r.json()
    assert (body["duration_hours"], body["duration_days"], body["screenshot_allowance"]) == (36, 2, 12)
    # Existing purchases keep their snapshot; new ones get the new values.
    c = _client("edit@example.com")
    _buy(c, "1-day")
    sub = c.get("/api/billing/me").json()["current"]
    assert sub["screenshot_allowance"] == 12


def test_duplicate_checkout_idempotency_key_is_refused():
    c = _client("idem@example.com")
    body = {"plan_id": _plan("1-day").id, "gateway": "mock", "idempotency_key": "chk-0001-abcdef"}
    assert c.post("/api/payments/checkout", json=body, headers=csrf_headers(c)).status_code == 201
    assert c.post("/api/payments/checkout", json=body, headers=csrf_headers(c)).status_code == 409
    with SessionLocal() as db:
        assert len(db.scalars(select(Payment)).all()) == 1


# ---------------------------------------------------------------- expiry


ACTIVATED = datetime(2026, 10, 21, 23, 15, tzinfo=IST)


@pytest.mark.parametrize("code,delta", [
    ("1-day", timedelta(hours=24)), ("3-days", timedelta(hours=72)), ("7-days", timedelta(hours=168)),
    ("1-month", timedelta(days=30)), ("3-months", timedelta(days=90)),
])
def test_exact_expiry_from_activation_instant(code, delta):
    _client("exp@example.com")
    pid = _payment("exp@example.com", code, status="paid")
    with SessionLocal() as db:
        sub = subs.activate_from_payment(db, db.get(Payment, pid), now=ACTIVATED.astimezone(timezone.utc))
        db.commit()
        starts, ends = subs.aware(sub.starts_at), subs.aware(sub.expires_at)
    assert ends - starts == delta
    assert starts == ACTIVATED  # same instant regardless of the timezone it is shown in
    if code == "1-day":
        assert ends.astimezone(IST) == datetime(2026, 10, 22, 23, 15, tzinfo=IST)
        with SessionLocal() as db:
            s = db.get(UserSubscription, sub.id)
            # Still active after midnight and one minute before the end...
            assert subs.derived_status(s, datetime(2026, 10, 22, 0, 1, tzinfo=IST)) == "active"
            assert subs.derived_status(s, ends - timedelta(minutes=1)) == "active"
            # ...and expired exactly at the end.
            assert subs.derived_status(s, ends) == "expired"


def test_expired_subscription_cannot_analyze_and_login_does_not_extend(gemini):
    c = _client("old@example.com")
    u = _user("old@example.com")
    with SessionLocal() as db:
        db.add(UserSubscription(user_id=u.id, user_email=u.email, plan_code="1-day", plan_name="1 Day Pass",
                                status="active", source="manual", screenshot_allowance=8,
                                starts_at=subs.utcnow() - timedelta(hours=25),
                                expires_at=subs.utcnow() - timedelta(hours=1)))
        db.commit()
    r = _analyze(c)
    assert r.status_code == 402 and r.json()["detail"]["code"] == "SUBSCRIPTION_REQUIRED"
    c.post("/api/auth/login", json={"email": "old@example.com", "password": "Passw0rd123"})
    assert c.get("/api/billing/me").json()["current"] is None
    assert gemini["calls"] == 0


def test_pending_or_failed_payment_grants_no_access(gemini):
    c = _client("pend@example.com")
    _payment("pend@example.com", "1-day", status="created")
    _payment("pend@example.com", "3-days", status="failed", order="order_F")
    assert c.get("/api/billing/me").json()["current"] is None
    assert _analyze(c).status_code == 402


# ---------------------------------------------------------------- credits


def test_one_day_pass_allows_exactly_eight_screenshots(gemini):
    c = _client("eight@example.com")
    _buy(c, "1-day")
    assert _analyze(c, 5).status_code == 200
    r = _analyze(c, 3)
    assert r.status_code == 200 and r.json()["usage"]["remaining"] == 0
    r = _analyze(c, 1)
    assert r.status_code == 402 and r.json()["detail"]["code"] == "NO_CREDITS"
    usage = c.get("/api/billing/me/usage").json()
    assert (usage["allowance"], usage["used"], usage["remaining"]) == (8, 8, 0)
    assert gemini["calls"] == 2


def test_request_larger_than_remaining_is_refused_without_charge(gemini):
    c = _client("big@example.com")
    _buy(c, "1-day")
    _analyze(c, 6)
    r = _analyze(c, 3)
    assert r.status_code == 402 and "2 left" in r.json()["detail"]["message"]
    assert c.get("/api/billing/me/usage").json()["used"] == 6


def test_duplicate_request_id_is_charged_once(gemini):
    c = _client("dup@example.com")
    _buy(c, "1-day")
    assert _analyze(c, 2, key="req-aaaa-0001").status_code == 200
    r = _analyze(c, 2, key="req-aaaa-0001")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "DUPLICATE_REQUEST"
    assert c.get("/api/billing/me/usage").json()["used"] == 2
    assert gemini["calls"] == 1


def test_failed_analysis_releases_credits_and_retry_is_charged_once(gemini):
    c = _client("fail@example.com")
    _buy(c, "1-day")
    gemini["fail"] = "PROVIDER_ERROR"
    assert _analyze(c, 3, key="req-bbbb-0001").status_code == 502
    assert c.get("/api/billing/me/usage").json()["used"] == 0
    gemini["fail"] = None
    assert _analyze(c, 3, key="req-bbbb-0001").status_code == 200
    assert c.get("/api/billing/me/usage").json()["used"] == 3
    with SessionLocal() as db:
        row = db.scalar(select(ScreenshotUsage).where(ScreenshotUsage.request_id == "req-bbbb-0001"))
        assert (row.status, row.attempts, row.credits_consumed, row.user_email) == (
            "completed", 2, 3, "fail@example.com")


def test_rejected_before_processing_costs_nothing(gemini):
    c = _client("reject@example.com")
    _buy(c, "1-day")
    bad = c.post("/api/screenshots/analyze", json={"images": [{"mimeType": "image/gif", "data": "x"}]},
                 headers=csrf_headers(c))
    assert bad.status_code == 422
    assert c.get("/api/billing/me/usage").json()["used"] == 0


def test_admin_has_unlimited_screenshot_analyses_without_a_plan(gemini, monkeypatch):
    # The per-minute spend guard still applies to admins; lift it to test the count.
    monkeypatch.setattr("app.api.screenshots.ANALYZE_PER_MINUTE", 1000)
    c = _admin("unlimited-admin@example.com")
    cfg = c.get("/api/screenshots/config").json()
    assert cfg["usage"]["unlimited"] is True
    for i in range(12):  # more than any small plan allows, and no plan at all
        r = _analyze(c, 1, key=f"adm-req-{i:04d}")
        assert r.status_code == 200, r.text
        assert r.json()["usage"]["unlimited"] is True
    with SessionLocal() as db:
        rows = db.scalars(select(ScreenshotUsage).where(
            ScreenshotUsage.user_email == "unlimited-admin@example.com")).all()
        assert len(rows) == 12
        assert all(r.status == "completed" and r.subscription_id is None for r in rows)
    # Idempotency still applies to admins.
    r = _analyze(c, 1, key="adm-req-0000")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "DUPLICATE_REQUEST"


def test_admin_with_a_plan_is_not_charged_and_failures_release_cleanly(gemini, monkeypatch):
    # The per-minute spend guard still applies to admins; lift it to test the count.
    monkeypatch.setattr("app.api.screenshots.ANALYZE_PER_MINUTE", 1000)
    c = _admin("planned-admin@example.com")
    _buy(c, "1-day")
    for i in range(10):  # beyond the 1-day pass's 8 credits
        assert _analyze(c, 1, key=f"padm-req-{i:04d}").status_code == 200
    assert c.get("/api/billing/me/usage").json()["used"] == 0
    gemini["fail"] = "PROVIDER_ERROR"
    assert _analyze(c, 1, key="padm-fail-0001").status_code == 502
    gemini["fail"] = None
    assert c.get("/api/billing/me/usage").json()["used"] == 0


def test_non_admin_still_needs_a_plan(gemini):
    c = _client("plain-user@example.com")
    r = _analyze(c, 1)
    assert r.status_code == 402 and r.json()["detail"]["code"] == "SUBSCRIPTION_REQUIRED"
    assert c.get("/api/screenshots/config").json()["usage"]["unlimited"] is False


def test_concurrent_reservations_never_exceed_allowance():
    _client("race@example.com")
    pid = _payment("race@example.com", "1-day", status="paid")
    with SessionLocal() as db:
        subs.activate_from_payment(db, db.get(Payment, pid))
        db.commit()
    user = _user("race@example.com")
    results: list[str] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        with SessionLocal() as db:
            try:
                credits.reserve(db, user, f"race-req-{i:04d}", 1)
                out = "ok"
            except credits.CreditError as exc:
                out = exc.code
            except Exception as exc:  # SQLite "database is locked" under contention
                out = type(exc).__name__
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with SessionLocal() as db:
        sub = db.scalar(select(UserSubscription).where(UserSubscription.payment_id == pid))
        held = len(db.scalars(select(ScreenshotUsage).where(ScreenshotUsage.status == "reserved")).all())
    # Worker-thread SQLite connections cache the schema; drop them so the
    # next test's fresh schema is seen by every pooled connection.
    engine.dispose()
    assert results.count("ok") <= 8
    assert sub.screenshots_used == results.count("ok") == held <= sub.screenshot_allowance


def test_credits_spent_from_soonest_expiring_plan_without_merging(gemini):
    c = _client("two@example.com")
    _buy(c, "1-month")
    _buy(c, "1-day")
    r = _analyze(c, 8)
    me = c.get("/api/billing/me").json()
    by_code = {s["plan_code"]: s for s in me["subscriptions"]}
    assert r.status_code == 200
    assert by_code["1-day"]["screenshots_used"] == 8 and by_code["1-month"]["screenshots_used"] == 0
    # 1-day pass is exhausted: next request goes to the monthly plan.
    assert _analyze(c, 2).status_code == 200
    me = c.get("/api/billing/me").json()
    assert {s["plan_code"]: s["screenshots_used"] for s in me["subscriptions"]} == {"1-day": 8, "1-month": 2}


def test_duplicate_webhook_and_reverify_do_not_reset_credits(gemini):
    c = _client("reset@example.com")
    co, v = _buy(c, "1-day")
    _analyze(c, 4)
    order = co.json()["order_id"]
    with SessionLocal() as db:
        pid = db.scalar(select(Payment.id).where(Payment.gateway_order_id == order))
        payment_service.mark_paid(db, pid, gateway_payment_id=None, amount_minor=None, currency=None,
                                  method="", via="webhook")
    assert c.get("/api/billing/me/usage").json()["used"] == 4
    with SessionLocal() as db:
        assert len(db.scalars(select(UserSubscription)).all()) == 1


# ---------------------------------------------------------------- payments


def test_currency_mismatch_from_gateway_is_rejected():
    _client("cur@example.com")
    pid = _payment("cur@example.com", "1-day")
    with SessionLocal() as db:
        r = payment_service.mark_paid(db, pid, gateway_payment_id="p1", amount_minor=9900, currency="USD",
                                      method="", via="webhook")
    assert r.status == "failed"
    with SessionLocal() as db:
        assert db.scalar(select(UserSubscription)) is None


def _rzp_signed(c: TestClient, body: dict, event_id: str):
    raw = json.dumps(body).encode()
    sig = hmac.new(b"whsec_test", raw, hashlib.sha256).hexdigest()
    return c.post("/api/payments/webhooks/razorpay", content=raw, headers={
        "X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": event_id, "Content-Type": "application/json"})


def test_refund_webhook_ends_access_and_late_paid_event_cannot_regrant(monkeypatch, gemini):
    s = get_settings()
    monkeypatch.setattr(s, "razorpay_key_id", "rzp_test_key")
    monkeypatch.setattr(s, "razorpay_key_secret", "rzp_test_secret")
    monkeypatch.setattr(s, "razorpay_webhook_secret", "whsec_test")
    c = _client("refund@example.com")
    u, plan = _user("refund@example.com"), _plan("1-day")
    with SessionLocal() as db:
        db.add(Payment(user_id=u.id, plan_id=plan.id, plan_code=plan.code, plan_name=plan.name,
                       duration_days=1, duration_hours=24, screenshot_allowance=8, amount_minor=9900,
                       currency="INR", gateway="razorpay", receipt="oi_r1", gateway_order_id="order_R1"))
        db.commit()
    pay = {"id": "pay_R1", "order_id": "order_R1", "amount": 9900, "currency": "INR", "status": "captured"}
    assert _rzp_signed(c, {"event": "payment.captured", "payload": {"payment": {"entity": pay}}},
                       "evt_p").json()["status"] == "processed"
    _analyze(c, 2)
    refund = {"event": "refund.processed", "payload": {
        "refund": {"entity": {"id": "rfnd_1", "amount": 9900, "currency": "INR", "payment_id": "pay_R1"}},
        "payment": {"entity": pay}}}
    assert _rzp_signed(c, refund, "evt_r").json()["status"] == "processed"
    me = c.get("/api/billing/me").json()
    assert me["current"] is None
    assert me["payments"][0]["status"] == "refunded" and me["payments"][0]["refund_reference"] == "rfnd_1"
    assert me["subscriptions"][0]["status"] == "refunded"
    assert me["subscriptions"][0]["screenshots_used"] == 2  # usage history kept
    _rzp_signed(c, {"event": "order.paid", "payload": {"payment": {"entity": pay}}}, "evt_late")
    assert c.get("/api/billing/me").json()["current"] is None


# ---------------------------------------------------------------- email rule


def test_every_table_with_user_id_has_user_email():
    """Checked against the ACTUAL database schema, not only the ORM models."""
    init_db()
    assert tables_missing_user_email() == []
    for table in Base.metadata.tables.values():
        if "user_id" in table.c:
            assert "user_email" in table.c, table.name


def test_new_rows_get_email_snapshot_and_keep_it_after_email_change():
    c = _client("snap@example.com")
    _buy(c, "1-day")
    u = _user("snap@example.com")
    with SessionLocal() as db:
        db.add(ActivityLog(kind="question_analyzed", user_id=u.id))
        db.add(PasswordResetToken(user_id=u.id, token_hash="h" * 64, expires_at=subs.utcnow()))
        db.commit()
        db.get(User, u.id).email = "renamed@example.com"
        db.commit()
        assert db.scalar(select(ActivityLog.user_email).where(ActivityLog.user_id == u.id)) == "snap@example.com"
        assert db.scalar(select(PasswordResetToken.user_email)) == "snap@example.com"
        assert db.scalar(select(Payment.user_email)) == "snap@example.com"
        assert db.scalar(select(UserSubscription.user_email)) == "snap@example.com"
        audit = db.scalar(select(SubscriptionAuditLog).where(SubscriptionAuditLog.action == "purchase"))
        assert audit.user_email == "snap@example.com"


def test_migration_adds_and_backfills_user_email_on_legacy_tables():
    _client("legacy@example.com")
    uid = _user("legacy@example.com").id
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE legacy_clicks (id INTEGER PRIMARY KEY, user_id INTEGER)"))
        conn.execute(text("INSERT INTO legacy_clicks (id, user_id) VALUES (1, :u), (2, 999999), (3, NULL)"), {"u": uid})
    try:
        init_db()
        cols = {c["name"] for c in sa_inspect(engine).get_columns("legacy_clicks")}
        assert "user_email" in cols
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT user_id, user_email FROM legacy_clicks ORDER BY id")).all()
        # Resolved from users; orphan and anonymous rows are kept, never invented.
        assert [r[1] for r in rows] == ["legacy@example.com", "", ""]
        assert len(rows) == 3
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE legacy_clicks"))


# ---------------------------------------------------------------- migration


def test_one_time_catalogue_migration_from_legacy_plans():
    with SessionLocal() as db:
        for model in (PlanPrice, SubscriptionAuditLog, SchemaMigration):
            db.query(model).delete()
        db.query(SubscriptionPlan).delete()
        db.commit()
        legacy = SubscriptionPlan(code="3-days", name="3 Days", price_minor=15000, duration_days=3,
                                  currency="INR", badge="Most popular")
        custom = SubscriptionPlan(code="team", name="Team", price_minor=500000, duration_days=10,
                                  currency="INR", is_active=False)
        db.add_all([legacy, custom])
        db.commit()
        db.add(User(email="pre@example.com", name="Pre"))
        db.commit()
        uid = db.scalar(select(User.id).where(User.email == "pre@example.com"))
        now = subs.utcnow()
        db.add(UserSubscription(user_id=uid, user_email="pre@example.com", plan_id=legacy.id,
                                plan_code="3-days", plan_name="3 Days", source="payment", status="active",
                                starts_at=now, expires_at=now + timedelta(days=3)))
        db.commit()
        legacy_id = legacy.id
        assert pricing_service.ensure_catalogue(db) == 4  # 1-day, 7-days, 1-month, 3-months
        plan = db.get(SubscriptionPlan, legacy_id)
        assert (plan.name, plan.price_minor, plan.duration_hours, plan.screenshot_allowance, plan.badge) == (
            "3 Days Pass", 19900, 72, 24, "")
        old_sub = db.scalar(select(UserSubscription).where(UserSubscription.user_id == uid))
        assert old_sub.screenshot_allowance == 24 and old_sub.plan_name == "3 Days"  # snapshot kept
        team = db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == "team"))
        assert (team.is_active, team.duration_hours, team.screenshot_allowance) == (False, 240, 80)
        # Runs once: an admin edit afterwards is never overwritten.
        plan.name = "Admin name"
        db.commit()
        assert pricing_service.ensure_catalogue(db) == 0
        db.refresh(plan)
        assert plan.name == "Admin name"


# ---------------------------------------------------------------- admin


ADMIN_GETS = ["/api/admin/currencies", "/api/admin/screenshot-usage", "/api/admin/billing/overview",
              "/api/admin/plans/1/prices", "/api/admin/payments/1"]


def test_admin_endpoints_reject_ordinary_users():
    c = _client("plain@example.com")
    for path in ADMIN_GETS:
        assert c.get(path).status_code == 403, path
    h = csrf_headers(c)
    assert c.put("/api/admin/plans/1/prices", json={"currency": "INR", "amount_minor": 1}, headers=h).status_code == 403
    assert c.patch("/api/admin/currencies/USD", json={"payments_enabled": True}, headers=h).status_code == 403
    assert c.post("/api/admin/subscriptions/1/credits", json={"allowance": 999, "reason": "hack"},
                  headers=h).status_code == 403
    assert c.post("/api/admin/subscriptions/1/cancel", json={"reason": "hack"}, headers=h).status_code == 403
    anon = TestClient(app)
    assert anon.get("/api/admin/billing/overview").status_code == 401


def test_admin_credit_adjustment_cancel_and_audit(gemini):
    c = _client("adj@example.com")
    _buy(c, "1-day")
    _analyze(c, 5)
    sub_id = c.get("/api/billing/me").json()["current"]["id"]
    admin = _admin()
    h = csrf_headers(admin)
    low = admin.post(f"/api/admin/subscriptions/{sub_id}/credits", json={"allowance": 4, "reason": "typo"}, headers=h)
    assert low.status_code == 400  # below the 5 already used
    assert admin.post(f"/api/admin/subscriptions/{sub_id}/credits", json={"allowance": 2, "reason": ""},
                      headers=h).status_code == 422  # reason required
    ok = admin.post(f"/api/admin/subscriptions/{sub_id}/credits", json={"allowance": 10, "reason": "goodwill"},
                    headers=h)
    assert ok.json()["screenshots_remaining"] == 5
    assert admin.post(f"/api/admin/subscriptions/{sub_id}/cancel", json={"reason": "customer asked"},
                      headers=h).json()["status"] == "cancelled"
    # Terminal: cannot be extended or revoked afterwards.
    assert admin.post(f"/api/admin/subscriptions/{sub_id}/extend", json={"days": 1, "reason": "x" * 3},
                      headers=h).status_code == 400
    assert _analyze(c).status_code == 402
    audit = admin.get("/api/admin/subscription-audit", params={"user_id": _user("adj@example.com").id}).json()
    actions = [a["action"] for a in audit]
    assert "credits_adjust" in actions and "cancel" in actions
    adj = next(a for a in audit if a["action"] == "credits_adjust")
    assert adj["details"]["allowance_before"] == 8 and adj["details"]["allowance_after"] == 10
    assert adj["user_email"] == "adj@example.com" and adj["actor_user_id"] is not None


def test_manual_grant_is_not_a_paid_transaction():
    _client("gift@example.com")
    admin = _admin()
    uid = _user("gift@example.com").id
    r = admin.post(f"/api/admin/users/{uid}/subscriptions",
                   json={"hours": 24, "screenshot_credits": 8, "reason": "support gift"}, headers=csrf_headers(admin))
    body = r.json()
    assert (body["source"], body["amount_minor"], body["payment_id"], body["screenshot_allowance"]) == (
        "manual", 0, None, 8)
    overview = admin.get("/api/admin/billing/overview").json()
    assert overview["revenue_by_currency"] == []


def test_overview_keeps_currencies_separate_and_filters_work(gemini):
    admin = _admin()
    admin.put(f"/api/admin/plans/{_plan('1-day').id}/prices", json={"currency": "USD", "amount_minor": 199},
              headers=csrf_headers(admin))
    admin.patch("/api/admin/currencies/USD", json={"payments_enabled": True}, headers=csrf_headers(admin))
    a, b = _client("inr@example.com"), _client("usd2@example.com")
    _buy(a, "1-day")
    _buy(b, "1-day", "USD")
    _analyze(a, 3)
    ov = admin.get("/api/admin/billing/overview").json()
    assert {(x["currency"], x["amount_minor"]) for x in ov["revenue_by_currency"]} == {("INR", 9900), ("USD", 199)}
    assert ov["active_subscriptions"] == 2 and ov["screenshots_analyzed"] == 3
    assert ov["payments_by_status"]["paid"] == 2
    assert admin.get("/api/admin/payments", params={"currency": "USD"}).json()["total"] == 1
    assert admin.get("/api/admin/subscriptions", params={"currency": "USD"}).json()["total"] == 1
    assert admin.get("/api/admin/subscriptions", params={"q": "1 Day"}).json()["total"] == 2
    usage = admin.get("/api/admin/screenshot-usage", params={"q": "inr@example.com"}).json()
    assert usage["total"] == 1 and usage["items"][0]["credits_consumed"] == 3
    assert usage["items"][0]["plan_name"] == "1 Day Pass"
    sub_id = a.get("/api/billing/me").json()["current"]["id"]
    assert admin.get("/api/admin/subscriptions", params={"q": f"#{sub_id}"}).json()["total"] == 1
    future = (subs.utcnow() + timedelta(days=5)).date().isoformat()
    assert admin.get("/api/admin/billing/overview", params={"date_from": future}).json()["payments_by_status"] == {}
