"""Regression tests for the security-hardening batch.

Covers: CSRF on every mutating resource route, sign-out-everywhere on password
reset, per-account login throttling, trusted-proxy client IP, CSV formula
injection, WebSocket origin check on cookie auth, the production session
secret, upload size streaming and the activity-log composite index.
"""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.main import app
from app.models.user import User
from tests.conftest import csrf_headers, login, register

RESUME_TEXT = "Jane Doe\nSkills: Python, FastAPI, SQL\nExperience: Engineer at Acme 2020-2024\n" * 3


def _promote_to_admin(email: str) -> None:
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        u.is_admin = True
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------------ CSRF


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/api/interview/prepare", {"resumeText": "", "jobDescription": ""}),
        ("post", "/api/interview/analyze", {"question": "Explain REST."}),
        ("post", "/api/resumes/from-text", {"raw_text": RESUME_TEXT}),
        ("post", "/api/roles", {"position": "Engineer", "job_description": "Build APIs."}),
    ],
)
def test_mutating_routes_require_csrf(client, method, path, body):
    register(client, "csrf@example.com")
    denied = getattr(client, method)(path, json=body)
    assert denied.status_code == 403
    assert "CSRF" in denied.json()["detail"]
    allowed = getattr(client, method)(path, json=body, headers=csrf_headers(client))
    assert allowed.status_code in (200, 201)


def test_resume_upload_requires_csrf(client):
    register(client, "upload-csrf@example.com")
    files = {"file": ("cv.txt", RESUME_TEXT.encode(), "text/plain")}
    assert client.post("/api/resume/upload", files=files).status_code == 403
    files = {"file": ("cv.txt", RESUME_TEXT.encode(), "text/plain")}
    ok = client.post("/api/resume/upload", files=files, headers=csrf_headers(client))
    assert ok.status_code == 200


def test_resume_patch_and_delete_require_csrf(client):
    register(client, "rd@example.com")
    rid = client.post(
        "/api/resumes/from-text", json={"raw_text": RESUME_TEXT}, headers=csrf_headers(client)
    ).json()["id"]
    assert client.patch(f"/api/resumes/{rid}", json={"is_active": True}).status_code == 403
    assert client.delete(f"/api/resumes/{rid}").status_code == 403
    assert client.delete(f"/api/resumes/{rid}", headers=csrf_headers(client)).status_code == 200


def test_admin_patch_requires_csrf():
    admin = TestClient(app)
    register(admin, "boss@example.com")
    _promote_to_admin("boss@example.com")
    other = TestClient(app)
    uid = int(register(other, "member@example.com").json()["user"]["id"])
    r = admin.patch(f"/api/admin/users/{uid}", json={"is_active": False})
    assert r.status_code == 403
    r = admin.patch(
        f"/api/admin/users/{uid}", json={"is_active": False}, headers=csrf_headers(admin)
    )
    assert r.status_code == 200 and r.json()["is_active"] is False


# ------------------------------------------------- sign out everywhere


def test_password_reset_revokes_existing_sessions():
    from app.auth.service import create_reset_token, get_user_by_email

    victim = TestClient(app)
    register(victim, "victim@example.com")
    stolen = TestClient(app)
    stolen.cookies.set("aia_session", victim.cookies.get("aia_session"))
    assert stolen.get("/api/auth/me").json()["authenticated"] is True

    db = SessionLocal()
    try:
        user = get_user_by_email(db, "victim@example.com")
        # Backdate the revocation boundary check: sessions minted in the same
        # second are accepted by design, so make the old session older.
        raw = create_reset_token(db, user)
    finally:
        db.close()

    import time

    time.sleep(1.1)
    r = victim.post(
        "/api/auth/reset-password", json={"token": raw, "password": "BrandNew123"}
    )
    assert r.status_code == 200
    # The copied cookie no longer authenticates.
    assert stolen.get("/api/auth/me").json()["authenticated"] is False
    # A fresh login with the new password works.
    fresh = TestClient(app)
    assert login(fresh, "victim@example.com", "BrandNew123").status_code == 200
    assert fresh.get("/api/auth/me").json()["authenticated"] is True


# ------------------------------------------------------- rate limiting


def test_login_throttled_per_account_across_ips(client, monkeypatch):
    register(client, "target@example.com")
    monkeypatch.setattr(get_settings(), "rate_limit_per_minute", 3)
    codes = []
    for i in range(5):
        r = client.post(
            "/api/auth/login",
            json={"email": "target@example.com", "password": "wrong-pass1"},
            # A different spoofed address every attempt.
            headers={"X-Forwarded-For": f"10.0.0.{i}"},
        )
        codes.append(r.status_code)
    assert codes[:3] == [401, 401, 401]
    assert 429 in codes[3:]


