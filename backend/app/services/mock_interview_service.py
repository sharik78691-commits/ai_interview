"""Mock interviews: role-based practice questions and scored answer feedback.

Self-practice only — the candidate answers in their own words and the model
acts as interviewer and coach. Each call is one JSON request to the admin's
default model (same registry as the answers). When no model is configured or
the provider fails, built-in questions and basic rule-based feedback keep the
practice going, marked ``source="basic"`` so the UI can say so.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable

from app.providers.llm.base import LLMProvider
from app.providers.llm.provider import LLMQuotaError
from app.providers.llm.registry import get_llm_provider
from app.schemas.mock_interview import MockFeedback, MockQuestion

logger = logging.getLogger(__name__)

#: Resolves a model key (None = default model) to a provider instance.
ProviderFactory = Callable[[str | None], LLMProvider]

#: How much resume / JD text reaches the model (more adds cost, not signal).
CONTEXT_CHARS = 3000
QUESTION_TYPES = ("technical", "behavioral", "hr")
LIST_LIMIT = 4
TEXT_LIMIT = 400

QUESTIONS_SYSTEM = (
    "You are an experienced interviewer running a MOCK interview so a candidate can "
    "practise. Write realistic questions for the role, matched to the requested type "
    "and difficulty, and grounded in the job description and resume when given. "
    "Avoid duplicates and trick questions. For each question add a one-sentence "
    "'focus': what a strong answer should show.\n"
    'Reply with JSON only: {"questions":[{"question":"...","type":"technical|behavioral|hr",'
    '"focus":"..."}]}'
)

FEEDBACK_SYSTEM = (
    "You are a supportive but honest interview coach reviewing a candidate's PRACTICE "
    "answer from a mock interview. Score it from 1 (weak) to 10 (excellent) for how "
    "well it would land with a real interviewer: relevance, structure (STAR for "
    "behavioral questions), specifics and impact, clarity. Point out what worked and "
    "the most useful improvements, and list the key points a strong answer would "
    "cover, using the candidate's resume where it helps. Never invent employers, "
    "achievements or numbers that are not in the resume or the answer — write a "
    "placeholder such as [X%] instead. Write to the candidate as 'you'. Keep every "
    "item to one short sentence.\n"
    'Reply with JSON only: {"score":7,"summary":"...","strengths":["..."],'
    '"improvements":["..."],"sampleAnswer":["..."]}'
)

# Built-in questions for when no model is available. {role} is filled in.
_BANK: dict[str, list[tuple[str, str]]] = {
    "behavioral": [
        ("Tell me about a time you had a disagreement with a teammate. How did you resolve it?",
         "A specific situation, what you did, and a positive outcome."),
        ("Describe a project that did not go as planned. What did you learn?",
         "Ownership of the problem and a concrete lesson you applied later."),
        ("Tell me about a time you had to deliver under a tight deadline.",
         "How you prioritised, communicated and what the result was."),
        ("Give an example of when you took the lead without being asked.",
         "Initiative, the action you took and measurable impact."),
        ("Describe a time you received critical feedback. What did you do with it?",
         "Openness to feedback and a clear change in how you worked."),
    ],
    "technical": [
        ("Walk me through the most technically challenging project on your resume.",
         "The problem, your design choices, trade-offs and the result."),
        ("How would you debug a feature that works locally but fails in production?",
         "A systematic approach: logs, reproduction, isolating the cause."),
        ("Which skills from the job description are you strongest in, and how have you used them?",
         "Concrete examples that map your experience to the role."),
        ("How do you make sure the code or work you deliver is reliable?",
         "Testing, reviews, monitoring and how you handle mistakes."),
        ("Explain a technical concept from your field as if to a non-technical colleague.",
         "Clarity, a simple analogy and checking for understanding."),
    ],
    "hr": [
        ("Tell me about yourself.",
         "A short story: current role, key strengths and why this {role} role."),
        ("Why do you want to work as a {role} here?",
         "Genuine motivation tied to the role and the company."),
        ("What are your biggest strengths and one area you are improving?",
         "Honest strengths with proof, and a real weakness with a plan."),
        ("Where do you see yourself in three years?",
         "Ambition that fits the role and shows commitment."),
        ("Why should we choose you for this role?",
         "Your best two or three reasons, backed by examples."),
    ],
}

_STAR_WORDS = {
    "situation": ("situation", "when i", "at my", "in my previous", "context"),
    "task": ("task", "goal", "responsible", "needed to", "had to"),
    "action": ("i did", "i built", "i led", "i decided", "i worked", "i created", "i implemented", "action"),
    "result": ("result", "outcome", "improved", "reduced", "increased", "saved", "achieved", "%"),
}


class MockInterviewService:
    """One mock-interview request (questions or feedback) with usage telemetry."""

    def __init__(self, provider_factory: ProviderFactory | None = None) -> None:
        self._provider_factory = provider_factory
        self.last_provider: str = ""
        self.last_model: str = ""
        self.last_usage: dict = {}

    # ------------------------------------------------------------ questions

    async def questions(
        self,
        role: str,
        job_description: str = "",
        resume: str = "",
        interview_type: str = "mixed",
        difficulty: str = "medium",
        count: int = 5,
    ) -> tuple[list[MockQuestion], str]:
        """Practice questions for ``role``. Returns (questions, source)."""
        role = _clean(role) or "this role"
        count = max(1, min(int(count), 10))
        user = (
            f"Role: {role}\n"
            f"Interview type: {interview_type}"
            f"{' (mix technical, behavioral and HR)' if interview_type == 'mixed' else ''}\n"
            f"Difficulty: {difficulty}\n"
            f"Number of questions: {count}\n\n"
            f"Job description:\n{(job_description or '')[:CONTEXT_CHARS] or '(not provided)'}\n\n"
            f"Resume:\n{(resume or '')[:CONTEXT_CHARS] or '(not provided)'}"
        )
        data = await self._ask(QUESTIONS_SYSTEM, user, max_tokens=300 + 120 * count)
        items = data.get("questions") if isinstance(data, dict) else None
        out: list[MockQuestion] = []
        seen: set[str] = set()
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            text = _clean(item.get("question"))[:TEXT_LIMIT]
            key = text.lower()
            if not text or key in seen:
                continue
            seen.add(key)
            qtype = str(item.get("type", "")).lower()
            if qtype not in QUESTION_TYPES:
                qtype = interview_type if interview_type in QUESTION_TYPES else "technical"
            out.append(MockQuestion(
                id=len(out) + 1, question=text, type=qtype,
                focus=_clean(item.get("focus"))[:TEXT_LIMIT],
            ))
            if len(out) == count:
                break
        if out:
            return out, "ai"
        return _bank_questions(role, interview_type, count), "basic"

    # ------------------------------------------------------------- feedback

    async def feedback(
        self,
        question: str,
        answer: str,
        question_type: str = "technical",
        role: str = "",
        job_description: str = "",
        resume: str = "",
    ) -> MockFeedback:
        """Score and coach one practice answer."""
        user = (
            f"Role: {_clean(role) or '(not provided)'}\n"
            f"Question type: {question_type}\n"
            f"Question: {_clean(question)}\n\n"
            f"Candidate's answer:\n{answer.strip()}\n\n"
            f"Job description:\n{(job_description or '')[:CONTEXT_CHARS] or '(not provided)'}\n\n"
            f"Resume:\n{(resume or '')[:CONTEXT_CHARS] or '(not provided)'}"
        )
        data = await self._ask(FEEDBACK_SYSTEM, user, max_tokens=900)
        parsed = _parse_feedback(data)
        if parsed is not None:
            return parsed
        return basic_feedback(question, answer, question_type)

    # -------------------------------------------------------------- helpers

    async def _ask(self, system: str, user: str, max_tokens: int) -> dict:
        """One JSON call to the default model; {} when none is usable."""
        factory = self._provider_factory or get_llm_provider
        provider = factory(None)
        self.last_provider = getattr(provider, "name", "") or type(provider).__name__
        self.last_model = getattr(provider, "model", "") or ""
        try:
            data = await provider.complete_json(system, user, max_tokens=max_tokens)
        except NotImplementedError:
            data = {}  # no model configured (demo mode)
        except LLMQuotaError as exc:
            logger.warning("Mock interview AI unavailable: %s", str(exc)[:200])
            data = {}
        self.last_usage = {
            "latency_ms": getattr(provider, "last_latency_ms", None),
            "input_tokens": getattr(provider, "last_input_tokens", None),
            "output_tokens": getattr(provider, "last_output_tokens", None),
            "error_category": getattr(provider, "last_error_category", None),
        }
        return data if isinstance(data, dict) else {}


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _str_list(value: object) -> list[str]:
    items = value if isinstance(value, list) else []
    return [s for s in (_clean(v)[:TEXT_LIMIT] for v in items) if s][:LIST_LIMIT]


def _parse_feedback(data: dict) -> MockFeedback | None:
    if not data:
        return None
    try:
        score = int(round(float(data.get("score"))))
    except (TypeError, ValueError):
        return None
    summary = _clean(data.get("summary"))[:TEXT_LIMIT]
    improvements = _str_list(data.get("improvements"))
    if not summary and not improvements:
        return None
    return MockFeedback(
        score=max(1, min(score, 10)),
        summary=summary or "Here is how your answer would land.",
        strengths=_str_list(data.get("strengths")),
        improvements=improvements,
        sampleAnswer=_str_list(data.get("sampleAnswer")),
        source="ai",
    )


def _bank_questions(role: str, interview_type: str, count: int) -> list[MockQuestion]:
    if interview_type in _BANK:
        pool = [(interview_type, q) for q in _BANK[interview_type]]
    else:  # mixed: alternate HR, technical, behavioral
        order = ("hr", "technical", "behavioral")
        pool = [
            (t, _BANK[t][i])
            for i in range(max(len(v) for v in _BANK.values()))
            for t in order
            if i < len(_BANK[t])
        ]
    return [
        MockQuestion(
            id=i + 1, question=q.format(role=role), type=t, focus=focus.format(role=role)
        )
        for i, (t, (q, focus)) in enumerate(pool[:count])
    ]


def basic_feedback(question: str, answer: str, question_type: str) -> MockFeedback:
    """Rule-based feedback used when no AI model is available."""
    text = answer.strip()
    low = text.lower()
    words = len(text.split())
    has_numbers = bool(re.search(r"\d", text))
    strengths: list[str] = []
    improvements: list[str] = []
    score = 5

    if words < 25:
        score -= 2
        improvements.append("Your answer is very short — aim for 60–150 words with one concrete example.")
    elif words > 250:
        score -= 1
        improvements.append("Your answer is long — lead with the main point and trim the details.")
    else:
        score += 1
        strengths.append("Good length for a spoken answer.")

    if has_numbers:
        score += 1
        strengths.append("You used numbers, which makes your impact concrete.")
    else:
        improvements.append("Add a measurable result (time saved, %, users, revenue).")

    if question_type == "behavioral":
        present = [k for k, cues in _STAR_WORDS.items() if any(c in low for c in cues)]
        missing = [k for k in _STAR_WORDS if k not in present]
        if len(present) >= 3:
            score += 2
            strengths.append("Your answer follows the STAR structure.")
        else:
            improvements.append(
                "Structure it with STAR — you could make the " + ", ".join(missing) + " clearer."
            )
    elif " i " in f" {low} " or low.startswith("i "):
        score += 1
        strengths.append("You described your own contribution.")
    else:
        improvements.append("Say what YOU did, not only what the team did.")

    return MockFeedback(
        score=max(1, min(score, 10)),
        summary="Basic feedback (AI review is unavailable right now): checked length, structure and specifics.",
        strengths=strengths[:LIST_LIMIT],
        improvements=improvements[:LIST_LIMIT] or ["Practise saying it out loud and keep it under two minutes."],
        sampleAnswer=[],
        source="basic",
    )
