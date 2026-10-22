"""One-time free trial: 20 min web live, 15 min desktop live, 2 screenshots.

Policy lives in app/services/free_trial_service.py. Live minutes are metered
on the interview socket; the tick is shortened here so tests stay fast.
"""
import base64

import pytest
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from app.api import websocket as ws_module
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.billing import FreeTrialUsage
from app.models.user import User
from app.services import free_trial_service as free_trial
from app.services import screenshot_service as svc
from app.services import subscription_service as subs
from tests.conftest import csrf_headers, register

JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 200).decode()


def _user(email: str) -> User:
    with SessionLocal() as db:
        u = db.scalar(select(User).where(User.email == email))
        db.expunge(u)
        return u


def _set_used(email: str, **cols) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        row = free_trial.get_or_create(db, user)
        for k, v in cols.items():
            setattr(row, k, v)
        db.commit()


def _row(email: str) -> FreeTrialUsage:
    with SessionLocal() as db:
        row = db.get(FreeTrialUsage, _user(email).id)
        db.expunge(row)
        return row


def _grant_plan(email: str, credits: int = 10) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        subs.grant(db, user, user, plan_id=None, days=1, screenshot_credits=credits, reason="test")


@pytest.fixture
def gemini(monkeypatch):
    monkeypatch.setattr(get_settings(), "gemini_api_key", "AIzaFAKEfakeFAKEfakeFAKEfake1234")
    state = {"fail": None}

    async def fake_analyze(images, client=None):
        if state["fail"]:
            raise svc.ScreenshotAnalysisError(state["fail"], "boom", 502)
        return svc.normalize_result({"questions": ["Q"], "directAnswer": "A"})

    monkeypatch.setattr(svc, "analyze", fake_analyze)
    return state


def _analyze(client, n=1):
    return client.post("/api/screenshots/analyze",
                       json={"images": [{"mimeType": "image/jpeg", "data": JPEG}] * n},
                       headers=csrf_headers(client))


# ------------------------------------------------------------- summary


def test_new_user_gets_20_web_15_desktop_minutes_and_2_screenshots(client):
    register(client, "new@example.com")
    t = client.get("/api/billing/me/free-trial").json()
    assert t["web"] == {"limitSeconds": 1200, "remainingSeconds": 1200}
    assert t["desktop"] == {"limitSeconds": 900, "remainingSeconds": 900}
    assert t["screenshots"] == {"limit": 2, "remaining": 2}
    assert t["hasPlan"] is False and t["enforced"] is True


# ---------------------------------------------------------- screenshots


def test_two_free_screenshots_without_a_plan_then_plan_required(client, gemini):
    register(client, "shots@example.com")
    cfg = client.get("/api/screenshots/config").json()["usage"]
    assert (cfg["planName"], cfg["remaining"], cfg["totalRemaining"]) == ("Free trial", 2, 2)
    r = _analyze(client)
    assert r.status_code == 200 and r.json()["usage"]["remaining"] == 1
    assert _analyze(client).status_code == 200
    r = _analyze(client)
    assert r.status_code == 402 and r.json()["detail"]["code"] == "SUBSCRIPTION_REQUIRED"
    assert _row("shots@example.com").screenshots_used == 2


def test_non_admin_config_never_reports_unlimited_and_shows_the_trial_count(client, gemini):
    # Regression: the desktop Screenshots page showed "Unlimited" to normal users.
    register(client, "notadmin@example.com")
    usage = client.get("/api/screenshots/config").json()["usage"]
    assert usage["unlimited"] is False
    assert (usage["allowance"], usage["remaining"], usage["freeRemaining"]) == (2, 2, 2)
    assert _analyze(client).status_code == 200 and _analyze(client).status_code == 200
    usage = client.get("/api/screenshots/config").json()["usage"]
    assert usage["unlimited"] is False
    assert (usage["remaining"], usage["totalRemaining"]) == (0, 0)
    assert _analyze(client).status_code == 402


def test_request_bigger_than_free_credits_is_refused_without_charge(client, gemini):
    register(client, "big3@example.com")
    assert _analyze(client, 3).status_code == 402
    assert _row("big3@example.com").screenshots_used == 0


