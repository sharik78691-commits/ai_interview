"""On-demand "If they ask further" (follow-ups) — answers no longer carry them.

Follow-ups were 15-37% of every answer's output tokens. They are now generated
only when the candidate clicks "Show follow-ups", so the Best Answer finishes
sooner and costs less, while the follow-ups keep their per-depth rules.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.ai_response import AIInterviewResponse, FollowUp
from app.providers.llm.provider import (
    FOLLOWUP_LIMIT,
    FOLLOWUP_RULES,
    LENGTH_RULES,
    LLMQuotaError,
    MockLLMProvider,
    OpenAICompatibleLLMProvider,
    build_followup_prompt,
    build_system_prompt,
)
from app.services.ai_service import AIService
from tests.test_llm_registry import GUIDANCE


# ------------------------------------------------------------------ prompts

@pytest.mark.parametrize("level", list(LENGTH_RULES))
def test_answer_prompt_asks_for_no_follow_ups(level) -> None:
    prompt = build_system_prompt(level)
    assert '"followUps": []' in prompt
    assert "followUps = ALWAYS []" in prompt
    # The per-depth follow-up rules moved out of the answer prompt.
    assert FOLLOWUP_RULES[level] not in prompt
    assert "- followUps:" not in LENGTH_RULES[level]
    # The Best Answer rules themselves are untouched.
    assert "answerPoints = BEST ANSWER" in prompt


@pytest.mark.parametrize("level", list(FOLLOWUP_RULES))
def test_followup_prompt_keeps_the_per_depth_rules(level) -> None:
    prompt = build_followup_prompt(level)
    assert FOLLOWUP_RULES[level] in prompt
    assert '{"followUps": [{"question": str, "answer": str}]}' in prompt
    assert "Never invent resume facts" in prompt


# ------------------------------------------------------------ provider call

class _Resp:
    def __init__(self, status: int, payload: dict | None = None, text: str = ""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)
        self.headers: dict = {}
        self.request = None

    def json(self) -> dict:
        return self._payload


def _client(*responses):
    post = AsyncMock(side_effect=list(responses))

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            return await post(*a, **kw)

    return _C(), post


def _completion(follow_ups: list[dict]) -> _Resp:
    return _Resp(200, {
        "choices": [{"message": {"content": json.dumps({"followUps": follow_ups})}}],
        "usage": {"prompt_tokens": 600, "completion_tokens": 120},
    })


def _provider(**kw) -> OpenAICompatibleLLMProvider:
    return OpenAICompatibleLLMProvider(
        api_key="k", base_url="https://api.test/v1", model="gpt-oss-120b", **kw
    )


async def test_generate_follow_ups_sends_answer_context_and_parses() -> None:
    items = [{"question": f"Q{i}?", "answer": f"A{i}."} for i in range(6)]
    client, post = _client(_completion(items))
    p = _provider(reasoning_effort="low")
    with patch("app.providers.llm.provider.shared_http", return_value=client):
        out = await p.generate_follow_ups(
            "Java dev", "Backend role", "What is a saga?", ["A saga splits a transaction."], "medium"
        )
    assert [f.question for f in out] == ["Q0?", "Q1?", "Q2?"]  # medium: max 3
    assert len(out) == FOLLOWUP_LIMIT["medium"]
    body = post.call_args.kwargs["json"]
    assert body["model"] == "gpt-oss-120b"
    assert body["reasoning_effort"] == "low"
    assert body["messages"][0]["content"] == build_followup_prompt("medium")
    user = body["messages"][1]["content"]
    assert "What is a saga?" in user and "A saga splits a transaction." in user
    assert p.last_input_tokens == 600 and p.last_output_tokens == 120


async def test_generate_follow_ups_drops_empty_items_and_bad_shapes() -> None:
    items = [{"question": "  ", "answer": "x"}, "junk", {"question": "Real?", "answer": ""}]
    client, _ = _client(_completion(items))
    with patch("app.providers.llm.provider.shared_http", return_value=client):
        out = await _provider().generate_follow_ups("", "", "q", [], "default")
    assert [f.question for f in out] == ["Real?"]


async def test_generate_follow_ups_raises_on_auth_error() -> None:
    client, _ = _client(_Resp(401, text='{"error":"bad key"}'))
    with patch("app.providers.llm.provider.shared_http", return_value=client):
        with pytest.raises(LLMQuotaError):
            await _provider().generate_follow_ups("", "", "q", [], "medium")


async def test_generate_follow_ups_retries_unreadable_json_once() -> None:
    bad = _Resp(200, {"choices": [{"message": {"content": "not json"}}]})
    client, post = _client(bad, _completion([{"question": "Why?", "answer": "Because."}]))
    with patch("app.providers.llm.provider.shared_http", return_value=client):
        out = await _provider().generate_follow_ups("", "", "q", [], "medium")
    assert [f.question for f in out] == ["Why?"]
    assert post.await_count == 2


async def test_mock_provider_returns_template_follow_ups() -> None:
    out = await MockLLMProvider().generate_follow_ups("", "", "What is a closure?", [], "medium")
    assert out and all(isinstance(f, FollowUp) and f.question for f in out)


# ------------------------------------------------------------ service layer

async def test_service_follow_ups_use_the_answer_and_are_cached() -> None:
    provider = AsyncMock()
    provider.name = "fake"
    provider.model = "m"
    provider.analyze_question = AsyncMock(return_value=AIInterviewResponse(**GUIDANCE))
    provider.generate_follow_ups = AsyncMock(
        return_value=[FollowUp(question="When would you use one?", answer="For callbacks.")]
    )
    svc = AIService("resume", "jd", "medium", provider_factory=lambda key: provider)
    await svc.analyze("What is a closure?")

    first = await svc.follow_ups("What is a closure?")
    again = await svc.follow_ups("  what is a CLOSURE?  ")  # same question, normalised
    assert first == again and first[0].question == "When would you use one?"
    provider.generate_follow_ups.assert_awaited_once()
    args = provider.generate_follow_ups.await_args.args
    assert args[2] == "What is a closure?"
    assert args[3] == GUIDANCE["answerPoints"]  # builds on the actual Best Answer
    assert args[4] == "medium"


async def test_service_follow_ups_not_cached_when_empty() -> None:
    provider = AsyncMock()
    provider.generate_follow_ups = AsyncMock(return_value=[])
    svc = AIService(provider_factory=lambda key: provider)
    assert await svc.follow_ups("q") == []
    assert await svc.follow_ups("q") == []
    assert provider.generate_follow_ups.await_count == 2


# ------------------------------------------------------------ websocket

def _auth(client: TestClient) -> None:
    client.post(
        "/api/auth/register",
        json={"email": "followups@example.com", "password": "Passw0rd123", "name": "F"},
    )


@patch("app.api.websocket.AIService")
def test_ws_request_followups_returns_ai_followups(ai_cls) -> None:
    ai_cls.return_value.follow_ups = AsyncMock(
        return_value=[FollowUp(question="Why Kafka?", answer="Durable log.")]
    )
    ai_cls.return_value.last_usage = {}
    with TestClient(app) as tc:
        _auth(tc)
        with tc.websocket_connect("/ws/interview") as ws:
            ws.receive_json()  # handshake
            ws.send_json({"type": "request_followups", "question": "What is a saga?"})
            frame = ws.receive_json()
    assert frame == {
        "type": "ai_followups",
        "question": "What is a saga?",
        "followUps": [{"question": "Why Kafka?", "answer": "Durable log."}],
    }


@patch("app.api.websocket.AIService")
def test_ws_request_followups_reports_errors_and_bad_input(ai_cls) -> None:
    ai_cls.return_value.follow_ups = AsyncMock(side_effect=LLMQuotaError("down"))
    ai_cls.return_value.last_usage = {}
    with TestClient(app) as tc:
        _auth(tc)
        with tc.websocket_connect("/ws/interview") as ws:
            ws.receive_json()
            ws.send_json({"type": "request_followups", "question": "What is a saga?"})
            failed = ws.receive_json()
            ws.send_json({"type": "request_followups", "question": ""})
            invalid = ws.receive_json()
    assert failed["type"] == "ai_followups" and failed["followUps"] == []
    assert failed["error"] == "Could not load follow-ups. Try again."
    assert invalid["type"] == "ai_followups" and invalid["error"] == "Invalid question."
