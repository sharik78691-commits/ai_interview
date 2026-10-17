"""Multi-LLM selection tests (mocked providers — no external API calls)."""
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.providers.llm import registry
from app.providers.llm.provider import (
    LLMQuotaError,
    MockLLMProvider,
    OpenAICompatibleLLMProvider,
    _extract_json,
)
from tests.conftest import authenticate, csrf_headers

client = TestClient(app)

GUIDANCE = {
    "question": "What is a closure?",
    "questionType": "technical",
    "answerPoints": ["Define scope capture.", "Give an example."],
    "star": None,
    "codeHint": None,
    "example": "Used it in a React codebase.",
    "keyTakeaways": ["Captures scope."],
    "followUpQuestions": ["When would you use one?"],
    "responseLength": "medium",
}


class _FakeResponse:
    """Minimal stub of httpx.Response for provider unit tests."""

    def __init__(self, status_code: int, payload: dict, headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload
        self.headers: dict = headers or {}
        self.request = None
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


def _client_for(response: _FakeResponse):
    """An async context manager mimicking httpx.AsyncClient posting once."""
    post = AsyncMock(return_value=response)

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return await post(*args, **kwargs)

    return _Client(), post


# ------------------------------------------------------------ registry

def test_catalog_has_four_models_with_groq_default() -> None:
    models = registry.list_models()
    keys = {m["key"] for m in models}
    assert {"groq-gpt-oss", "gemini-flash", "qwen-coder", "openrouter-free"} <= keys
    assert registry.default_model_key() == "groq-gpt-oss"


def test_list_models_exposes_no_secrets() -> None:
    for m in registry.list_models():
        assert "api_key" not in m
        assert "apiKey" not in m
        blob = json.dumps(m).lower()
        assert "bearer" not in blob
        assert "sk-" not in blob


def test_validate_model_key_defaults_and_rejects_unknown() -> None:
    assert registry.validate_model_key(None) == registry.default_model_key()
    assert registry.validate_model_key("  ") == registry.default_model_key()
    assert registry.validate_model_key("gemini-flash") == "gemini-flash"
    with pytest.raises(ValueError):
        registry.validate_model_key("gpt-4-hacker")


def test_unknown_model_falls_back_to_default_provider() -> None:
    provider = registry.get_llm_provider("no-such-model")
    assert isinstance(provider, (OpenAICompatibleLLMProvider, MockLLMProvider))


def test_model_without_key_returns_mock() -> None:
    spec = registry.get_model("gemini-flash")
    if spec.api_key:
        pytest.skip("GEMINI_API_KEY configured — mock fallback not applicable")
    provider = registry.get_llm_provider("gemini-flash")
    assert isinstance(provider, MockLLMProvider)


# ------------------------------------------------------------ normalization

def test_extract_json_tolerates_fences_and_preamble() -> None:
    fenced = "```json\n" + json.dumps(GUIDANCE) + "\n```"
    assert _extract_json(fenced)["question"] == GUIDANCE["question"]
    preamble = "Here is my reasoning... " + json.dumps(GUIDANCE)
    assert _extract_json(preamble)["questionType"] == "technical"


def test_extract_json_repairs_comma_slips() -> None:
    """Nested structures (comparison table) trip reasoning models into missing
    or trailing commas — the parser must repair, not discard."""
    # Missing comma between the headers array and the next key.
    bad = (
        '{"question": "q", "answerPoints": ["a"], "comparison": '
        '{"headers": ["Aspect", "X", "Y"] "rows": [["d", "1", "2"]]}}'
    )
    data = _extract_json(bad)
    assert data["comparison"]["headers"] == ["Aspect", "X", "Y"]

    # Trailing comma before a closing brace.
    bad2 = '{"question": "q", "answerPoints": ["a"],}'
    assert _extract_json(bad2)["question"] == "q"


def test_error_categories() -> None:
    P = OpenAICompatibleLLMProvider
    assert P._categorize_status(429) == "rate_limit"
    assert P._categorize_status(401) == "auth"
    assert P._categorize_status(404) == "not_found"
    assert P._categorize_status(408) == "timeout"
    assert P._categorize_status(500) == "server"
    assert P._categorize_status(400, "insufficient_quota") == "quota"


# ------------------------------------------------------------ provider HTTP (mocked)

async def test_openai_compatible_provider_returns_guidance() -> None:
    provider = OpenAICompatibleLLMProvider(
        api_key="test-key", base_url="https://example.test/v1", model="test-model"
    )
    ok = _FakeResponse(
        200, {"choices": [{"message": {"content": json.dumps(GUIDANCE)}}], "usage": {}}
    )
    fake, _ = _client_for(ok)
    with patch("app.providers.llm.provider.shared_http", return_value=fake):
        resp = await provider.analyze_question("r", "j", "What is a closure?")
    assert resp.question == "What is a closure?"
    assert provider.last_error_category == "ok"


async def test_rate_limit_raises_quota_error() -> None:
    provider = OpenAICompatibleLLMProvider(
        api_key="test-key", base_url="https://example.test/v1", model="test-model"
    )
    provider.max_retries = 0
    limited = _FakeResponse(429, {"error": {"message": "slow down"}})
    fake, _ = _client_for(limited)
    with patch("app.providers.llm.provider.shared_http", return_value=fake):
        with pytest.raises(LLMQuotaError, match="rate limit"):
            await provider.analyze_question("r", "j", "What is REST?")
    assert provider.last_error_category == "rate_limit"


async def test_invalid_key_maps_to_auth() -> None:
    provider = OpenAICompatibleLLMProvider(
        api_key="bad", base_url="https://example.test/v1", model="test-model"
    )
    provider.max_retries = 0
    denied = _FakeResponse(401, {"error": {"message": "bad key"}})
    fake, _ = _client_for(denied)
    with patch("app.providers.llm.provider.shared_http", return_value=fake):
        with pytest.raises(LLMQuotaError, match="authentication"):
            await provider.analyze_question("r", "j", "What is REST?")
    assert provider.last_error_category == "auth"


# ------------------------------------------------------------ response handling

async def test_question_type_case_normalized() -> None:
    """Providers (esp. Gemini) may return "Technical" — must not discard it."""
    from app.models.ai_response import AIInterviewResponse

    assert AIInterviewResponse(
        question="q", questionType="Technical", answerPoints=["a"]
    ).questionType == "technical"
    assert AIInterviewResponse(
        question="q", questionType="CODING", answerPoints=["a"]
    ).questionType == "coding"


async def test_missing_question_echo_defaults_to_asked() -> None:
    provider = OpenAICompatibleLLMProvider(
        api_key="test-key", base_url="https://example.test/v1", model="test-model"
    )
    body = dict(GUIDANCE)
    del body["question"]
    fake, _ = _client_for(_FakeResponse(200, {"choices": [{"message": {"content": json.dumps(body)}}], "usage": {}}))
    with patch("app.providers.llm.provider.shared_http", return_value=fake):
        resp = await provider.analyze_question("r", "j", "What is a closure?")
    assert resp.question == "What is a closure?"


async def test_malformed_response_marks_bad_response() -> None:
    provider = OpenAICompatibleLLMProvider(
        api_key="test-key", base_url="https://example.test/v1", model="test-model"
    )
    fake, _ = _client_for(
        _FakeResponse(200, {"choices": [{"message": {"content": "not json at all"}}], "usage": {}})
    )
    with patch("app.providers.llm.provider.shared_http", return_value=fake):
        resp = await provider.analyze_question("r", "j", "What is REST?")
    assert resp.answerPoints  # mock fallback still answers
    assert provider.last_error_category == "bad_response"
    assert (provider.last_latency_ms or 0) > 0


def test_openrouter_headers_only_on_openrouter() -> None:
    or_provider = OpenAICompatibleLLMProvider(
        api_key="k", base_url="https://openrouter.ai/api/v1", model="m"
    )
    headers = or_provider._headers()
    assert headers["Authorization"] == "Bearer k"
    assert headers.get("X-Title") == "OyeInterview"
    assert "HTTP-Referer" in headers

    groq = OpenAICompatibleLLMProvider(
        api_key="k", base_url="https://api.groq.com/openai/v1", model="m"
    )
    assert set(groq._headers()) == {"Authorization"}


# ------------------------------------------------------------ API contracts
def test_ai_models_endpoint_shape() -> None:
    r = client.get("/api/ai/models")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["models"], list) and len(body["models"]) >= 4
    assert body["default"] == registry.default_model_key()
    for m in body["models"]:
        for field in ("key", "provider", "displayName", "available", "freeTier",
                      "capabilities", "isDefault"):
            assert field in m, field
    assert "sk-" not in json.dumps(body).lower()


