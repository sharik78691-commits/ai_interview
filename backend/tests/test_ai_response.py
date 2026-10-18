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
    svc_mod.get_llm_provider = lambda model_key=None: Boom()
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
    assert len(long.keyDetails) >= len(medium.keyDetails) >= len(short.keyDetails)
    assert len(long.followUps) > len(medium.followUps) > len(short.followUps)
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
    assert "5-8 SPOKEN SENTENCES" in long_prompt
    assert "2-3 SHORT SPOKEN SENTENCES" in build_system_prompt("short").upper()


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


def test_prompt_requires_human_voice() -> None:
    """The shared system prompt must carry the human-voice rules (all 4
    live providers use it)."""
    from app.providers.llm.provider import build_system_prompt

    prompt = build_system_prompt("medium")
    assert "HUMAN VOICE" in prompt
    assert "SAY OUT LOUD" in prompt
    assert "BANNED" in prompt


def test_prompt_is_adaptive_answer_card() -> None:
    """The prompt asks for ONE adaptive Interview Answer Card, not the old
    fixed sections (opener / comparison table / takeaways / code hint)."""
    from app.providers.llm.provider import BASE_SCHEMA, build_system_prompt

    prompt = build_system_prompt("medium")
    assert "INTERVIEW ANSWER CARD" in prompt
    for section in ("BEST ANSWER", "EXPLAIN IT SIMPLY", "WHAT THE INTERVIEWER EXPECTS",
                    "PRACTICAL EXAMPLE", "IF THEY ASK FURTHER", "CODING CARD"):
        assert section in prompt, section
    assert "Do NOT force every section" in prompt
    for qtype in ("HR", "THEORY", "SCENARIO", "CODING"):
        assert qtype in prompt, qtype
    # Old fixed sections are no longer requested.
    assert "COMPARISON TABLE" not in prompt
    assert '"comparison"' not in BASE_SCHEMA
    assert '"keyTakeaways"' not in BASE_SCHEMA
    # answerPoints must stay the first content key so streaming latency
    # (first provisional sentence) is unchanged.
    assert BASE_SCHEMA.index('"answerPoints"') < BASE_SCHEMA.index('"simpleExplanation"')


async def test_mock_comparison_question_contrasts_in_key_details() -> None:
    provider = MockLLMProvider()
    resp = await provider.analyze_question(
        "resume", "job", "What is the difference between SQL and NoSQL?"
    )
    assert resp.comparison is None  # no separate "Quick comparison" table
    assert any("SQL" in d and "NoSQL" in d for d in resp.keyDetails)


async def test_mock_coding_card_has_approach_complexity_edge_cases() -> None:
    provider = MockLLMProvider()
    for level in ("short", "medium", "long"):
        resp = await provider.analyze_question(
            "Python resume", "Python role", "Write a function to solve two-sum?", level
        )
        assert resp.approach and resp.codeHint and resp.complexity, level
        # Two programs: the AI solution and a human-style rewrite.
        assert resp.humanCode and "def " in resp.humanCode, level
        assert resp.humanCode != resp.codeHint
        assert "#" in resp.humanCode  # carries plain-language comments
        assert "O(" in resp.complexity
        assert len(resp.edgeCases) >= 2


async def test_mock_behavioral_card_is_story_not_theory() -> None:
    provider = MockLLMProvider()
    resp = await provider.analyze_question(
        "resume", "job", "Tell me about a time you faced a difficult challenge?"
    )
    assert resp.star is not None
    assert resp.simpleExplanation is None  # HR/scenario: no "explain it simply"
    assert resp.approach is None and resp.codeHint is None and resp.humanCode is None
    assert all(f.answer for f in resp.followUps)


