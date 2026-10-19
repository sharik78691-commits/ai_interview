"""Per-model latency regressions (mocked providers — no external API calls).

Measured baseline that motivated these tests (real providers, repeated runs so
single-sample network outliers were not mistaken for signal):

  openai/gpt-oss-120b (the DEFAULT model)  first bullet 1.12s -> 0.50s with
                                            reasoning_effort="low", total
                                            1.56s -> 0.84s, JSON valid either way
  gemini-3.5-flash                          first bullet 4.43s vs 0.71s for
                                            Flash-Lite — it thinks first
  gemini-3.5-flash at max_tokens=700        finish_reason="length", 0/3 valid
                                            JSON -> forced whole extra request
  gemini-flash-lite with effort="none"      HTTP 400 (out-of-enum)

These tests pin:
1. reasoning_effort is wired for the models where it measured a win, and NOT
   for the ones where it measured neutral/negative or was never verified.
2. The field actually reaches the request body (stream AND single-shot).
3. A provider that REJECTS the field can never fail the answer.
4. The spare attempt does not extend normal retries (a slow provider does not
   get slower because of this change).
5. A truncated/empty answer lifts the output budget so the retry has headroom
   instead of failing identically.
6. Time-to-first-bullet is recorded — it is what the candidate perceives, and
   the stored total latency hides it.
"""
import json
from unittest.mock import patch

import pytest

from app.providers.llm import registry
from app.providers.llm.provider import (
    LLMQuotaError,
    MAX_TOKENS,
    OpenAICompatibleLLMProvider,
    build_system_prompt,
)
from tests.test_llm_registry import GUIDANCE
from tests.test_llm_stream import _FakeStreamResp, _sse_lines, _stream_client_for


def _provider(**kwargs) -> OpenAICompatibleLLMProvider:
    return OpenAICompatibleLLMProvider(
        api_key="test-key", base_url="https://example.test/v1", model="test-model",
        **kwargs,
    )


def _collect(deltas: list):
    async def _cb(point: str) -> None:
        deltas.append(point)

    return _cb


class _PostResp:
    """Minimal httpx.Response stub for single-shot calls."""

    def __init__(self, status_code: int, payload: dict | None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers: dict = {}
        self.request = None
        self.text = text or json.dumps(self._payload)

    def json(self) -> dict:
        return self._payload


def _ok_post(content: str = json.dumps(GUIDANCE)) -> _PostResp:
    return _PostResp(200, {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    })


class _FakeClient:
    """Serves a queued sequence of stream responses AND single-shot responses,
    recording every request body so tests can inspect the payload."""

    def __init__(self, streams: list | None = None, posts: list | None = None):
        self.streams = list(streams or [])
        self.posts = list(posts or [])
        self.stream_bodies: list = []
        self.post_bodies: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, *args, **kwargs):
        self.stream_bodies.append(kwargs.get("json"))
        resp = self.streams.pop(0)

        class _CM:
            async def __aenter__(self):
                return resp

            async def __aexit__(self, *a):
                return False

        return _CM()

    async def post(self, *args, **kwargs):
        self.post_bodies.append(kwargs.get("json"))
        return self.posts.pop(0)


def _broken_json_stream() -> list[str]:
    """A stream that yields complete bullets but no closable JSON object.

    Cutting at the last comma removes the ONLY closing brace (GUIDANCE is a
    flat dict), so _extract_json must fail and the budget must be lifted.
    """
    full = json.dumps(GUIDANCE)
    truncated = full[: full.rfind(",")]
    assert "}" not in truncated  # guarantee the parse really fails
    return (
        ["data: " + json.dumps({"choices": [{"delta": {"content": truncated}}]})]
        + ["data: [DONE]"]
    )


# ------------------------------------------------------- config / wiring

