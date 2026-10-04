"""Google OAuth validation tests (no live Google calls)."""
import pytest

from app.auth import oauth
from app.auth.service import get_user_by_email, upsert_google_user
from app.db.session import SessionLocal



def test_google_login_unconfigured_returns_503(client):
    # Force Google off regardless of the developer's real backend/.env.
    from app.core.config import get_settings

    settings = get_settings()
    saved_id, saved_secret = settings.google_client_id, settings.google_client_secret
    settings.google_client_id = ""
    settings.google_client_secret = ""
    try:
        r = client.get("/api/auth/google/login", follow_redirects=False)
        assert r.status_code == 503
    finally:
        settings.google_client_id = saved_id
        settings.google_client_secret = saved_secret


def test_google_callback_state_mismatch_redirects_with_error(client):
    r = client.get(
        "/api/auth/google/callback?code=abc&state=xyz",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "error=google_state" in r.headers["location"]


def test_google_callback_missing_code(client):
    r = client.get("/api/auth/google/callback?state=xyz", follow_redirects=False)
    assert r.status_code == 302
    assert "error=google_invalid" in r.headers["location"]


def test_upsert_google_user_creates_google_only_account():
    db = SessionLocal()
    try:
        user = upsert_google_user(
            db,
            subject="google-sub-1",
            email="g@example.com",
            email_verified=True,
            name="G User",
        )
        assert user.auth_provider == "google"
        assert user.password_hash is None
        assert user.google_subject_id == "google-sub-1"
        assert user.email_verified is True
    finally:
        db.close()


def test_upsert_google_user_links_existing_local_account():
    """A local account with the same verified email is linked, not duplicated."""
    from app.auth.service import register_user

    db = SessionLocal()
    try:
        local = register_user(db, "link@example.com", "Passw0rd123", "Local")
        assert local.password_hash is not None

        linked = upsert_google_user(
            db,
            subject="google-sub-2",
            email="link@example.com",
            email_verified=True,
            name="Linked",
        )
        # Same row: identity linked, password preserved.
        assert linked.id == local.id
        assert linked.google_subject_id == "google-sub-2"
        assert linked.password_hash is not None
        # No duplicate account created.
        assert get_user_by_email(db, "link@example.com").id == local.id
    finally:
        db.close()


@pytest.mark.asyncio
async def test_exchange_code_unconfigured_raises():
    with pytest.raises(oauth.OAuthError):
        await oauth.exchange_code_for_identity("some-code")


def test_build_authorize_url_contains_state_and_client():
    from app.core.config import get_settings

    settings = get_settings()
    settings.google_client_id = "test-client-id"
    try:
        url = oauth.build_authorize_url("state-123")
        assert "state=state-123" in url
        assert "client_id=test-client-id" in url
        assert "accounts.google.com" in url
    finally:
        settings.google_client_id = ""