def test_card_tolerates_model_slips_and_keeps_legacy_fields() -> None:
    """Blank sections become null, string follow-ups are coerced, and the
    legacy followUpQuestions list stays filled for older clients."""
    resp = AIInterviewResponse(
        question="q",
        questionType="technical",
        answerPoints=["It's a queue."],
        simpleExplanation="   ",
        example="",
        keyDetails=["  ", "**FIFO** order"],
        followUps=["Why a queue? - It decouples producers from consumers.", {"question": "", "answer": "x"}],
    )
    assert resp.simpleExplanation is None
    assert resp.example is None
    assert resp.keyDetails == ["**FIFO** order"]
    assert [f.question for f in resp.followUps] == ["Why a queue?"]
    assert resp.followUps[0].answer == "It decouples producers from consumers."
    assert resp.followUpQuestions == ["Why a queue?"]


def test_legacy_payload_still_validates() -> None:
    """Answers stored in the old shape (comparison/keyTakeaways) still load."""
    resp = AIInterviewResponse(
        question="q",
        questionType="technical",
        answerPoints=["a"],
        keyTakeaways=["t"],
        followUpQuestions=["f - hint"],
        comparison={"headers": ["Aspect", "A", "B"], "rows": [["x", "1", "2"]]},
    )
    assert resp.followUps == []
    assert resp.followUpQuestions == ["f - hint"]
    assert resp.comparison is not None


async def test_comparison_schema_roundtrip() -> None:
    """comparison serializes/deserializes cleanly through Pydantic."""
    from app.models.ai_response import ComparisonTable

    t = ComparisonTable(headers=["Aspect", "A", "B"], rows=[["X", "1", "2"]])
    data = t.model_dump()
    assert ComparisonTable(**data) == t


async def test_mock_has_no_ai_tells() -> None:
    """Mock/demo guidance must read human — no robotic AI-tell phrases in
    any branch or depth level."""
    provider = MockLLMProvider()
    banned = [
        "as an ai", "as a language model", "delve", "furthermore", "moreover",
        "in today's fast-paced", "landscape", "tapestry", "game-changer",
        "cutting-edge", "it's important to note", "in conclusion",
    ]
    questions = [
        "Can you explain the Saga pattern in microservices?",  # technical
        "Tell me about a time you faced a difficult challenge?",  # behavioral
        "Write a function to reverse a binary tree?",  # coding
        "What is your greatest strength?",  # general
    ]
    for q in questions:
        for level in ("short", "medium", "long"):
            resp = await provider.analyze_question(
                "Python AWS developer", "Backend role", q, level
            )
            blob = " ".join(
                [
                    *resp.answerPoints,
                    resp.example or "",
                    resp.simpleExplanation or "",
                    *resp.keyDetails,
                    *(f.question + " " + f.answer for f in resp.followUps),
                ]
            ).lower()
            for phrase in banned:
                assert phrase not in blob, f"{phrase!r} in {resp.questionType}/{level}"
            # No two bullets opening the same way (listicle monotony).
            openers = [p.split()[0].lower().strip(",;:") for p in resp.answerPoints]
            assert len(set(openers)) == len(openers), (
                resp.questionType, level, resp.answerPoints
            )


def test_prompt_requires_two_programs_for_coding() -> None:
    """Coding answers must ship program 1 (AI) AND program 2 (human-style)."""
    from app.providers.llm.provider import BASE_SCHEMA, build_system_prompt

    assert '"humanCode"' in BASE_SCHEMA
    for level in ("short", "medium", "long"):
        prompt = build_system_prompt(level)
        assert "PROGRAM 1" in prompt and "PROGRAM 2" in prompt, level
        assert "humanCode" in prompt, level


def test_default_level_covers_everything_interviewer_expects():
    from app.api.interview import VALID_LENGTHS, _normalize_length
    from app.models.ai_response import AIInterviewResponse
    from app.providers.llm.provider import MAX_TOKENS, build_system_prompt

    assert "default" in VALID_LENGTHS
    assert _normalize_length("default") == "default"
    prompt = build_system_prompt("default").upper()
    assert "DEPTH LEVEL: DEFAULT" in prompt
    assert "EVERY POINT THEY EXPECT" in prompt
    assert MAX_TOKENS["default"] >= MAX_TOKENS["medium"]
    resp = AIInterviewResponse(
        question="q", questionType="technical", answerPoints=["a"], responseLength="default"
    )
    assert resp.responseLength == "default"