def test_reasoning_effort_only_where_measured_to_help() -> None:
    """Enabled: Groq gpt-oss (1.12s -> 0.50s to first bullet) and full Flash
    (4.43s -> 3.38s). Left OFF where it measured neutral or negative (flash-lite
    0.71 vs 0.74s, 3.1-lite 1.40 vs 2.02s) or was never verified (OpenRouter has
    no API key configured here; OpenAI was never probed)."""
    assert registry.get_model("groq-gpt-oss").reasoning_effort == "low"
    assert registry.get_model("gemini-3.5-flash").reasoning_effort == "low"

    assert registry.get_model("gemini-flash").reasoning_effort is None
    assert registry.get_model("gemini-3.1-flash-lite").reasoning_effort is None
    assert registry.get_model("qwen-coder").reasoning_effort is None
    assert registry.get_model("openrouter-free").reasoning_effort is None
    assert registry.get_model("openai-gpt6-luna").reasoning_effort is None


def test_reasoning_effort_reaches_the_request_payload() -> None:
    p = _provider(reasoning_effort="low")
    body, max_tokens = p._request_payload("short", "r", "j", "q")
    assert body["reasoning_effort"] == "low"
    assert body["temperature"] == 0.4
    assert max_tokens == MAX_TOKENS["short"]


def test_provider_without_reasoning_config_omits_the_field() -> None:
    p = _provider()
    body, _ = p._request_payload("short", "r", "j", "q")
    assert "reasoning_effort" not in body


def test_registry_passes_reasoning_effort_through(monkeypatch) -> None:
    # A fake key builds the real provider class without any network call
    # (the suite blanks live keys, which would otherwise yield the mock).
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "groq_api_key", "test-key")
    p = registry.get_llm_provider("groq-gpt-oss")
    assert getattr(p, "reasoning_effort", None) == "low"
    p2 = registry.get_llm_provider("gemini-flash")
    assert getattr(p2, "reasoning_effort", None) is None


# ------------------------------------------------------- short-cap headroom

def test_short_token_cap_has_reasoning_headroom() -> None:
    """700 sat below what a reasoning model needs just to think: gpt-oss came
    back with finish_reason="length" and gemini-3.5-flash returned 0/3 valid
    JSON at that cap. Non-reasoning models still stop on their own, so the
    larger cap costs no latency (flash-lite 1.56s @700 vs 1.61s @1200)."""
    assert MAX_TOKENS["short"] >= 1000
    assert MAX_TOKENS["medium"] >= MAX_TOKENS["short"]
    assert MAX_TOKENS["long"] >= MAX_TOKENS["medium"]


# ------------------------------------------------------- safety net

async def test_stream_drops_reasoning_effort_after_400_then_succeeds() -> None:
    """A provider update that stops accepting the field must degrade to the old
    behaviour, never fail the answer outright."""
    provider = _provider(reasoning_effort="low")
    fake = _FakeClient(streams=[_FakeStreamResp(400, []),
                                _FakeStreamResp(200, _sse_lines(GUIDANCE))])
    deltas: list[str] = []
    with patch("app.providers.llm.provider.shared_http", return_value=fake):
        resp = await provider.analyze_question_stream(
            "r", "j", "What is a closure?", "medium", _collect(deltas)
        )

    assert len(fake.stream_bodies) == 2, fake.stream_bodies
    assert fake.stream_bodies[0].get("reasoning_effort") == "low"
    assert "reasoning_effort" not in fake.stream_bodies[1]
    assert provider._reasoning_disabled is True
    assert deltas == GUIDANCE["answerPoints"]
    assert resp.question == "What is a closure?"
    assert provider.last_error_category == "ok"


async def test_single_shot_drops_reasoning_effort_after_400_then_succeeds() -> None:
    provider = _provider(reasoning_effort="low")
    fake = _FakeClient(posts=[
        _PostResp(400, None, '{"error":"unknown parameter"}'),
        _ok_post(),
    ])
    with patch("app.providers.llm.provider.shared_http", return_value=fake):
        resp = await provider.analyze_question("r", "j", "What is a closure?", "medium")

    assert len(fake.post_bodies) == 2, fake.post_bodies
    assert fake.post_bodies[0].get("reasoning_effort") == "low"
    assert "reasoning_effort" not in fake.post_bodies[1]
    assert resp.answerPoints == GUIDANCE["answerPoints"]
    assert provider.last_error_category == "ok"


