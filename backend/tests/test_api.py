"""API integration tests."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

RESUME = "Jane Doe, Senior Python developer with 5 years of FastAPI and AWS work. " * 4
JOB = "We need a backend engineer skilled in Python, FastAPI, AWS and SQL. " * 4


def test_health() -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_prepare_ok() -> None:
    r = client.post(
        "/api/interview/prepare",
        json={"resumeText": RESUME, "jobDescription": JOB},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_prepare_fail() -> None:
    r = client.post(
        "/api/interview/prepare",
        json={"resumeText": "short", "jobDescription": "short"},
    )
    assert r.status_code == 422


def test_analyze_returns_guidance() -> None:
    client.post(
        "/api/interview/prepare",
        json={"resumeText": RESUME, "jobDescription": JOB},
    )
    r = client.post(
        "/api/interview/analyze",
        json={"question": "Can you explain REST API design?"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["answerPoints"]
    assert body["questionType"] in ("technical", "behavioral", "coding", "general")


def test_upload_txt() -> None:
    content = b"John Smith\n\nSKILLS\nPython, SQL\n\nEXPERIENCE\nBackend Engineer at X 2020-2023\n"
    r = client.post(
        "/api/resume/upload",
        files={"file": ("resume.txt", content, "text/plain")},
    )
    assert r.status_code == 200
    assert r.json()["chars"] > 0
