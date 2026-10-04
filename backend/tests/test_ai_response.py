"""AI response / mock provider tests."""
from app.models.ai_response import AIInterviewResponse
from app.providers.llm.provider import MockLLMProvider


async def test_mock_returns_valid_response() -> None:
    provider = MockLLMProvider()
    resp = await provider.analyze_question(
        "Python FastAPI developer with AWS experience",
        "Backend role requiring Python and AWS",
        "Can you explain how you design a REST API?",
    )
    assert isinstance(resp, AIInterviewResponse)
    assert resp.questionType in ("technical", "behavioral", "coding", "general")
    assert len(resp.answerPoints) >= 1


async def test_mock_behavioral_has_star() -> None:
    provider = MockLLMProvider()
    resp = await provider.analyze_question(
        "resume", "job", "Tell me about a time you faced a difficult challenge?"
    )
    assert resp.questionType == "behavioral"
    assert resp.star is not None


async def test_mock_coding_has_hint() -> None:
    provider = MockLLMProvider()
    resp = await provider.analyze_question(
        "resume", "job", "Write a function to reverse a binary tree?"
    )
    assert resp.questionType == "coding"
    assert resp.codeHint


async def test_coding_hint_present_at_every_length() -> None:
    """A coding question must always carry a code hint, at every depth level."""
    provider = MockLLMProvider()
    q = "Write a function to solve two-sum with optimal complexity?"
    for level in ("short", "medium", "long"):
        resp = await provider.analyze_question("Python resume", "Python role", q, level)
        assert resp.questionType == "coding", level
        assert resp.codeHint, f"codeHint missing at level={level}"
        # Real code, not a one-line pseudo sketch.
        assert "\n" in resp.codeHint, f"codeHint not multi-line at level={level}"
        assert "def " in resp.codeHint, f"codeHint has no function at level={level}"


def test_json_schema_roundtrip() -> None:
    resp = AIInterviewResponse(
        question="What is a closure?",
        questionType="technical",
        answerPoints=["Define scope capture.", "Give an example."],
        followUpQuestions=["When would you use one?"],
    )
    data = resp.model_dump()
    assert AIInterviewResponse(**data) == resp


async def test_quota_error_falls_back_with_warning() -> None:
    """A 429/insufficient_quota must not crash the interview, but the user
    should be told live AI was unavailable."""
    from app.providers.llm.provider import LLMQuotaError
    from app.services.ai_service import AIService

    class Boom(MockLLMProvider):
        name = "boom"

        async def analyze_question(
            self, resume, job_description, question, response_length="medium"
        ):
            raise LLMQuotaError("LLM quota exhausted — add credits.")

    import app.services.ai_service as svc_mod

    original = svc_mod.get_llm_provider
    svc_mod.get_llm_provider = lambda: Boom()
    try:
        service = AIService(resume_text="Java dev", job_description="Java role")
        resp = await service.analyze("What is a Saga pattern?")
    finally:
        svc_mod.get_llm_provider = original

    assert resp.answerPoints  # template guidance still produced
    assert service.last_warning and "quota" in service.last_warning.lower()


async def test_response_length_controls_depth() -> None:
    """short -> few bullets, long -> many bullets + example + takeaways."""
    provider = MockLLMProvider()
    q = "Can you explain the Saga pattern in microservices?"
    resume = "10 years Java, Spring Boot, AWS, Kafka"
    job = "Senior Java Microservices Developer"

    short = await provider.analyze_question(resume, job, q, "short")
    medium = await provider.analyze_question(resume, job, q, "medium")
    long = await provider.analyze_question(resume, job, q, "long")

    assert short.responseLength == "short"
    assert len(short.answerPoints) < len(medium.answerPoints) < len(long.answerPoints)
    assert short.example is None
    assert long.example and len(long.example) > 40
    assert len(long.keyTakeaways) >= len(medium.keyTakeaways) >= len(short.keyTakeaways)
    # Long answers must be actionable, not filler.
    assert all(len(p) > 20 for p in long.answerPoints)


async def test_invalid_length_falls_back_to_medium() -> None:
    provider = MockLLMProvider()
    resp = await provider.analyze_question("r", "j", "Explain Saga pattern?", "gigantic")
    assert resp.responseLength == "medium"


def test_length_aliases_normalized() -> None:
    from app.api.interview import _normalize_length

    assert _normalize_length("concise") == "short"
    assert _normalize_length("DETAILED") == "long"
    assert _normalize_length(None) == "medium"
    assert _normalize_length("short") == "short"


def test_prompt_mentions_requested_level() -> None:
    from app.providers.llm.provider import build_system_prompt

    long_prompt = build_system_prompt("long").upper()
    assert "LONG" in long_prompt
    assert "SENIOR" in long_prompt  # senior-level mentoring framing
    assert "8-12" in long_prompt
    assert "3-4" in build_system_prompt("short").upper()


def test_friendly_error_messages() -> None:
    from app.providers.llm.provider import OpenAICompatibleLLMProvider as P

    assert "quota" in P._friendly_error(429, '{"error":{"code":"insufficient_quota"}}').lower()
    assert "rate limit" in P._friendly_error(429, "{}").lower()
    assert "API_KEY" in P._friendly_error(401, "{}")
    # 404 on a retired model must name the model and point at LLM_MODEL.
    retired = P._friendly_error(404, '{"error":{"message":"model not found"}}', "llama-3.3-70b-versatile")
    assert "LLM_MODEL" in retired
    assert "llama-3.3-70b-versatile" in retired


async def test_short_factual_is_technical() -> None:
    provider = MockLLMProvider()
    for q in ("what is Java", "what is AWS", "is Java free"):
        resp = await provider.analyze_question("Java AWS resume", "Java role", q)
        assert resp.questionType == "technical", q
