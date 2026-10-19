"""Desktop screenshot analysis: server-held Gemini key, auth, validation."""
import base64
import json

import httpx
import pytest

from app.core.config import get_settings
from app.services import screenshot_service as svc
from tests.conftest import authenticate, csrf_headers

JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 200).decode()
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 200).decode()
FAKE_KEY = "AIzaFAKEfakeFAKEfakeFAKEfake1234"

MODEL_JSON = {
    "questions": ["  What is a cache?  ", ""],
    "topic": "Caching",
    "seniority": "Mid",
    "directAnswer": "Fast storage.",
    "interviewAnswer": "A cache keeps hot data close.",
    "keyPoints": ["TTL", 3],
    "followUps": [{"question": "Eviction?", "suggestedResponse": "LRU"}, {"question": ""}],
    "code": {"explanation": "", "complexity": "", "edgeCases": [], "improvedAnswer": ""},
}


def _gemini_ok(text: str | None = None) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text or json.dumps(MODEL_JSON)}]}}]}


class _FakeClient:
    """Stands in for httpx.AsyncClient; records the outgoing request."""

    def __init__(self, response: httpx.Response | Exception):
        self.response = response
        self.calls: list[dict] = []

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    async def aclose(self):
        pass


@pytest.fixture
def server_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "gemini_api_key", FAKE_KEY)
    return FAKE_KEY


@pytest.fixture
def fake_gemini(monkeypatch):
    holder: dict = {}

    def install(response):
        fake = _FakeClient(response)
        holder["client"] = fake
        monkeypatch.setattr(svc.httpx, "AsyncClient", lambda **_: fake)
        return fake

    return install


def _analyze(client, images, headers=None):
    return client.post(
        "/api/screenshots/analyze",
        json={"images": images},
        headers=csrf_headers(client) if headers is None else headers,
    )


# ------------------------------------------------------------------ auth


def test_analyze_requires_login(client):
    res = client.post("/api/screenshots/analyze", json={"images": [{"mimeType": "image/jpeg", "data": JPEG}]})
    assert res.status_code == 401


def test_analyze_requires_csrf(client, server_key):
    authenticate(client)
    res = _analyze(client, [{"mimeType": "image/jpeg", "data": JPEG}], headers={})
    assert res.status_code == 403


def test_config_requires_login_and_never_exposes_key(client, server_key):
    assert client.get("/api/screenshots/config").status_code == 401
    authenticate(client)
    res = client.get("/api/screenshots/config")
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is True
    assert body["limits"] == {"maxImages": svc.MAX_IMAGES, "maxTotalBytes": svc.MAX_TOTAL_BYTES}
    assert FAKE_KEY not in res.text


def test_config_reports_not_configured_without_server_key(client):
    authenticate(client)
    assert client.get("/api/screenshots/config").json()["configured"] is False


# ------------------------------------------------------------------ success


