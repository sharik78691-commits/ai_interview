"""Authentication tests: register, login, logout, session, reset, rate limit."""
from tests.conftest import csrf_headers, login, register

EMAIL = "user@example.com"
PASSWORD = "Passw0rd123"


def test_register_creates_session(client):
    r = register(client, EMAIL, PASSWORD, "Jane")
    assert r.status_code == 201
    body = r.json()
    assert body["authenticated"] is True
    assert body["user"]["email"] == EMAIL
    assert body["user"]["provider"] == "local"
    # Session + CSRF cookies are set.
    assert client.cookies.get("aia_session")
    assert client.cookies.get("aia_csrf")


def test_register_never_returns_password_hash(client):
    r = register(client, EMAIL, PASSWORD)
    assert "password" not in r.text.lower()
    assert "hash" not in r.text.lower()


def test_duplicate_registration_rejected(client):
    register(client, EMAIL, PASSWORD)
    r = register(client, EMAIL, PASSWORD)
    assert r.status_code == 409


def test_register_weak_password_rejected(client):
    r = register(client, "weak@example.com", "short")
    assert r.status_code == 422


def test_register_invalid_email_rejected(client):
    r = register(client, "not-an-email", PASSWORD)
    assert r.status_code == 422


def test_login_success(client):
    register(client, EMAIL, PASSWORD)
    client.cookies.clear()
    r = login(client, EMAIL, PASSWORD)
    assert r.status_code == 200
    assert r.json()["authenticated"] is True


def test_login_invalid_password(client):
    register(client, EMAIL, PASSWORD)
    client.cookies.clear()
    r = login(client, EMAIL, "WrongPass123")
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid email or password."


def test_login_unknown_email_generic_error(client):
    r = login(client, "nobody@example.com", PASSWORD)
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid email or password."


def test_me_requires_session(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["authenticated"] is False


def test_me_returns_user_when_authenticated(client):
    register(client, EMAIL, PASSWORD, "Jane")
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    body = r.json()
    assert body["authenticated"] is True
    assert body["user"]["email"] == EMAIL


def test_logout_invalidates_session(client):
    register(client, EMAIL, PASSWORD)
    r = client.post("/api/auth/logout", headers=csrf_headers(client))
    assert r.status_code == 200
    # Session cookie cleared -> /me is unauthenticated.
    r2 = client.get("/api/auth/me")
    assert r2.json()["authenticated"] is False


def test_logout_requires_csrf(client):
    register(client, EMAIL, PASSWORD)
    r = client.post("/api/auth/logout")  # no CSRF header
    assert r.status_code == 403


def test_password_is_hashed_not_plaintext(client):
    from app.auth.service import get_user_by_email
    from app.db.session import SessionLocal

    register(client, EMAIL, PASSWORD)
    db = SessionLocal()
    try:
        user = get_user_by_email(db, EMAIL)
        assert user is not None
        assert user.password_hash is not None
        assert user.password_hash != PASSWORD
        assert user.password_hash.startswith("$argon2")
    finally:
        db.close()


def test_forgot_password_generic_response(client):
    # Unknown email -> same generic response (no enumeration).
    r = client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert r.status_code == 200
    assert "If an account exists" in r.json()["message"]


def test_reset_password_flow(client):
    register(client, EMAIL, PASSWORD)
    # Create a token directly (the email link is not returned by the API).
    from app.auth.service import create_reset_token, get_user_by_email
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        user = get_user_by_email(db, EMAIL)
        raw = create_reset_token(db, user)
    finally:
        db.close()

    r = client.post(
        "/api/auth/reset-password",
        json={"token": raw, "password": "NewPassw0rd1"},
    )
    assert r.status_code == 200

    # Old password no longer works; new one does.
    client.cookies.clear()
    assert login(client, EMAIL, PASSWORD).status_code == 401
    assert login(client, EMAIL, "NewPassw0rd1").status_code == 200


def test_reset_token_single_use(client):
    register(client, EMAIL, PASSWORD)
    from app.auth.service import create_reset_token, get_user_by_email
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        user = get_user_by_email(db, EMAIL)
        raw = create_reset_token(db, user)
    finally:
        db.close()

    assert client.post(
        "/api/auth/reset-password", json={"token": raw, "password": "NewPassw0rd1"}
    ).status_code == 200
    # Second use of the same token fails.
    assert client.post(
        "/api/auth/reset-password", json={"token": raw, "password": "Another123"}
    ).status_code == 400


def test_invalid_reset_token(client):
    r = client.post(
        "/api/auth/reset-password",
        json={"token": "not-a-real-token", "password": "NewPassw0rd1"},
    )
    assert r.status_code == 400


def test_expired_reset_token(client):
    register(client, EMAIL, PASSWORD)
    from datetime import datetime, timedelta, timezone

    from app.auth.service import _hash_reset_token, get_user_by_email
    from app.db.session import SessionLocal
    from app.models.user import PasswordResetToken

    db = SessionLocal()
    try:
        user = get_user_by_email(db, EMAIL)
        raw = "expired-token-value"
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=_hash_reset_token(raw),
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            )
        )
        db.commit()
    finally:
        db.close()

    r = client.post(
        "/api/auth/reset-password", json={"token": raw, "password": "NewPassw0rd1"}
    )
    assert r.status_code == 400


def test_rate_limit_login(client):
    from app.core.ratelimit import reset_rate_limits

    reset_rate_limits()
    # Default limit is overridden to 1000 in conftest; force a tiny limit here.
    from app.core import ratelimit

    original = ratelimit.get_settings().rate_limit_per_minute
    ratelimit.get_settings().rate_limit_per_minute = 3
    try:
        codes = [
            login(client, "nobody@example.com", PASSWORD).status_code for _ in range(5)
        ]
        assert 429 in codes
    finally:
        ratelimit.get_settings().rate_limit_per_minute = original
        reset_rate_limits()