def test_client_ip_respects_trusted_proxy_count(monkeypatch):
    from starlette.requests import Request

    from app.core.ratelimit import _client_ip

    def req(xff: str) -> Request:
        scope = {
            "type": "http",
            "headers": [(b"x-forwarded-for", xff.encode())],
            "client": ("203.0.113.9", 1234),
        }
        return Request(scope)

    settings = get_settings()
    monkeypatch.setattr(settings, "trusted_proxy_count", 0)
    assert _client_ip(req("6.6.6.6, 1.2.3.4")) == "6.6.6.6"  # legacy behaviour
    monkeypatch.setattr(settings, "trusted_proxy_count", 1)
    assert _client_ip(req("6.6.6.6, 1.2.3.4")) == "1.2.3.4"  # spoofed entry ignored
    monkeypatch.setattr(settings, "trusted_proxy_count", 2)
    assert _client_ip(req("6.6.6.6, 1.2.3.4, 5.6.7.8")) == "1.2.3.4"


def test_analyze_rate_limited_per_user(client, monkeypatch):
    register(client, "spender@example.com")
    monkeypatch.setattr(get_settings(), "ai_rate_limit_per_minute", 2)
    codes = [
        client.post(
            "/api/interview/analyze", json={"question": "Explain REST."}, headers=csrf_headers(client)
        ).status_code
        for _ in range(3)
    ]
    assert codes == [200, 200, 429]


def test_analyze_rejects_oversized_question(client):
    register(client, "big@example.com")
    r = client.post(
        "/api/interview/analyze", json={"question": "x" * 5000}, headers=csrf_headers(client)
    )
    assert r.status_code == 422


# ---------------------------------------------------------------- uploads


def test_upload_too_large_rejected(client, monkeypatch):
    register(client, "huge@example.com")
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    blob = b"a" * (1024 * 1024 + 10)
    r = client.post(
        "/api/resume/upload",
        files={"file": ("cv.txt", blob, "text/plain")},
        headers=csrf_headers(client),
    )
    assert r.status_code == 400
    assert "too large" in r.json()["detail"].lower()


def test_resume_list_does_not_load_file_blob(client):
    from sqlalchemy import inspect as sa_inspect, select

    from app.models.resume_record import ResumeRecord

    register(client, "lazy@example.com")
    client.post("/api/resumes/from-text", json={"raw_text": RESUME_TEXT}, headers=csrf_headers(client))
    db = SessionLocal()
    try:
        rec = db.scalars(select(ResumeRecord)).first()
        assert "file_data" in sa_inspect(rec).unloaded
        assert rec.file_data  # still loads on demand
    finally:
        db.close()


# -------------------------------------------------------------- admin CSV


def test_csv_export_neutralises_formulas():
    admin = TestClient(app)
    register(admin, "csvadmin@example.com")
    _promote_to_admin("csvadmin@example.com")
    evil = TestClient(app)
    register(evil, "evil@example.com", name='=HYPERLINK("http://evil.test","x")')
    body = admin.get("/api/admin/users/export").text
    assert "'=HYPERLINK" in body
    assert ',=HYPERLINK' not in body


# -------------------------------------------------------------- WebSocket


def test_ws_cookie_auth_rejects_foreign_origin(client):
    register(client, "ws-origin@example.com")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/ws/interview", headers={"origin": "https://evil.example"}
        ) as ws:
            ws.receive_json()


def test_ws_cookie_auth_allows_configured_origin(client):
    register(client, "ws-ok@example.com")
    origin = get_settings().cors_origins[0]
    with client.websocket_connect("/ws/interview", headers={"origin": origin}) as ws:
        assert ws.receive_json()["type"] == "status"


# --------------------------------------------------------- session secret


def test_production_without_secret_never_uses_public_fallback(monkeypatch):
    from app.auth import security

    settings = get_settings()
    monkeypatch.setattr(settings, "session_secret", "")
    monkeypatch.setattr(type(settings), "is_production", property(lambda self: True))
    assert security._secret() != security._DEV_FALLBACK_SECRET
    assert len(security._secret()) >= 32


def test_csrf_bootstrap_uses_session_cookie_attributes(client):
    register(client, "boot@example.com")
    r = client.get("/api/auth/csrf")
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "aia_csrf=" in cookie and "httponly" not in cookie


# ------------------------------------------------------------- DB indexes


def test_activity_log_has_kind_created_at_index():
    from sqlalchemy import inspect as sa_inspect

    from app.db.session import _ensure_activity_indexes, engine

    _ensure_activity_indexes()  # idempotent on an existing table
    names = {ix["name"] for ix in sa_inspect(engine).get_indexes("activity_log")}
    assert "ix_activity_log_kind_created_at" in names