def test_analyze_uses_server_key_in_header_and_normalizes(client, server_key, fake_gemini):
    authenticate(client)
    fake = fake_gemini(httpx.Response(200, json=_gemini_ok()))
    res = _analyze(
        client,
        [{"mimeType": "image/jpeg", "data": JPEG}, {"mimeType": "image/png", "data": PNG}],
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["result"]["questions"] == ["What is a cache?"]
    assert body["result"]["keyPoints"] == ["TTL"]
    assert body["result"]["followUps"] == [{"question": "Eviction?", "suggestedResponse": "LRU"}]
    assert body["result"]["code"] is None
    assert FAKE_KEY not in res.text

    call = fake.calls[0]
    assert call["headers"]["x-goog-api-key"] == FAKE_KEY
    assert FAKE_KEY not in call["url"]
    assert call["url"].endswith(f"/{get_settings().screenshot_model}:generateContent")
    inline = [p for p in call["json"]["contents"][0]["parts"] if "inline_data" in p]
    assert [p["inline_data"]["mime_type"] for p in inline] == ["image/jpeg", "image/png"]


def test_analyze_accepts_fenced_json(client, server_key, fake_gemini):
    authenticate(client)
    fake_gemini(httpx.Response(200, json=_gemini_ok("```json\n" + json.dumps(MODEL_JSON) + "\n```")))
    assert _analyze(client, [{"mimeType": "image/jpeg", "data": JPEG}]).json()["ok"] is True


# ------------------------------------------------------------------ errors


def _code(res) -> str:
    return res.json()["detail"]["code"]


def test_analyze_without_server_key_is_not_configured(client, fake_gemini):
    authenticate(client)
    fake = fake_gemini(httpx.Response(200, json=_gemini_ok()))
    res = _analyze(client, [{"mimeType": "image/jpeg", "data": JPEG}])
    assert res.status_code == 503
    assert _code(res) == "NOT_CONFIGURED"
    assert fake.calls == []


@pytest.mark.parametrize(
    "images, code",
    [
        ([], "VALIDATION"),
        ([{"mimeType": "image/gif", "data": JPEG}], "VALIDATION"),
        ([{"mimeType": "image/jpeg", "data": "not base64!!"}], "VALIDATION"),
        ([{"mimeType": "image/jpeg", "data": PNG}], "VALIDATION"),
    ],
)
def test_analyze_rejects_bad_images_before_calling_ai(client, server_key, fake_gemini, images, code):
    authenticate(client)
    fake = fake_gemini(httpx.Response(200, json=_gemini_ok()))
    res = _analyze(client, images)
    assert res.status_code == 422
    assert _code(res) == code
    assert fake.calls == []


def test_analyze_rejects_too_many_images(client, server_key):
    authenticate(client)
    res = _analyze(client, [{"mimeType": "image/jpeg", "data": JPEG}] * (svc.MAX_IMAGES + 1))
    assert res.status_code == 422


def test_analyze_rejects_oversize_batch(client, server_key, fake_gemini):
    authenticate(client)
    fake = fake_gemini(httpx.Response(200, json=_gemini_ok()))
    half = base64.b64encode(b"\xff\xd8\xff" + b"0" * (svc.MAX_TOTAL_BYTES // 2 + 10)).decode()
    res = _analyze(client, [{"mimeType": "image/jpeg", "data": half}] * 2)
    assert res.status_code == 413
    assert _code(res) == "TOO_LARGE"
    assert fake.calls == []


@pytest.mark.parametrize(
    "status, code, http",
    [
        (400, "PROVIDER_ERROR", 502),
        (401, "SERVER_KEY_INVALID", 503),
        (429, "RATE_LIMITED", 429),
        (500, "PROVIDER_ERROR", 502),
    ],
)
def test_provider_errors_map_to_codes_without_leaking_key(
    client, server_key, fake_gemini, status, code, http
):
    authenticate(client)
    fake_gemini(httpx.Response(status, text=f"API key not valid: {FAKE_KEY}"))
    res = _analyze(client, [{"mimeType": "image/jpeg", "data": JPEG}])
    assert res.status_code == http
    assert _code(res) == code
    assert FAKE_KEY not in res.text


def test_provider_timeout_and_block(client, server_key, fake_gemini):
    authenticate(client)
    fake_gemini(httpx.ReadTimeout("slow"))
    assert _code(_analyze(client, [{"mimeType": "image/jpeg", "data": JPEG}])) == "TIMEOUT"
    fake_gemini(httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}))
    assert _code(_analyze(client, [{"mimeType": "image/jpeg", "data": JPEG}])) == "BLOCKED"
    fake_gemini(httpx.Response(200, json=_gemini_ok("not json")))
    assert _code(_analyze(client, [{"mimeType": "image/jpeg", "data": JPEG}])) == "BAD_RESPONSE"


def test_analyze_is_rate_limited_per_user(client, server_key, fake_gemini):
    from app.api.screenshots import ANALYZE_PER_MINUTE

    authenticate(client)
    fake_gemini(httpx.Response(200, json=_gemini_ok()))
    for _ in range(ANALYZE_PER_MINUTE):
        assert _analyze(client, [{"mimeType": "image/jpeg", "data": JPEG}]).status_code == 200
    assert _analyze(client, [{"mimeType": "image/jpeg", "data": JPEG}]).status_code == 429


def test_redact_strips_google_keys():
    assert FAKE_KEY not in svc.redact(f"bad key {FAKE_KEY}")
