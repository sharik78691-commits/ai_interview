"""LLM providers: rule-based mock + OpenAI-compatible HTTP provider."""
import json
import logging
import re

import httpx

from app.core.config import get_settings
from app.models.ai_response import AIInterviewResponse
from app.providers.llm.base import LLMProvider

logger = logging.getLogger(__name__)

BASE_SCHEMA = """{"question": str, "questionType": "technical|behavioral|coding|general", "answerPoints": [str, ...non-empty], "star": {"situation": str, "task": str, "action": str, "result": str} | null, "codeHint": str | null, "example": str | null, "keyTakeaways": [str], "followUpQuestions": [str], "responseLength": "short|medium|long"}"""

LENGTH_RULES: dict[str, str] = {
    "short": """- answerPoints: 3-4 SHORT bullets (max ~12 words each). Rapid-fire mode.
- example: null. keyTakeaways: 1-2 items. followUpQuestions: 1-2 items.
- The candidate needs a quick mental cue, not an essay.""",
    "medium": """- answerPoints: 5-7 bullets, each 1-2 sentences (15-35 words). Structure the answer clearly.
- example: 1 short concrete example (2-3 sentences) grounded in the candidate's stack, or null.
- keyTakeaways: 2-3 items. followUpQuestions: 2-3 items.""",
    "long": """You are acting as a SENIOR ENGINEER / SENIOR ANALYST mentoring the candidate. Be thorough and specific.
- answerPoints: 8-12 bullets grouped in this logical order:
  1) Definition & core concept
  2) How it actually works (mechanism, internals)
  3) Trade-offs, failure modes, and when NOT to use it
  4) Comparison with alternatives
  5) Real production relevance tied to the candidate's resume and the job description
- Each bullet: 20-45 words with concrete technical substance (class names, patterns, protocols, metrics). No filler like "explain it clearly".
- example: a DETAILED real-world scenario (4-6 sentences) referencing services, scale, and outcomes.
- keyTakeaways: 3-5 crisp one-liners.
- followUpQuestions: 3-5 sharp interviewer probes at increasing depth.""",
}

COMMON_RULES = """RULES:
- Respond with STRICT JSON only. No markdown fences, no preamble, no commentary.
- Every bullet must be tailored to the candidate's ACTUAL resume experience and the target job description. Reference their real stack (languages, frameworks, cloud, domains) explicitly.
- Include "star" only for behavioral questions. Include "codeHint" only for coding questions.
- Never invent facts about the candidate that are not in the resume; speak in guidance terms ("frame it around your X experience").
- Set responseLength to the requested level."""


def build_system_prompt(response_length: str = "medium") -> str:
    level = response_length if response_length in LENGTH_RULES else "medium"
    return (
        "You are an AI interview coach. Given the candidate resume, job description, "
        "and interviewer question, respond with STRICT JSON only matching this schema:\n"
        f"{BASE_SCHEMA}\n\n"
        f"DEPTH LEVEL: {level.upper()}\n"
        f"{LENGTH_RULES[level]}\n\n"
        f"{COMMON_RULES}"
    )


# Kept for backwards compatibility with existing imports/tests.
SYSTEM_PROMPT = build_system_prompt("medium")

BEHAVIORAL_KEYWORDS = [
    "tell me about", "challenge", "conflict", "difficult", "mistake",
    "failure", "team", "led", "leadership", "disagree", "pressure",
    "deadline", "strength", "weakness", "yourself", "star", "situation",
    "accomplishment", "proud",
]

CODING_KEYWORDS = [
    "algorithm", "code", "coding", "function", "implement", "complexity",
    "data structure", "leetcode", "reverse", "sort", "binary", "tree",
    "graph", "dynamic programming", "recursion", "debug", "big o",
]


TECHNICAL_STARTERS = (
    "who", "what", "when", "where", "why", "how", "which",
    "can", "could", "would", "should", "is", "are", "do", "does",
    "did", "will", "have", "has", "explain", "describe", "tell",
)


