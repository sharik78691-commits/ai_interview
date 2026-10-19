"""Admin panel rate limiting: per admin user, separate from the login limit.

Regression: every /api/admin/* call shared ONE per-IP bucket capped at
RATE_LIMIT_PER_MINUTE (10, the login brute-force limit). The admin page fires
5 requests per load, so a couple of reloads answered 429 everywhere.
"""
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.main import app
from app.models.user import User
from tests.conftest import login, register

# What the admin page loads on open (admin.component.ts ngOnInit).
ADMIN_PAGE_LOAD = [
    "/api/admin/stats",
    "/api/admin/business",
    "/api/admin/users",
    "/api/admin/ai-models",
    "/api/admin/stt-models",
]


def _admin_client(email: str) -> TestClient:
    c = TestClient(app)
    register(c, email)
    db = SessionLocal()
    try:
        db.query(User).filter(User.email == email).first().is_admin = True
        db.commit()
    finally:
        db.close()
    assert login(c, email).status_code == 200
    return c


@pytest.fixture
def production_login_limit(monkeypatch):
    """Use the real default login limit (tests otherwise run with 1000)."""
    monkeypatch.setattr(get_settings(), "rate_limit_per_minute", 10)


def test_admin_page_reloads_are_not_blocked_by_the_login_limit(production_login_limit):
    c = _admin_client("owner@example.com")
    # Four full page loads = 20 admin requests, double the old per-IP cap.
    for _ in range(4):
        for path in ADMIN_PAGE_LOAD:
            r = c.get(path)
            assert r.status_code == 200, (path, r.status_code, r.text[:120])


def test_admin_limit_is_per_admin_user_and_sends_retry_after(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_rate_limit_per_minute", 3)
    first = _admin_client("a1@example.com")
    second = _admin_client("a2@example.com")  # same test IP

    for _ in range(3):
        assert first.get("/api/admin/stats").status_code == 200
    blocked = first.get("/api/admin/stats")
    assert blocked.status_code == 429
    retry_after = int(blocked.headers["Retry-After"])
    assert 1 <= retry_after <= 60

    # Another admin on the same IP keeps their own budget.
    assert second.get("/api/admin/stats").status_code == 200


def test_admin_billing_routes_share_the_admin_user_budget(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_rate_limit_per_minute", 2)
    c = _admin_client("b1@example.com")
    assert c.get("/api/admin/stats").status_code == 200
    assert c.get("/api/admin/stt-models").status_code == 200
    # The budget is per admin user across every /admin route (dashboard + billing).
    assert c.get("/api/admin/stats").status_code == 429


def test_non_admin_gets_403_not_429_and_anonymous_gets_401(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_rate_limit_per_minute", 1)
    anon = TestClient(app)
    assert anon.get("/api/admin/stats").status_code == 401
    user = TestClient(app)
    register(user, "plain@example.com")
    for _ in range(3):
        assert user.get("/api/admin/stats").status_code == 403


def test_login_limit_still_protects_auth_endpoints(production_login_limit):
    c = TestClient(app)
    codes = [
        c.post("/api/auth/login", json={"email": "x@example.com", "password": "wrong-pass-1"}).status_code
        for _ in range(12)
    ]
    assert 429 in codes
    blocked = c.post("/api/auth/login", json={"email": "x@example.com", "password": "wrong-pass-1"})
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


def test_every_admin_route_is_guarded():
    """No /api/admin route may bypass the admin + rate-limit dependency."""
    from app.auth.dependencies import require_admin_rate_limited

    def deps(route):
        out, stack = set(), list(route.dependant.dependencies)
        while stack:
            d = stack.pop()
            out.add(d.call)
            stack.extend(d.dependencies)
        return out

    from app.api import admin, admin_billing

    admin_routes = [r for m in (admin, admin_billing) for r in m.router.routes]
    assert len(admin_routes) >= 20
    for r in admin_routes:
        assert require_admin_rate_limited in deps(r), r.path
