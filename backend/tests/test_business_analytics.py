"""Owner-level business report: funnel, segments, KPIs, insights, endpoint."""
from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.services.business_analytics import EventRow, UserRow, build_report
from tests.conftest import csrf_headers, login, register
from tests.test_admin_dashboard import _promote_to_admin

D1 = date(2026, 10, 30)
D0 = D1 - timedelta(days=29)


def _at(day: date, hour: int = 10) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)


def _user(i: int, signed: date, admin: bool = False) -> UserRow:
    return UserRow(i, f"U{i}", f"u{i}@x.com", "google" if i % 2 else "local",
                   _at(signed), admin, True)


def _ev(kind: str, uid: int, day: date, **kw) -> EventRow:
    return EventRow(kind=kind, user_id=uid, created_at=_at(day), **kw)


def test_funnel_segments_and_exclusion_of_admins() -> None:
    d = D1 - timedelta(days=5)
    users = [_user(1, d), _user(2, d), _user(3, d), _user(4, d), _user(99, d, admin=True)]
    events = [
        # u1: power user, returns on a second day
        *[_ev("question_analyzed", 1, d) for _ in range(10)],
        _ev("interview_prepared", 1, d), _ev("user_login", 1, d + timedelta(days=1)),
        # u2: prepared but never asked
        _ev("interview_prepared", 2, d),
        # u3: casual, one question
        _ev("question_analyzed", 3, d),
        # admin activity must never count
        *[_ev("question_analyzed", 99, d) for _ in range(50)],
    ]
    r = build_report(users, events, D0, D1)
    f = {s["key"]: s["count"] for s in r["funnel"]}
    assert f == {"signed_up": 4, "prepared": 2, "asked": 2, "returned": 1}
    assert r["activation_rate_pct"] == 50.0
    seg = {s["key"]: s["count"] for s in r["segments"]}
    assert seg["power"] == 1 and seg["casual"] == 1
    assert seg["lapsed"] == 1 and seg["never_activated"] == 1
    assert r["kpis"]["questions"]["value"] == 11  # admin's 50 excluded
    assert r["totals"]["members"] == 4
    assert r["top_users"][0]["id"] == 1
    assert {p["id"] for p in r["never_started"]} == {2, 4}
    assert len(r["daily"]) == 30 and len(r["heatmap"]) == 7 and len(r["heatmap"][0]) == 24


def test_period_comparison_and_insights() -> None:
    prev = D0 - timedelta(days=3)
    users = [_user(i, prev) for i in range(1, 5)] + [_user(10, D1)]
    r = build_report(users, [], D0, D1)
    nu = r["kpis"]["new_users"]
    assert nu["value"] == 1 and nu["previous"] == 4 and nu["delta_pct"] == -75.0
    levels = {i["level"] for i in r["insights"]}
    assert levels <= {"action", "warn", "good"}
    assert any("down" in i["title"].lower() for i in r["insights"])


def test_ai_reliability_percentiles() -> None:
    d = D1 - timedelta(days=1)
    events = [
        _ev("llm_usage", 1, d, latency_ms=ms, error_category="ok", input_tokens=10, output_tokens=5)
        for ms in (1000, 2000, 3000, 4000)
    ] + [_ev("llm_usage", 1, d, latency_ms=None, error_category="rate_limit")]
    r = build_report([_user(1, d)], events, D0, D1)
    ai = r["ai"]
    assert ai["calls"] == 5 and ai["errors"] == 1 and ai["success_rate_pct"] == 80.0
    assert ai["p50_latency_ms"] in (2000, 3000) and ai["p95_latency_ms"] == 4000
    assert ai["input_tokens"] == 40


def test_empty_system_does_not_crash() -> None:
    r = build_report([], [], D0, D1)
    assert r["insights"] and r["insights"][0]["title"] == "No customers yet"