def _classify(question: str) -> str:
    q = question.lower().strip()
    if any(k in q for k in CODING_KEYWORDS):
        return "coding"
    if any(k in q for k in BEHAVIORAL_KEYWORDS):
        return "behavioral"
    words = q.split()
    first = words[0].strip("',\"") if words else ""
    # Short factual questions ("what is Java", "is Java free") are technical,
    # not general — they deserve a definition-style answer, not filler.
    if first in TECHNICAL_STARTERS:
        return "technical"
    if len(words) <= 6 and not q.endswith("?"):
        return "general"
    return "technical"


def _extract_resume_skills(resume: str) -> list[str]:
    known = [
        "python", "java", "spring", "fastapi", "react", "angular", "typescript",
        "javascript", "sql", "aws", "docker", "kubernetes", "machine learning",
        "django", "node", "c++", "go", "rust", "azure", "gcp", "redis",
    ]
    low = resume.lower()
    return [s for s in known if s in low]


def _strip_code_fences(content: str) -> str:
    content = content.strip()
    match = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
    if match:
        return match.group(1).strip()
    return content


def _extract_json(content: str) -> dict:
    """Parse the model's reply into a dict, tolerating fences and preamble text."""
    text = _strip_code_fences(content)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Some models prefix reasoning or a sentence before the JSON object.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError(f"no JSON object in LLM response: {text[:120]}")


class MockLLMProvider(LLMProvider):
    name = "mock"

    async def analyze_question(
        self,
        resume: str,
        job_description: str,
        question: str,
        response_length: str = "medium",
    ) -> AIInterviewResponse:
        level = response_length if response_length in LENGTH_RULES else "medium"
        qtype = _classify(question)
        skills = _extract_resume_skills(resume or "")
        skill_txt = ", ".join(skills[:5]) if skills else "your background"
        job_snippet = (job_description or "").strip().replace("\n", " ")[:160]

        if qtype == "behavioral":
            core = [
                f"Frame your answer with the STAR method, drawing on {skill_txt}.",
                "Quantify impact: scope, actions you owned, measurable outcome.",
                "Close by linking the lesson learned to this role's needs.",
            ]
            star = {
                "situation": "Briefly set the context (team, project, stakes).",
                "task": "State your specific responsibility.",
                "action": "Describe 2-3 concrete steps you took.",
                "result": "Share the measurable result and what you learned.",
            }
            follows = [
                "What would you do differently next time?",
                "How did the team respond to your approach?",
            ]
            extra = [
                "Name the stakeholders involved and how you kept them informed.",
                "Explain what made the situation difficult (ambiguity, scale, deadline).",
                "Show your decision criteria: options considered and why you chose yours.",
            ]
            example = (
                "Production incident on a payment service: error rate spiked after a "
                f"config rollout in your {skill_txt} stack. You triaged with dashboards "
                "and distributed tracing, identified the root cause, rolled back, then "
                "added canary deploys and an SLO alert to prevent recurrence."
            )
        elif qtype == "coding":
            core = [
                "Clarify inputs, outputs, constraints, and edge cases first.",
                "State a brute-force baseline then optimize; discuss time/space complexity.",
                f"Leverage {skill_txt} idioms while writing clean, testable code.",
            ]
            star = None
            follows = [
                "What is the time and space complexity?",
                "How would you handle very large inputs?",
            ]
            extra = [
                "Discuss the data structure choice and why it beats the alternatives.",
                "Handle duplicate/edge inputs explicitly before optimizing.",
                "Mention how you would unit test this and what you'd assert.",
            ]
            example = None
            code_hint = (
                "Pseudo: def solve(...):  # handle empty input, then iterate; "
                "track best with hash map O(n)."
            )
        elif qtype == "general":
            core = [
                "Give a concise 30-60 second answer tied to the role.",
                f"Reference relevant strengths from {skill_txt}.",
                "End with enthusiasm for the position.",
            ]
            star = None
            follows = ["What interests you most about this role?"]
            extra = [
                "Bridge your background to the job's core requirement in one line.",
                "Mention one measurable outcome that proves the skill.",
            ]
            example = None
            code_hint = None
        else:  # technical
            core = [
                "Start with a one-sentence definition, then go one level deeper.",
                f"Ground your answer in hands-on experience with {skill_txt}.",
                (
                    f"Relate your answer to the job: {job_snippet}..."
                    if job_snippet
                    else "Mention trade-offs and when you would choose alternatives."
                ),
            ]
            star = None
            follows = [
                "Can you describe a project where you applied this?",
                "What are the trade-offs of this approach?",
            ]
            extra = [
                "Explain the mechanism: components involved and the data flow.",
                "Call out failure modes, trade-offs, and when NOT to pick this.",
                "Compare against the most common alternative the interviewer has in mind.",
                f"Weave in your {skill_txt} experience so it sounds practiced, not recited.",
            ]
            example = (
                f"In a {skill_txt} based platform you applied this to a real workload, "
                "measured the outcome, and improved reliability or throughput. Describe "
                "the scale you handled and the numbers you moved."
            )
            code_hint = None

        # Scale the answer to the requested depth.
        points = list(core)
        if level == "long":
            points += extra
            points.append(
                "Summarize in one sentence a senior engineer would say to close the answer."
            )
        elif level == "medium":
            points += extra[:1]

        def _first_sentence(text: str) -> str:
            head = text.split(". ")[0].rstrip(".")
            return f"{head}." if head else ""

        takeaways: list[str] = []
        if level != "short":
            source = (core + extra)[: 2 if level == "medium" else 4]
            takeaways = [s for s in (_first_sentence(p) for p in source) if s]

        if level == "long":
            follow_ups = follows + [
                "How would this change at 10x the current scale?",
                "What would make you abandon this approach?",
            ]
        elif level == "medium":
            follow_ups = follows
        else:
            follow_ups = follows[:1]

        return AIInterviewResponse(
            question=question,
            questionType=qtype,
            answerPoints=points,
            star=star,
            codeHint=code_hint if qtype == "coding" else None,
            example=example if level != "short" else None,
            keyTakeaways=takeaways,
            followUpQuestions=follow_ups,
            responseLength=level,
        )