def test_analyze_rejects_unknown_model() -> None:
    authenticate(client, "models1@example.com")
    r = client.post(
        "/api/interview/analyze",
        json={"question": "What is REST?", "model": "evil-model"},
        headers=csrf_headers(client),
    )
    assert r.status_code == 422


def test_analyze_accepts_known_model_key() -> None:
    authenticate(client, "models2@example.com")
    r = client.post(
        "/api/interview/analyze",
        json={"question": "What is REST?", "model": "groq-gpt-oss"},
        headers=csrf_headers(client),
    )
    # No key in CI -> mock fallback still returns valid guidance.
    assert r.status_code == 200
    assert r.json()["answerPoints"]


def test_admin_ai_models_requires_admin() -> None:
    authenticate(client, "models3@example.com")
    r = client.get("/api/admin/ai-models", headers=csrf_headers(client))
    assert r.status_code == 403


def test_overrides_cached_single_db_hit() -> None:
    """Two reads in a row cost one DB session (per-answer latency)."""
    from app.providers.llm import registry as reg

    reg.invalidate_model_overrides()
    with patch("app.db.session.SessionLocal") as sl:
        sl.return_value.query.return_value.all.return_value = []
        reg._load_overrides()
        reg._load_overrides()
        assert sl.call_count == 1


def test_overrides_invalidation_and_ttl_expiry() -> None:
    from app.providers.llm import registry as reg

    reg.invalidate_model_overrides()
    with patch("app.db.session.SessionLocal") as sl:
        sl.return_value.query.return_value.all.return_value = []
        reg._load_overrides()
        assert sl.call_count == 1
        # Admin write -> next read hits the DB immediately.
        reg.invalidate_model_overrides()
        reg._load_overrides()
        assert sl.call_count == 2
        # Stale TTL -> next read hits the DB again.
        reg._overrides_cached_at = 0.0
        reg._load_overrides()
        assert sl.call_count == 3


def test_short_token_cap() -> None:
    from app.providers.llm.provider import MAX_TOKENS

    assert MAX_TOKENS["short"] == 700
    assert MAX_TOKENS["medium"] == 2000
    assert MAX_TOKENS["long"] == 3000