async def test_single_shot_drops_reasoning_effort_after_422_then_succeeds() -> None:
    """DeepSeek rejects a bad effort value with HTTP 422 (body deserialization:
    `unknown variant ... expected one of none, minimal, low, ...`), not 400 —
    the safety net must cover both statuses or the retry would never fire."""
    provider = _provider(reasoning_effort="low")
    fake = _FakeClient(posts=[
        _PostResp(422, None,
                  '{"error":"reasoning_effort: unknown variant `banana`"}'),
        _ok_post(),
    ])
    with patch("app.providers.llm.provider.shared_http", return_value=fake):
        resp = await provider.analyze_question("r", "j", "What is a closure?", "medium")

    assert len(fake.post_bodies) == 2, fake.post_bodies
    assert fake.post_bodies[0].get("reasoning_effort") == "low"
    assert "reasoning_effort" not in fake.post_bodies[1]
    assert provider._reasoning_disabled is True
    assert resp.answerPoints == GUIDANCE["answerPoints"]


async def test_422_without_reasoning_effort_is_not_retried() -> None:
    """A 422 caused by something else must NOT be mistaken for a rejected
    field: no effort was sent, so there is nothing to drop and nothing to
    retry — fail fast instead of burning a second request."""
    provider = _provider()
    provider.max_retries = 2
    fake = _FakeClient(posts=[_PostResp(422, None, '{"error":"bad body"}')] * 4)
    with patch("app.providers.llm.provider.shared_http", return_value=fake):
        with pytest.raises(LLMQuotaError):
            await provider.analyze_question("r", "j", "What is a closure?", "short")

    assert len(fake.post_bodies) == 1, fake.post_bodies


async def test_spare_attempt_does_not_extend_normal_retries() -> None:
    """The extra attempt exists ONLY to drop a rejected field. A provider that
    is merely rate-limiting must still be abandoned after max_retries, so this
    change cannot make a slow provider slower."""
    provider = _provider(reasoning_effort="low")
    provider.max_retries = 1
    fake = _FakeClient(posts=[_PostResp(429, None, '{"error":"rate"}')] * 5)
    with patch("app.providers.llm.provider.shared_http", return_value=fake):
        with pytest.raises(LLMQuotaError):
            await provider.analyze_question("r", "j", "What is a closure?", "short")

    assert len(fake.post_bodies) == provider.max_retries + 1, fake.post_bodies


async def test_non_reasoning_provider_also_gets_only_max_retries() -> None:
    provider = _provider()
    provider.max_retries = 1
    fake = _FakeClient(posts=[_PostResp(429, None, '{"error":"rate"}')] * 5)
    with patch("app.providers.llm.provider.shared_http", return_value=fake):
        with pytest.raises(LLMQuotaError):
            await provider.analyze_question("r", "j", "What is a closure?", "short")

    assert len(fake.post_bodies) == provider.max_retries + 1, fake.post_bodies


# ------------------------------------------------------- budget lift

async def test_unparseable_stream_answer_lifts_budget_for_the_retry() -> None:
    """Truncated JSON used to be retried with the SAME budget, so the second
    attempt failed identically and the user got templates. The retry must get
    real headroom."""
    provider = _provider()
    fake = _FakeClient(
        streams=[_FakeStreamResp(200, _broken_json_stream())],
        posts=[_ok_post()],
    )
    deltas: list[str] = []
    with patch("app.providers.llm.provider.shared_http", return_value=fake):
        resp = await provider.analyze_question_stream(
            "r", "j", "What is a closure?", "short", _collect(deltas)
        )

    assert resp.answerPoints == GUIDANCE["answerPoints"]
    # The stream DID emit provisional bullets before the parse failed.
    assert deltas, deltas
    assert len(fake.post_bodies) == 1, fake.post_bodies
    assert provider._budget_multiplier > 1.0
    assert fake.post_bodies[0]["max_tokens"] > MAX_TOKENS["short"]
    assert provider.last_error_category == "ok"


