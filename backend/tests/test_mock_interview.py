"""Mock interviews: practice questions and scored feedback on the candidate's answer."""
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.providers.llm.base import LLMProvider
from app.providers.llm.provider import LLMQuotaError, OpenAICompatibleLLMProvider
from app.services.mock_interview_service import MockInterviewService, basic_feedback
from tests.conftest import authenticate, csrf_headers
from tests.test_followups_on_demand import _client, _Resp


class FakeProvider(LLMProvider):
    """Returns a canned JSON reply (or raises) from complete_json."""

    name = "fake"
    model = "fake-model"

    def __init__(self, reply=None, error: Exception | None = None) -> None:
        self.reply = reply or {}
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def analyze_question(self, *a, **kw):  # pragma: no cover - unused here
        raise NotImplementedError

    async def complete_json(self, system: str, user: str, max_tokens: int = 1200) -> dict:
        self.calls.append((system, user))
        if self.error:
            raise self.error
        return self.reply


def _service(provider: LLMProvider) -> MockInterviewService:
    return MockInterviewService(provider_factory=lambda _key: provider)


# ------------------------------------------------------------- questions


async def test_questions_from_ai_are_deduped_typed_and_capped() -> None:
    fake = FakeProvider({"questions": [
        {"question": "Tell me about a conflict.", "type": "behavioral", "focus": "STAR"},
        {"question": "tell me about a conflict.", "type": "behavioral"},  # duplicate
        {"question": "Design a URL shortener.", "type": "system-design"},  # unknown type
        {"question": "  ", "type": "hr"},  # empty
        "junk",
        {"question": "Why this company?", "type": "hr"},
    ]})
    qs, source = await _service(fake).questions("Backend Engineer", "Python, AWS", "", "mixed", "hard", 2)
    assert source == "ai"
    assert [q.question for q in qs] == ["Tell me about a conflict.", "Design a URL shortener."]
    assert [q.id for q in qs] == [1, 2]
    assert qs[1].type == "technical"  # unknown type falls back
    system, user = fake.calls[0]
    assert "MOCK interview" in system
    assert "Backend Engineer" in user and "hard" in user and "Python, AWS" in user


async def test_questions_fall_back_to_built_in_bank_without_a_model() -> None:
    qs, source = await _service(FakeProvider(error=NotImplementedError())).questions(
        "Data Analyst", interview_type="behavioral", count=3
    )
    assert source == "basic"
    assert len(qs) == 3 and all(q.type == "behavioral" for q in qs)


async def test_mixed_bank_alternates_types_and_fills_role() -> None:
    qs, _ = await _service(FakeProvider(error=LLMQuotaError("quota"))).questions(
        "QA Engineer", interview_type="mixed", count=4
    )
    assert [q.type for q in qs] == ["hr", "technical", "behavioral", "hr"]
    assert all("{role}" not in q.question + q.focus for q in qs)
    assert any("QA Engineer" in q.question + q.focus for q in qs)


# -------------------------------------------------------------- feedback


async def test_feedback_from_ai_is_clamped_and_trimmed() -> None:
    fake = FakeProvider({
        "score": 14, "summary": "Clear and specific.",
        "strengths": ["a", "b", "c", "d", "e"], "improvements": ["Add a metric."],
        "sampleAnswer": ["Situation", "Action", "Result"],
    })
    fb = await _service(fake).feedback("Tell me about a conflict.", "I resolved it by ...", "behavioral")
    assert fb.source == "ai"
    assert fb.score == 10
    assert len(fb.strengths) == 4
    assert fb.improvements == ["Add a metric."]
    assert "PRACTICE" in fake.calls[0][0]
    assert "I resolved it by ..." in fake.calls[0][1]


@pytest.mark.parametrize("reply", [{}, {"score": "n/a", "summary": "x"}, {"score": 6}])
async def test_unusable_ai_feedback_falls_back_to_basic(reply) -> None:
    fb = await _service(FakeProvider(reply)).feedback("Q?", "Short answer.", "technical")
    assert fb.source == "basic"


def test_basic_feedback_rewards_star_structure_and_numbers() -> None:
    star = (
        "In my previous job the situation was a failing release. My task was to fix the "
        "pipeline before launch. I decided to split the build and I implemented caching. "
        "As a result we reduced build time by 40% and shipped on time for 2000 users."
    )
    strong = basic_feedback("Tell me about a deadline.", star, "behavioral")
    weak = basic_feedback("Tell me about a deadline.", "We did it.", "behavioral")
    assert strong.score > weak.score
    assert any("STAR" in s for s in strong.strengths)
    assert any("short" in s for s in weak.improvements)
    assert 1 <= weak.score <= 10 and 1 <= strong.score <= 10


# ------------------------------------------------- provider complete_json


async def test_complete_json_posts_prompt_and_returns_dict() -> None:
    reply = _Resp(200, {
        "choices": [{"message": {"content": json.dumps({"score": 7})}}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 10},
    })
    client, post = _client(reply)
    p = OpenAICompatibleLLMProvider(api_key="k", base_url="https://api.test/v1", model="m")
    with patch("app.providers.llm.provider.shared_http", return_value=client):
        out = await p.complete_json("SYS", "USER", max_tokens=321)
    assert out == {"score": 7}
    body = post.call_args.kwargs["json"]
    assert body["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}]
    assert body["max_tokens"] == 321
    assert p.last_input_tokens == 50


async def test_complete_json_raises_quota_error_on_auth_failure() -> None:
    client, _ = _client(_Resp(401, text='{"error":"bad key"}'))
    p = OpenAICompatibleLLMProvider(api_key="k", base_url="https://api.test/v1", model="m")
    with patch("app.providers.llm.provider.shared_http", return_value=client):
        with pytest.raises(LLMQuotaError):
            await p.complete_json("s", "u")


# ------------------------------------------------------------------- API


def test_endpoints_require_sign_in() -> None:
    c = TestClient(app)
    assert c.post("/api/mock-interview/questions", json={}).status_code in (401, 403)
    assert c.post("/api/mock-interview/feedback", json={"question": "q", "answer": "a"}).status_code in (401, 403)


def test_questions_endpoint_in_demo_mode_uses_built_in_bank() -> None:
    c = TestClient(app)
    authenticate(c, "mock1@example.com")
    r = c.post(
        "/api/mock-interview/questions",
        json={"role": "Java Developer", "interviewType": "technical", "difficulty": "easy", "count": 3},
        headers=csrf_headers(c),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "basic"
    assert len(body["questions"]) == 3
    assert {q["type"] for q in body["questions"]} == {"technical"}


def test_questions_endpoint_validates_count_and_type() -> None:
    c = TestClient(app)
    authenticate(c, "mock2@example.com")
    assert c.post("/api/mock-interview/questions", json={"count": 50}, headers=csrf_headers(c)).status_code == 422
    assert c.post("/api/mock-interview/questions", json={"interviewType": "live"}, headers=csrf_headers(c)).status_code == 422


def test_feedback_endpoint_scores_answer_and_rejects_blank() -> None:
    c = TestClient(app)
    authenticate(c, "mock3@example.com")
    r = c.post(
        "/api/mock-interview/feedback",
        json={"question": "Tell me about yourself.", "questionType": "hr", "answer": "I am a developer."},
        headers=csrf_headers(c),
    )
    assert r.status_code == 200
    fb = r.json()
    assert 1 <= fb["score"] <= 10 and fb["source"] == "basic" and fb["improvements"]
    blank = c.post(
        "/api/mock-interview/feedback",
        json={"question": "Q?", "answer": "   "},
        headers=csrf_headers(c),
    )
    assert blank.status_code == 422