class LLMQuotaError(RuntimeError):
    """Raised when the provider refuses the call due to quota/rate limits."""


class OpenAICompatibleLLMProvider(LLMProvider):
    name = "openai-compatible"

    # Retry 429/5xx a couple of times before falling back to templates.
    max_retries = 2

    def __init__(self, api_key: str = "", base_url: str = "", model: str = "") -> None:
        settings = get_settings()
        self.api_key = api_key or settings.llm_api_key
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.llm_model
        self._mock = MockLLMProvider()

    @staticmethod
    def _friendly_error(status: int, body: str, model: str = "") -> str:
        detail = body[:300]
        low = body.lower()
        if "insufficient_quota" in low or "billing" in low:
            return (
                "LLM quota exhausted — add credits/billing to your provider account "
                f"(HTTP {status}). {detail}"
            )
        if status == 429:
            return (
                "LLM rate limit reached — too many requests per minute. "
                f"Wait a moment and ask again (HTTP {status}). {detail}"
            )
        if status == 401 or status == 403:
            return "LLM authentication failed — check LLM_API_KEY in backend/.env."
        if status == 404:
            hint = f" Check LLM_MODEL in backend/.env (currently '{model}')." if model else ""
            if "model" in low or "decommission" in low or "not found" in low:
                return (
                    f"LLM model '{model}' was not found — it may have been retired by "
                    f"the provider.{hint} {detail}"
                )
            return (
                f"LLM endpoint not found (HTTP 404). Check LLM_BASE_URL.{hint} {detail}"
            )
        return f"LLM request failed (HTTP {status}). {detail}"

    async def analyze_question(
        self,
        resume: str,
        job_description: str,
        question: str,
        response_length: str = "medium",
    ) -> AIInterviewResponse:
        import asyncio
        import time

        level = response_length if response_length in LENGTH_RULES else "medium"
        payload = {
            "model": self.model,
            # Longer answers benefit from a bit more creativity in phrasing.
            "temperature": 0.6 if level == "long" else 0.4,
            "messages": [
                {"role": "system", "content": build_system_prompt(level)},
                {
                    "role": "user",
                    "content": (
                        f"Desired response length: {level.upper()}\n\n"
                        f"Resume:\n{resume[:4000]}\n\n"
                        f"Job description:\n{job_description[:4000]}\n\n"
                        f"Interviewer question:\n{question}"
                    ),
                },
            ],
        }
        # Token budgets — lowered so "long" still answers deeply but completes
        # in a reasonable time on real providers (Groq, OpenAI, etc.).
        max_tokens = {"short": 700, "medium": 1400, "long": 2000}.get(level, 1400)
        logger.info(
            "LLM request: model=%s level=%s max_tokens=%d timeout=15s qlen=%d",
            self.model, level, max_tokens, len(question)
        )
        t0 = time.perf_counter()
        try:
            # Total per-attempt timeout. 30s felt like "stuck UI"; 15s keeps it
            # snappy while still allowing big completions.
            async with httpx.AsyncClient(timeout=15.0) as client:
                last_exc: Exception | None = None
                for attempt in range(self.max_retries + 1):
                    try:
                        resp = await client.post(
                            f"{self.base_url}/chat/completions",
                            headers={"Authorization": f"Bearer {self.api_key}"},
                            json={**payload, "max_tokens": max_tokens},
                        )
                        if resp.status_code >= 400:
                            raise httpx.HTTPStatusError(
                                f"HTTP {resp.status_code}",
                                request=resp.request,
                                response=resp,
                            )
                        choice = resp.json()["choices"][0]
                        message = choice.get("message") or {}
                        content = (message.get("content") or "").strip()
                        # Reasoning-capable models can return empty content
                        # when they spend the whole budget thinking.
                        if not content:
                            if attempt < self.max_retries:
                                logger.warning(
                                    "LLM returned empty content — retrying with more budget"
                                )
                                max_tokens = int(max_tokens * 1.5)
                                payload["messages"][0]["content"] += (
                                    "\nRespond with JSON only. Do not add reasoning text."
                                )
                                await asyncio.sleep(0.5)
                                continue
                            raise ValueError("LLM returned an empty response")
                        data = _extract_json(content)
                        # Trust our request over the model's self-report so the
                        # UI always reflects the level the user actually chose.
                        data["responseLength"] = level
                        logger.info("LLM ok: %.2fs, %d tokens, %d points",
                                    time.perf_counter() - t0, max_tokens, len(data.get("answerPoints", [])))
                        return AIInterviewResponse(**data)
                    except httpx.HTTPStatusError as exc:
                        last_exc = exc
                        status = exc.response.status_code
                        body = exc.response.text or ""
                        # Retry transient limits/server errors only.
                        if status in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                            wait = 1.5 * (2**attempt)
                            retry_after = exc.response.headers.get("retry-after")
                            if retry_after and retry_after.isdigit():
                                wait = min(float(retry_after), 10.0)
                            logger.warning(
                                "LLM %s (%.2fs) — retrying in %.1fs (attempt %d/%d)",
                                status, time.perf_counter() - t0, wait, attempt + 1, self.max_retries,
                            )
                            await asyncio.sleep(wait)
                            continue
                        raise LLMQuotaError(
                            self._friendly_error(status, body, self.model)
                        ) from exc
                    except (httpx.TimeoutException, httpx.RequestError) as exc:
                        last_exc = exc
                        if attempt < self.max_retries:
                            logger.warning("LLM network error (%s) — retrying", exc)
                            await asyncio.sleep(1.5 * (2**attempt))
                            continue
                        raise LLMQuotaError("Unable to reach the AI service.") from exc
                raise LLMQuotaError(
                    self._friendly_error(429, str(last_exc), self.model)
                )
        except LLMQuotaError:
            raise  # caller decides: surface a message or fall back
        except Exception as exc:
            # Malformed JSON / schema mismatch: safe to fall back silently.
            logger.warning("LLM response unusable (%.2fs), falling back to mock: %s",
                           time.perf_counter() - t0, exc)
            return await self._mock.analyze_question(resume, job_description, question)


def get_llm_provider() -> LLMProvider:
    settings = get_settings()
    if settings.llm_api_key:
        return OpenAICompatibleLLMProvider()
    return MockLLMProvider()
