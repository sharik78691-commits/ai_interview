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

def test_catalog_has_core_models_with_gemini_flash_lite_default() -> None:
    models = registry.list_models()
    keys = {m["key"] for m in models}
    assert {"groq-gpt-oss", "gemini-flash", "qwen-coder", "openrouter-free"} <= keys
    # AI Guidance default: Gemini 3.5 Flash-Lite (fastest + steadiest measured).
    assert registry.default_model_key() == "gemini-flash"
    assert registry.get_model(None).key == "gemini-flash"


def test_default_uses_gemini_when_its_key_is_configured(monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "gemini_api_key", "g-key")
    monkeypatch.setattr(get_settings(), "groq_api_key", "groq-key")
    assert registry.default_model_key() == "gemini-flash"
    assert registry.get_llm_provider(None).model == get_settings().gemini_model


def test_default_falls_back_to_groq_without_a_gemini_key(monkeypatch) -> None:
    """A server without GEMINI_API_KEY must still answer for real (Groq),
    not silently with the mock provider."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "gemini_api_key", "")
    monkeypatch.setattr(get_settings(), "groq_api_key", "groq-key")
    assert registry.default_model_key() == "groq-gpt-oss"
    assert isinstance(registry.get_llm_provider(None), OpenAICompatibleLLMProvider)


def test_env_and_admin_default_still_win_over_built_in(monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "gemini_api_key", "g-key")
    monkeypatch.setenv("DEFAULT_LLM_MODEL", "groq-gpt-oss")
    assert registry.default_model_key() == "groq-gpt-oss"


def test_openai_model_uses_reasoning_params() -> None:
    """OpenAI GPT-6 Luna (reasoning) needs max_completion_tokens + no
    temperature; other providers keep max_tokens + temperature."""
    luna = registry.get_model("openai-gpt6-luna")
    assert luna.max_tokens_param == "max_completion_tokens"
    assert luna.supports_temperature is False

    groq = registry.get_model("groq-gpt-oss")
    assert groq.max_tokens_param == "max_tokens"
    assert groq.supports_temperature is True


def test_openai_provider_payload_omits_temperature() -> None:
    from app.providers.llm.provider import OpenAICompatibleLLMProvider

    p = OpenAICompatibleLLMProvider(
        api_key="k", base_url="https://api.openai.com/v1", model="gpt-6-luna",
        max_tokens_param="max_completion_tokens", supports_temperature=False,
    )
    body, _ = p._request_payload("medium", "r", "j", "q")
    assert "temperature" not in body
    assert p.max_tokens_param == "max_completion_tokens"


def test_deepseek_models_registered() -> None:
    """Both requested DeepSeek models sit on the official OpenAI-compatible
    endpoint and are selectable alongside the other providers."""
    from app.core.config import get_settings

    s = get_settings()
    flash = registry.get_model("deepseek-flash")
    pro = registry.get_model("deepseek-v4-pro")

    assert flash.provider == "DeepSeek"
    assert pro.provider == "DeepSeek"
    assert flash.base_url == pro.base_url == "https://api.deepseek.com/v1"
    # Env-driven ids (defaults documented in .env.example).
    assert flash.model_id == s.deepseek_model
    assert pro.model_id == s.deepseek_v4_pro_model
    # Plain OpenAI-compatible payload — no provider-specific knobs.
    assert flash.max_tokens_param == "max_tokens"
    assert flash.supports_temperature is True
    # Generation needs a topped-up balance, so never advertise free tier.
    assert flash.free_tier is False
    assert pro.free_tier is False

    listed = {m["key"]: m for m in registry.list_models()}
    assert {"deepseek-flash", "deepseek-v4-pro"} <= listed.keys()
    assert listed["deepseek-flash"]["provider"] == "DeepSeek"
    assert listed["deepseek-flash"]["displayName"] == "DeepSeek-V4.1 Flash — DeepSeek"
    assert listed["deepseek-v4-pro"]["displayName"] == "DeepSeek-V4 Pro — DeepSeek"
    assert listed["deepseek-flash"]["freeTier"] is False


def test_deepseek_default_model_ids_match_get_models() -> None:
    """GET https://api.deepseek.com/v1/models advertises exactly these ids; a
    wrong one only fails at call time, so pin the shipped defaults rather than
    trusting an edited local .env."""
    import os

    from app.core.config import Settings

    with patch.dict(os.environ):
        os.environ.pop("DEEPSEEK_MODEL", None)
        os.environ.pop("DEEPSEEK_V4_PRO_MODEL", None)
        fresh = Settings()
    assert fresh.deepseek_model == "deepseek-flash"
    assert fresh.deepseek_v4_pro_model == "deepseek-v4-pro"


def test_deepseek_effort_split_speed_vs_capability() -> None:
    """Flash is the speed tier -> ask for a short think (DeepSeek's own
    GET /models reports effort default_level "high", i.e. a slow default).
    Pro is the capability tier -> keeps DeepSeek's default rather than trading
    depth for speed. The field name is DeepSeek's own: an invalid value is
    rejected pre-billing with HTTP 422 listing `none, minimal, low, medium,
    high, xhigh, ultra, max`."""
    assert registry.get_model("deepseek-flash").reasoning_effort == "low"
    assert registry.get_model("deepseek-v4-pro").reasoning_effort is None

    p = OpenAICompatibleLLMProvider(
        api_key="k",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-flash",
        reasoning_effort=registry.get_model("deepseek-flash").reasoning_effort,
    )
    body, _ = p._request_payload("short", "r", "j", "q")
    assert body["reasoning_effort"] == "low"
    assert body["temperature"] == 0.4

    pro = OpenAICompatibleLLMProvider(
        api_key="k",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-v4-pro",
        reasoning_effort=registry.get_model("deepseek-v4-pro").reasoning_effort,
    )
    pro_body, _ = pro._request_payload("short", "r", "j", "q")
    assert "reasoning_effort" not in pro_body


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
    # DeepSeek phrases an empty balance as "Insufficient Balance" on HTTP 402;
    # it must land in the same quota bucket (admin dashboard + retry policy).
    assert P._categorize_status(402, '{"error":"Insufficient Balance"}') == "quota"
    assert P._categorize_status(402) == "quota"
    assert P._categorize_status(400, "insufficient balance") == "quota"
    assert P._categorize_status(422, '{"error":"unknown variant"}') == "http_422"


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
    """short's cap carries reasoning headroom (was 700 — measured to truncate:
    gemini-3.5-flash 0/3 valid JSON, gpt-oss finish_reason="length"). The other
    levels are unchanged. See tests/test_llm_model_latency.py for the evidence."""
    from app.providers.llm.provider import MAX_TOKENS

    assert MAX_TOKENS["short"] == 1400
    assert MAX_TOKENS["medium"] == 2000
    assert MAX_TOKENS["long"] == 3000


# ------------------------------------------------- Vercel AI Gateway · DeepSeek

def test_gateway_deepseek_v4_flash_registered_on_vercel_ai_gateway() -> None:
    from app.core.config import get_settings

    s = get_settings()
    spec = registry.get_model("gateway-deepseek-v4-flash")
    assert spec.base_url == s.ai_gateway_base_url == "https://ai-gateway.vercel.sh/v1"
    assert spec.model_id == s.gateway_deepseek_flash_model == "deepseek/deepseek-v4.1-flash-1017"
    assert spec.api_key == s.ai_gateway_api_key
    # Existing direct-DeepSeek models are untouched.
    assert registry.get_model("deepseek-flash").base_url == "https://api.deepseek.com/v1"


def test_gateway_model_unavailable_without_key_and_listed_without_secrets() -> None:
    row = next(m for m in registry.list_models() if m["key"] == "gateway-deepseek-v4-flash")
    assert row["available"] is False  # conftest blanks AI_GATEWAY_API_KEY
    assert "api_key" not in row and "apiKey" not in row
    assert isinstance(registry.get_llm_provider("gateway-deepseek-v4-flash"), MockLLMProvider)


def test_gateway_model_calls_gateway_with_bearer_key(monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ai_gateway_api_key", "vck_test")
    provider = registry.get_llm_provider("gateway-deepseek-v4-flash")
    assert isinstance(provider, OpenAICompatibleLLMProvider)
    assert provider.base_url == "https://ai-gateway.vercel.sh/v1"
    assert provider.model == "deepseek/deepseek-v4.1-flash-1017"
    assert provider._headers()["Authorization"] == "Bearer vck_test"


def test_gateway_card_required_is_reported_as_billing_not_bad_key() -> None:
    body = (
        '{"error":{"message":"AI Gateway requires a valid credit card on file to service '
        'requests.","type":"customer_verification_required"}}'
    )
    assert OpenAICompatibleLLMProvider._categorize_status(403, body) == "quota"
    msg = OpenAICompatibleLLMProvider._friendly_error(403, body, "deepseek/deepseek-v4.1-flash-1017")
    assert "payment card" in msg
    assert "LLM_API_KEY" not in msg
    # A genuinely bad key is still reported as an auth problem.
    assert OpenAICompatibleLLMProvider._categorize_status(401, '{"error":"invalid key"}') == "auth"


def test_gateway_deepseek_runs_with_reasoning_off() -> None:
    """Regression: at the default effort the gateway DeepSeek Flash spent ~20s
    thinking and spilled reasoning into the answer (finish_reason=length,
    invalid JSON). It must request reasoning_effort=none."""
    spec = registry.get_model("gateway-deepseek-v4-flash")
    assert spec.reasoning_effort == "none"
    p = OpenAICompatibleLLMProvider(
        api_key="vck_test", base_url=spec.base_url, model=spec.model_id,
        reasoning_effort=spec.reasoning_effort,
    )
    body, _ = p._request_payload("medium", "r", "j", "q")
    assert body["reasoning_effort"] == "none"
    assert body["model"] == "deepseek/deepseek-v4.1-flash-1017"


def test_gateway_gpt_oss_registered_with_low_reasoning(monkeypatch) -> None:
    """GPT-OSS 120B through Vercel AI Gateway: same gateway key/endpoint as
    the DeepSeek entry, short think (low) so the first bullet is fast."""
    from app.core.config import get_settings

    spec = registry.get_model("gateway-gpt-oss")
    assert spec.model_id == get_settings().gateway_gpt_oss_model == "openai/gpt-oss-120b"
    assert spec.base_url == "https://ai-gateway.vercel.sh/v1"
    assert spec.reasoning_effort == "low"
    # Unavailable without the gateway key (conftest blanks it), never leaks it.
    row = next(m for m in registry.list_models() if m["key"] == "gateway-gpt-oss")
    assert row["available"] is False and "api_key" not in row

    monkeypatch.setattr(get_settings(), "ai_gateway_api_key", "vck_test")
    provider = registry.get_llm_provider("gateway-gpt-oss")
    assert isinstance(provider, OpenAICompatibleLLMProvider)
    body, _ = provider._request_payload("medium", "r", "j", "q")
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["reasoning_effort"] == "low"
    assert provider._headers()["Authorization"] == "Bearer vck_test"
    # The Groq-hosted GPT-OSS entry is untouched.
    assert registry.get_model("groq-gpt-oss").base_url == "https://api.groq.com/openai/v1"


def test_cerebras_gpt_oss_registered_with_low_reasoning(monkeypatch) -> None:
    """GPT-OSS 120B on Cerebras (OpenAI-compatible endpoint)."""
    from app.core.config import get_settings

    spec = registry.get_model("cerebras-gpt-oss")
    assert spec.provider == "Cerebras"
    assert spec.base_url == get_settings().cerebras_base_url == "https://api.cerebras.ai/v1"
    assert spec.model_id == get_settings().cerebras_gpt_oss_model == "gpt-oss-120b"
    assert spec.reasoning_effort == "low"
    # Unavailable without the key (conftest blanks CEREBRAS_API_KEY); no secrets listed.
    row = next(m for m in registry.list_models() if m["key"] == "cerebras-gpt-oss")
    assert row["available"] is False and "api_key" not in row
    assert isinstance(registry.get_llm_provider("cerebras-gpt-oss"), MockLLMProvider)

    monkeypatch.setattr(get_settings(), "cerebras_api_key", "csk-test")
    provider = registry.get_llm_provider("cerebras-gpt-oss")
    assert isinstance(provider, OpenAICompatibleLLMProvider)
    body, _ = provider._request_payload("medium", "r", "j", "q")
    assert body["model"] == "gpt-oss-120b"
    assert body["reasoning_effort"] == "low"
    assert provider._headers()["Authorization"] == "Bearer csk-test"
