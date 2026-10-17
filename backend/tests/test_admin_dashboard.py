"""Admin dashboard smoke tests (lean business shape)."""
from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app
from app.models.user import User
from tests.conftest import authenticate, csrf_headers, login, register

client = TestClient(app)


def _promote_to_admin(email: str) -> None:
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        u.is_admin = True
        db.commit()
    finally:
        db.close()


def test_admin_stats_lean_shape() -> None:
    register(client, "owner@example.com")
    _promote_to_admin("owner@example.com")
    login(client, "owner@example.com")

    r = client.get("/api/admin/stats", headers=csrf_headers(client))
    assert r.status_code == 200, r.text
    body = r.json()

    # New lean shape only.
    for key in ("system", "range", "totals", "in_range", "signups_by_day",
                "ai_usage", "recent_users"):
        assert key in body, key
    # Removed page-analytics sections must be gone.
    for gone in ("top_pages", "top_clicks", "page_times", "recent_logins",
                 "recent_visits", "live_feed", "in_range_extra", "all_time",
                 "visits_by_day", "logins_by_day", "visitors_by_day"):
        assert gone not in body, gone

    totals = body["totals"]
    for key in ("users_total", "active_users", "admin_count", "total_logins"):
        assert key in totals, key

    in_range = body["in_range"]
    for key in ("signups", "interviews", "questions", "ai_calls", "ai_errors"):
        assert key in in_range, key

    # Default window = last 30 days.
    assert body["range"]["from_date"] and body["range"]["to_date"]
    assert len(body["signups_by_day"]) == 30
    assert isinstance(body["ai_usage"], list)
    assert isinstance(body["recent_users"], list)


def test_admin_stats_honors_date_range() -> None:
    """A custom from/to window drives in_range and the series length."""
    register(client, "owner5@example.com")
    _promote_to_admin("owner5@example.com")
    login(client, "owner5@example.com")

    r = client.get(
        "/api/admin/stats",
        params={"from": "2026-01-01", "to": "2026-01-07"},
        headers=csrf_headers(client),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["range"]["from_date"] == "2026-01-01"
    assert body["range"]["to_date"] == "2026-01-07"
    assert len(body["signups_by_day"]) == 7


def test_admin_user_row_has_no_analytics_fields() -> None:
    register(client, "owner2@example.com")
    _promote_to_admin("owner2@example.com")
    login(client, "owner2@example.com")

    r = client.get("/api/admin/users", headers=csrf_headers(client))
    assert r.status_code == 200
    row = r.json()["users"][0]
    for gone in ("last_location", "resumes_count", "roles_count"):
        assert gone not in row, gone


def test_activity_ingestion_endpoints_removed() -> None:
    """Page-time / clicks ingestion was removed with the lean cut."""
    assert client.post("/api/activity/page-time", json={"path": "/", "seconds": 1}).status_code == 404
    assert client.post("/api/activity/clicks", json={"events": []}).status_code == 404


def test_activity_log_model_is_slim() -> None:
    from sqlalchemy import inspect

    from app.db.session import engine

    cols = {c["name"] for c in inspect(engine).get_columns("activity_log")}
    for gone in ("ip_hash", "country", "region", "city", "path", "method",
                 "label", "target", "seconds", "user_agent"):
        assert gone not in cols, gone
    for kept in ("kind", "user_id", "provider", "model", "latency_ms",
                 "error_category", "created_at"):
        assert kept in cols, kept


def test_ai_usage_summary_present_after_analyze() -> None:
    """One analyzed question -> one llm_usage row surfaced in ai_usage."""
    authenticate(client, "usage@example.com")
    client.post(
        "/api/interview/prepare",
        json={"resumeText": "Python dev", "jobDescription": "Backend role"},
    )
    client.post(
        "/api/interview/analyze",
        json={"question": "What is REST?", "model": "groq-gpt-oss"},
        headers=csrf_headers(client),
    )

    # Promote + login a different admin to read the dashboard.
    register(client, "owner3@example.com")
    _promote_to_admin("owner3@example.com")
    login(client, "owner3@example.com")

    r = client.get("/api/admin/stats", headers=csrf_headers(client))
    body = r.json()
    assert body["in_range"]["ai_calls"] >= 1