async def test_empty_content_lifts_budget_and_sends_retry_addendum() -> None:
    """A reasoning model that spends the whole cap thinking returns empty
    content. The retry must be BOTH bigger and explicitly JSON-only."""
    provider = _provider()
    fake = _FakeClient(posts=[
        _PostResp(200, {"choices": [{"message": {"content": ""}}], "usage": {}}),
        _ok_post(),
    ])
    with patch("app.providers.llm.provider.shared_http", return_value=fake):
        resp = await provider.analyze_question("r", "j", "What is a closure?", "short")

    assert len(fake.post_bodies) == 2
    assert fake.post_bodies[1]["max_tokens"] > fake.post_bodies[0]["max_tokens"]
    # The retry addendum, not the base prompt's own wording.
    retry_prompt = fake.post_bodies[1]["messages"][0]["content"]
    assert "Do not add reasoning text" in retry_prompt
    assert "Do not add reasoning text" not in fake.post_bodies[0]["messages"][0]["content"]
    assert resp.answerPoints == GUIDANCE["answerPoints"]


def test_retry_addendum_is_absent_on_a_fresh_provider() -> None:
    p = _provider()
    body, _ = p._request_payload("short", "r", "j", "q")
    assert p._system_addendum == ""
    assert body["messages"][0]["content"] == build_system_prompt("short")


# ------------------------------------------------------- perceived latency

async def test_first_bullet_latency_is_recorded() -> None:
    """The candidate perceives time-to-first-bullet, not the total. The stored
    total hides the model's silent thinking phase, so this is tracked apart."""
    provider = _provider()
    deltas: list[str] = []
    fake = _stream_client_for(_FakeStreamResp(200, _sse_lines(GUIDANCE)))
    with patch("app.providers.llm.provider.shared_http", return_value=fake):
        assert provider.first_delta_ms is None  # not set until a stream runs
        await provider.analyze_question_stream(
            "r", "j", "What is a closure?", "medium", _collect(deltas)
        )
    assert provider.first_delta_ms is not None
    assert provider.first_delta_ms > 0
    assert deltas == GUIDANCE["answerPoints"]


async def test_first_bullet_latency_absent_when_nothing_streamed() -> None:
    provider = _provider()
    fake = _FakeClient(posts=[_ok_post()])
    with patch("app.providers.llm.provider.shared_http", return_value=fake):
        resp = await provider.analyze_question("r", "j", "What is a closure?", "short")
    assert resp.answerPoints  # non-stream path still works
    assert provider.first_delta_ms is None


async def test_service_exposes_first_bullet_latency() -> None:
    """AIService must surface time-to-first-bullet next to the total, so the
    backend log can compare models by what the candidate actually saw."""
    from app.services import ai_service as svc_mod
    from app.services.ai_service import AIService

    class _StreamProvider:
        name = "test"
        model = "test-model"
        last_latency_ms = 1234.0
        first_delta_ms = 555.0
        last_input_tokens = 10
        last_output_tokens = 20
        last_error_category = "ok"

        async def analyze_question_stream(self, *args, **kwargs):
            return "answer"

        async def analyze_question(self, *args, **kwargs):
            return "answer"

    svc = AIService(resume_text="r", job_description="j")
    with patch.object(svc_mod, "get_llm_provider", lambda key=None: _StreamProvider()):
        await svc.analyze("What is a closure?")

    assert svc.last_usage["first_delta_ms"] == 555.0
    assert svc.last_usage["latency_ms"] == 1234.0
    assert svc.last_usage["error_category"] == "ok"