def test_failed_free_analysis_gives_the_credit_back(client, gemini):
    register(client, "freefail@example.com")
    gemini["fail"] = "PROVIDER_ERROR"
    assert _analyze(client).status_code == 502
    assert _row("freefail@example.com").screenshots_used == 0


def test_plan_credits_are_used_before_free_ones(client, gemini):
    register(client, "planfirst@example.com")
    _grant_plan("planfirst@example.com", credits=5)
    r = _analyze(client)
    assert r.status_code == 200
    assert _row("planfirst@example.com").screenshots_used == 0
    assert r.json()["usage"]["totalRemaining"] == 4 + 2


# ------------------------------------------------------------ live time


@pytest.fixture
def fast_tick(monkeypatch):
    monkeypatch.setattr(ws_module, "TRIAL_TICK_SECONDS", 0.05)
    return 0.05


def _handshake(ws) -> dict:
    msg = ws.receive_json()
    assert msg["type"] == "status"
    return msg["access"]


def test_handshake_reports_trial_minutes_per_client(client):
    register(client, "hand@example.com")
    with client.websocket_connect("/ws/interview") as ws:
        a = _handshake(ws)
        assert (a["mode"], a["client"], a["remainingSeconds"]) == ("trial", "web", 1200)
    with client.websocket_connect("/ws/interview?client=desktop") as ws:
        a = _handshake(ws)
        assert (a["mode"], a["client"], a["remainingSeconds"]) == ("trial", "desktop", 900)


def test_used_up_web_pool_is_refused_but_desktop_pool_still_works(client):
    register(client, "webdone@example.com")
    _set_used("webdone@example.com", web_seconds_used=1200)
    with client.websocket_connect("/ws/interview") as ws:
        frame = ws.receive_json()
        assert frame["type"] == "access" and frame["mode"] == "denied"
        assert frame["code"] == "TRIAL_EXHAUSTED"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == ws_module.TRIAL_EXHAUSTED_CLOSE_CODE
    with client.websocket_connect("/ws/interview?client=desktop") as ws:
        assert _handshake(ws)["mode"] == "trial"


def test_active_session_is_metered_and_closed_when_minutes_run_out(client, fast_tick):
    register(client, "meter@example.com")
    _set_used("meter@example.com", web_seconds_used=1200 - 1)  # 1 second left
    with client.websocket_connect("/ws/interview") as ws:
        assert _handshake(ws)["remainingSeconds"] == 1
        ws.send_json({"type": "interviewer_audio_start"})
        while True:
            frame = ws.receive_json()
            if frame["type"] == "access":
                break
        assert frame["mode"] == "denied"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == ws_module.TRIAL_EXHAUSTED_CLOSE_CODE
    assert _row("meter@example.com").web_seconds_used >= 1200
    assert _row("meter@example.com").desktop_seconds_used == 0


def test_idle_session_uses_no_minutes(client, fast_tick):
    import time

    register(client, "idle@example.com")
    with client.websocket_connect("/ws/interview") as ws:
        _handshake(ws)
        time.sleep(0.3)  # several ticks, no audio and no questions
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
    assert _row("idle@example.com").web_seconds_used == 0


def test_plan_holder_and_admin_are_unlimited(client):
    register(client, "payer@example.com")
    _set_used("payer@example.com", web_seconds_used=1200)
    _grant_plan("payer@example.com")
    with client.websocket_connect("/ws/interview") as ws:
        assert _handshake(ws)["mode"] == "unlimited"
    with SessionLocal() as db:
        db.scalar(select(User).where(User.email == "payer@example.com")).is_admin = True
        db.commit()
    with client.websocket_connect("/ws/interview") as ws:
        assert _handshake(ws)["mode"] == "unlimited"


def test_gate_can_be_switched_off(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "free_trial_enforced", False)
    register(client, "nogate@example.com")
    _set_used("nogate@example.com", web_seconds_used=1200)
    with client.websocket_connect("/ws/interview") as ws:
        assert _handshake(ws)["mode"] == "unlimited"
