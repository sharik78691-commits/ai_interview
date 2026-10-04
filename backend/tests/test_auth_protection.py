"""Protected API access, cross-user isolation and WebSocket auth tests."""
from tests.conftest import csrf_headers, register

RESUME = "Jane Doe, Senior Python developer with 5 years of FastAPI and AWS work. " * 4
JOB = "We need a backend engineer skilled in Python, FastAPI, AWS and SQL. " * 4


def test_health_is_public(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_prepare_requires_auth(client):
    r = client.post(
        "/api/interview/prepare",
        json={"resumeText": RESUME, "jobDescription": JOB},
    )
    assert r.status_code == 401


def test_context_requires_auth(client):
    assert client.get("/api/interview/context").status_code == 401


def test_analyze_requires_auth(client):
    r = client.post("/api/interview/analyze", json={"question": "What is REST?"})
    assert r.status_code == 401


def test_resume_upload_requires_auth(client):
    r = client.post(
        "/api/resume/upload",
        files={"file": ("resume.txt", b"hello world", "text/plain")},
    )
    assert r.status_code == 401


def test_authenticated_prepare_and_analyze(client):
    register(client, "a@example.com")
    r = client.post(
        "/api/interview/prepare",
        json={"resumeText": RESUME, "jobDescription": JOB},
    )
    assert r.status_code == 200
    r2 = client.post("/api/interview/analyze", json={"question": "Explain REST."})
    assert r2.status_code == 200
    assert r2.json()["answerPoints"]


def test_cross_user_context_isolation(client):
    """User B must never see user A's interview context."""
    # User A prepares context.
    register(client, "a@example.com")
    client.post(
        "/api/interview/prepare",
        json={"resumeText": RESUME, "jobDescription": JOB},
    )
    assert client.get("/api/interview/context").json()["hasContext"] is True

    # User B (fresh session) sees an empty context.
    client.cookies.clear()
    register(client, "b@example.com")
    ctx = client.get("/api/interview/context").json()
    assert ctx["hasContext"] is False
    assert ctx["resumeChars"] == 0
    assert ctx["jobChars"] == 0


def test_ws_rejects_unauthenticated(client):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/interview") as ws:
            ws.receive_json()


def test_ws_accepts_authenticated(client):
    register(client, "ws@example.com")
    with client.websocket_connect("/ws/interview") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "status"
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_ws_authenticated_analyze_flow(client):
    register(client, "ws2@example.com")
    client.post(
        "/api/interview/prepare",
        json={"resumeText": RESUME, "jobDescription": JOB},
    )
    with client.websocket_connect("/ws/interview") as ws:
        ws.receive_json()  # initial status
        ws.send_json({"type": "interviewer_question", "text": "What is a REST API?"})
        # Expect a transcript echo then AI guidance.
        seen = []
        for _ in range(3):
            seen.append(ws.receive_json()["type"])
            if "ai_guidance" in seen:
                break
        assert "ai_guidance" in seen


def test_security_headers_present(client):
    r = client.get("/api/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Security-Policy" in r.headers
    assert "Referrer-Policy" in r.headers