def test_business_endpoint_requires_admin_and_returns_shape() -> None:
    c = TestClient(app)
    assert c.get("/api/admin/business").status_code in (401, 403)
    register(c, "owner-biz@example.com")
    r = c.get("/api/admin/business", headers=csrf_headers(c))
    assert r.status_code in (401, 403)
    _promote_to_admin("owner-biz@example.com")
    login(c, "owner-biz@example.com")
    r = c.get("/api/admin/business", headers=csrf_headers(c))
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("kpis", "engagement", "funnel", "segments", "daily", "heatmap",
                "auth_mix", "ai", "top_users", "win_back", "never_started", "insights"):
        assert key in body, key


def test_ai_cost_report_per_model_windows_and_estimates() -> None:
    from datetime import datetime, timezone

    from app.services.ai_cost import build_cost_report

    now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    catalog = [
        {"key": "groq-gpt-oss", "displayName": "GPT-OSS", "provider": "Groq", "modelId": "m", "freeTier": True},
        {"key": "deepseek-flash", "displayName": "DS", "provider": "DeepSeek", "modelId": "d", "freeTier": False},
        {"key": "idle", "displayName": "Idle", "provider": "X", "modelId": "i", "freeTier": False},
    ]

    def ev(key, hours_ago, **kw):
        return EventRow(kind="llm_usage", user_id=1, created_at=now - timedelta(hours=hours_ago),
                        provider=key, model="m", error_category=kw.pop("err", "ok"), **kw)

    events = [
        # exactly 1M in / 1M out reported -> deepseek 0.14 + 0.28 USD
        ev("deepseek-flash", 2, input_tokens=1_000_000, output_tokens=1_000_000, latency_ms=1000),
        # no tokens reported -> estimated from the model's own average above
        ev("deepseek-flash", 3, latency_ms=2000),
        ev("deepseek-flash", 4, err="rate_limit"),  # failed: counted, free
        ev("groq-gpt-oss", 30, input_tokens=1000, output_tokens=1000, latency_ms=500),  # 7d, not 24h
        ev("groq-gpt-oss", 24 * 20, input_tokens=1000, output_tokens=1000),  # 30d only
    ]
    r = build_cost_report(events, catalog, now)
    d24 = {m["key"]: m for m in r["windows"]["24h"]["models"]}
    assert d24["deepseek-flash"]["calls"] == 3 and d24["deepseek-flash"]["errors"] == 1
    assert d24["deepseek-flash"]["estimated_calls"] == 1
    # reported call + estimated call (same avg tokens) = 2 x 0.42 USD
    assert abs(d24["deepseek-flash"]["cost_usd"] - 0.84) < 1e-6
    assert d24["groq-gpt-oss"]["calls"] == 0
    assert d24["idle"]["calls"] == 0  # catalog models always listed
    w7 = {m["key"]: m for m in r["windows"]["7d"]["models"]}
    w30 = {m["key"]: m for m in r["windows"]["30d"]["models"]}
    assert w7["groq-gpt-oss"]["calls"] == 1 and w30["groq-gpt-oss"]["calls"] == 2
    # free-tier model: list-price cost shown, billed is zero
    assert w7["groq-gpt-oss"]["cost_inr"] > 0 and w7["groq-gpt-oss"]["billed_inr"] == 0
    assert w7["deepseek-flash"]["billed_inr"] == w7["deepseek-flash"]["cost_inr"] > 0
    assert len(r["series"]["24h"]) == 24 and len(r["series"]["30d"]) == 30
    assert r["windows"]["24h"]["totals"]["estimated_share_pct"] == 50.0


def test_ai_costs_endpoint_admin_only_and_shape() -> None:
    c = TestClient(app)
    assert c.get("/api/admin/ai-costs").status_code in (401, 403)
    register(c, "owner-cost@example.com")
    assert c.get("/api/admin/ai-costs").status_code in (401, 403)
    _promote_to_admin("owner-cost@example.com")
    login(c, "owner-cost@example.com")
    r = c.get("/api/admin/ai-costs", headers=csrf_headers(c))
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("windows", "series", "pricing", "run_rate", "usd_to_inr"):
        assert k in body
    assert set(body["windows"]) == {"24h", "7d", "30d", "90d"}
