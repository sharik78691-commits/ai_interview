"""LLM providers: rule-based mock + OpenAI-compatible HTTP provider."""
import json
import logging
import re
from collections.abc import Awaitable, Callable

import httpx

from app.core.config import get_settings
from app.core.http import shared_http
from app.models.ai_response import AIInterviewResponse, ComparisonTable
from app.providers.llm.base import LLMProvider

logger = logging.getLogger(__name__)

BASE_SCHEMA = """{"question": str, "questionType": "technical|behavioral|coding|general", "answerPoints": [str, ...non-empty], "star": {"situation": str, "task": str, "action": str, "result": str} | null, "codeHint": str | null, "example": str | null, "keyTakeaways": [str], "followUpQuestions": [str], "responseLength": "short|medium|long", "comparison": {"headers": [str, ...], "rows": [[str, ...]]} | null}"""

LENGTH_RULES: dict[str, str] = {
    "short": """- answerPoints: 3-4 SHORT bullets (max ~12 words each). Quick mental cue, not an essay.
- example: null. keyTakeaways: 1-2 items. followUpQuestions: 1-2 items.
- CODING: codeHint still holds the COMPLETE runnable solution (see CODE RULES).""",
    "medium": """- answerPoints: 5-7 bullets, 1-2 sentences each (15-35 words).
- example: 1 short concrete example (2-3 sentences) from the candidate's stack, or null.
- keyTakeaways: 2-3 items. followUpQuestions: 2-3 items.
- CODING: codeHint holds the COMPLETE runnable solution (see CODE RULES).""",
    "long": """You are a SENIOR ENGINEER / SENIOR ANALYST mentoring the candidate. Be thorough and specific.
- answerPoints: 8-12 bullets (20-45 words each), ordered: 1) definition & core concept 2) mechanism/internals 3) trade-offs, failure modes, when NOT to use it 4) comparison with alternatives 5) production relevance tied to resume + job description. Concrete substance (class names, patterns, protocols, metrics) — no filler.
- example: DETAILED real-world scenario (4-6 sentences) with services, scale, outcomes.
- keyTakeaways: 3-5 crisp one-liners. followUpQuestions: 3-5 sharp probes at increasing depth.
- CODING: codeHint holds the COMPLETE runnable solution (see CODE RULES).""",
}

# Output token caps per depth. These are CAPS, not targets — the model stops
# when done, so a higher cap only adds headroom for reasoning models (gpt-oss)
# to finish their thought AND emit the JSON without getting truncated empty.
MAX_TOKENS: dict[str, int] = {"short": 700, "medium": 2000, "long": 3000}

COMMON_RULES = """RULES:
- STRICT JSON only. No fences, preamble, or commentary.
- Tailor every bullet to the candidate's ACTUAL resume + the job description; name their real stack.
- "star" for behavioral only. "codeHint" for coding only — and for coding it is NEVER null, at ANY depth.
- Never invent resume facts; advise ("frame it around your X experience").
- Set responseLength to the requested level.

COMPARISON TABLE (difference / compare / contrast / vs questions, EVERY depth):
- When the interviewer asks to compare, differentiate, or contrast two or more things, set "comparison" to a side-by-side table.
- headers: first column a short label like "Aspect", then one column per thing compared (e.g. ["Aspect", "X", "Y"]).
- rows: 4-6 rows, one per dimension (e.g. Definition, Key idea, Use case, Trade-offs, When to pick). Keep each cell 3-12 words, scannable.
- Keep answerPoints as the spoken summary; the table is the visual aid. For non-comparison questions set "comparison": null.
- Inside string values you MAY mark key terms with **double asterisks** and identifiers with `backticks` — they are plain characters and still valid JSON.

HUMAN VOICE (sound like a person, not an AI):
- Every bullet must be SAY OUT LOUD ready: plain spoken English, contractions OK, concrete verbs, real numbers/tools from the resume.
- Vary openers; no back-to-back repetition, no listicle filler ("Firstly... Secondly... In conclusion...").
- BANNED: "as an AI", "as a language model", "delve", "furthermore", "moreover", "in today's fast-paced", "landscape", "tapestry", "game-changer", "cutting-edge", "it's important to note", "in conclusion", "overall,". No hype adjectives ("crucial", "seamless") unless earned — name the service, metric, outcome.
- "example" reads like a retold memory ("we hit... so I..."), not a textbook case.

CODE RULES (coding questions, EVERY depth):
- codeHint = COMPLETE working solution to the exact problem — never pseudo-code or sketches.
- Runnable code in the best-fit language (resume's primary, else Python): full signature, algorithm body, one key-step comment, trailing time/space complexity ("# Time: O(n), Space: O(1)").
- Self-contained, copy-paste ready ("\\n" for line breaks). SHORT still ships complete code — only prose shrinks."""


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

COMPARISON_KEYWORDS = [
    "difference", "differences", "compare", "comparison", "contrast",
    "versus", " vs ", "distinguish", "differentiate", "pros and cons",
    "which is better", "what is the difference",
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


def _is_comparison(question: str) -> bool:
    """True when the question asks to compare / differentiate two+ things."""
    q = f" {question.lower().strip()} "
    return any(k in q for k in COMPARISON_KEYWORDS)


def _clean_term(s: str) -> str:
    return re.sub(r"[?.,!]+$", "", (s or "").strip()).strip() or "Option"


def _comparison_terms(question: str) -> tuple[str, str]:
    """Best-effort split of a comparison question into its two sides."""
    q = question.strip()
    m = re.search(r"difference\s+between\s+(.+?)\s+and\s+(.+)", q, re.IGNORECASE)
    if m:
        return _clean_term(m.group(1)), _clean_term(m.group(2))
    m = re.search(r"\b(?:compare|contrast)\s+(.+?)\s+(?:and|vs\.?|versus)\s+(.+)", q, re.IGNORECASE)
    if m:
        return _clean_term(m.group(1)), _clean_term(m.group(2))
    m = re.search(r"\b(.+?)\s+(?:vs\.?|versus)\s+(.+)", q, re.IGNORECASE)
    if m:
        return _clean_term(m.group(1)), _clean_term(m.group(2))
    return "Option A", "Option B"


def _comparison_table(question: str) -> ComparisonTable | None:
    """Build a side-by-side table for comparison questions (mock/demo)."""
    if not _is_comparison(question):
        return None
    a, b = _comparison_terms(question)
    return ComparisonTable(
        headers=["Aspect", a, b],
        rows=[
            ["Definition", f"What {a} is, in one line", f"What {b} is, in one line"],
            ["Key idea", "The core concept behind it", "The core concept behind it"],
            ["Use case", "When it fits best", "When it fits best"],
            ["Trade-offs", "Main cost or limitation", "Main cost or limitation"],
            ["When to pick", f"Choose {a} when…", f"Choose {b} when…"],
        ],
    )


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


def _repair_json(text: str) -> str:
    """Fix the most common LLM JSON slips (never drops content).

    Reasoning models producing a nested structure (like the comparison table)
    occasionally emit a missing or trailing comma, which strict ``json.loads``
    rejects outright. These are safe, structural fixes only:
    - trailing commas before ``}`` / ``]``
    - a missing comma where a ``}`` or ``]`` is immediately followed by a key.
    """
    text = re.sub(r",\s*([}\]])", r"\1", text)  # trailing comma
    text = re.sub(r"([}\]])\s*\"", r'\1, "', text)  # missing comma after }/]
    return text


def _extract_json(content: str) -> dict:
    """Parse the model's reply into a dict, tolerating fences, preamble text
    and the common missing/trailing-comma slips."""
    text = _strip_code_fences(content)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"no JSON object in LLM response: {text[:120]}")
    # Prefer the whole text (covers a clean object), then the substring.
    for candidate in (text, text[start : end + 1]):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    # Repair common comma slips and retry both shapes.
    for candidate in (_repair_json(text), _repair_json(text[start : end + 1])):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    raise ValueError(f"no valid JSON object in LLM response: {text[:160]}")


def _extract_answer_points(buffer: str) -> list[str]:
    """Completed `answerPoints` bullets inside a PARTIAL JSON stream.

    Scans for the `"answerPoints": [...]` array and returns every fully
    closed string element. An unterminated trailing string is ignored (more
    deltas are still coming). Returns [] when the key/array isn't there yet,
    so callers can simply diff by count and emit only new bullets.
    """
    key = '"answerPoints"'
    idx = buffer.find(key)
    if idx == -1:
        return []
    i = buffer.find("[", idx + len(key))
    if i == -1:
        return []
    i += 1
    points: list[str] = []
    n = len(buffer)
    while i < n:
        while i < n and buffer[i] in " \t\r\n,":
            i += 1
        if i >= n or buffer[i] == "]":
            break
        if buffer[i] != '"':
            i += 1  # tolerate unexpected chars; final validation is strict
            continue
        i += 1
        cur: list[str] = []
        closed = False
        while i < n:
            ch = buffer[i]
            if ch == "\\" and i + 1 < n:
                cur.append(ch)
                cur.append(buffer[i + 1])
                i += 2
                continue
            if ch == '"':
                closed = True
                i += 1
                break
            cur.append(ch)
            i += 1
        if not closed:
            break  # incomplete trailing string — wait for more deltas
        raw = '"' + "".join(cur) + '"'
        try:
            points.append(json.loads(raw))
        except json.JSONDecodeError:
            points.append("".join(cur))
    return points


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
                f"Open with the STAR shape: one line of context from your {skill_txt} work, then what you personally owned.",
                "Put a number on it — the scope, what you did, what moved because of it.",
                "Land it by tying what you learned to what this role actually needs.",
            ]
            star = {
                "situation": "Briefly set the context (team, project, stakes).",
                "task": "State your specific responsibility.",
                "action": "Describe 2-3 concrete steps you took.",
                "result": "Share the measurable result and what you learned.",
            }
            follows = [
                "What would you do differently if it happened again?",
                "How did the rest of the team react to your call?",
            ]
            extra = [
                "Name who was in the room and how you kept them posted — that's what makes the story believable.",
                "Say what actually made it hard: the ambiguity, the scale, or the deadline.",
                "Walk through the options you weighed and why you picked yours.",
            ]
            example = (
                "Think of it like the time a payment service started erroring right "
                f"after a config rollout in your {skill_txt} stack — you dug through "
                "dashboards and traces, found it, rolled back, then added canary "
                "deploys plus an SLO alert so it couldn't happen again."
            )
        elif qtype == "coding":
            core = [
                "Repeat the inputs, outputs, and edge cases back first — it buys thinking time and avoids wrong turns.",
                "Start with the straightforward approach, then sharpen it; call out time and space as you go.",
                f"Write it the way you would in your {skill_txt} day job — clean names, testable pieces.",
            ]
            star = None
            follows = [
                "What is the time and space complexity?",
                "How would you handle very large inputs?",
            ]
            extra = [
                "Say why this data structure beats the obvious alternatives.",
                "Call out duplicates and empty inputs before anyone has to ask.",
                "Mention the one unit test you'd write first and what it would assert.",
            ]
            example = None
            # A complete, runnable solution — not pseudo-code. The exact problem
            # varies, so this is a correct, self-contained template the candidate
            # can adapt, with complexity noted.
            code_hint = (
                "def solve(nums):\n"
                "    \"\"\"Return the answer for the given input.\"\"\"\n"
                "    if not nums:\n"
                "        return None  # handle the empty-input edge case\n"
                "    seen = {}  # value -> index, for O(1) lookups\n"
                "    for i, n in enumerate(nums):\n"
                "        if n in seen:\n"
                "            return [seen[n], i]\n"
                "        seen[n] = i\n"
                "    return None\n\n"
                "# Time: O(n)  Space: O(n)"
            )
        elif qtype == "general":
            core = [
                "Keep it to 30-60 seconds and aim it squarely at this role.",
                f"Borrow one genuine strength from your {skill_txt} background.",
                "Finish with why this position honestly interests you.",
            ]
            star = None
            follows = ["What interests you most about this role?"]
            extra = [
                "Bridge your background to the job's core need in a single line.",
                "Back one claim with a real number or outcome.",
            ]
            example = None
            code_hint = None
        else:  # technical
            core = [
                "Give the one-line definition first, then go one level deeper — that's the whole opener.",
                f"Tie each claim to something you've actually built with {skill_txt}.",
                (
                    f"Close the loop to the role: {job_snippet}..."
                    if job_snippet
                    else "Say where this breaks down and what you'd pick instead."
                ),
            ]
            star = None
            follows = [
                "Can you describe a project where you applied this?",
                "What are the trade-offs of this approach?",
            ]
            extra = [
                "Explain the moving parts and how data flows between them.",
                "Name the failure modes, and where you'd refuse to use this.",
                "Stack it against the alternative the interviewer is probably thinking of.",
                f"Drop in your {skill_txt} experience so it sounds lived-in, not rehearsed.",
            ]
            example = (
                f"Picture the {skill_txt} platform where you used this on a real workload, "
                "measured it, and moved reliability or throughput — tell them the scale "
                "you were at and the numbers that changed."
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
            comparison=_comparison_table(question),
        )


class LLMQuotaError(RuntimeError):
    """Raised when the provider refuses the call due to quota/rate limits."""


class OpenAICompatibleLLMProvider(LLMProvider):
    name = "openai-compatible"

    # Retry 429/5xx a couple of times before falling back to templates.
    max_retries = 2

    @staticmethod
    def _categorize_status(status: int, body: str = "") -> str:
        """Map an HTTP status (+body) to a stable, reportable error category."""
        low = body.lower()
        if "insufficient_quota" in low or "billing" in low:
            return "quota"
        if status == 429:
            return "rate_limit"
        if status in (401, 403):
            return "auth"
        if status == 404:
            return "not_found"
        if status == 408:
            return "timeout"
        if status >= 500:
            return "server"
        return f"http_{status}"

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        name: str = "openai-compatible",
    ) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.llm_api_key
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.llm_model
        self.name = name
        self._mock = MockLLMProvider()
        # Usage telemetry (filled after each call; read by AIService for tracking).
        self.last_latency_ms: float = 0.0
        self.last_input_tokens: int | None = None
        self.last_output_tokens: int | None = None
        self.last_error_category: str | None = None
        # True when the latest streaming call emitted at least one delta
        # before finishing/failing (lets the caller pick single-shot retry).
        self.stream_had_deltas: bool = False

    def _headers(self) -> dict[str, str]:
        """Auth (+ attribution OpenRouter asks for on its endpoint only)."""
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if "openrouter" in self.base_url.lower():
            # OpenRouter credits/requests ranking uses these; other providers
            # ignore them, so they are sent only here.
            try:
                referer = get_settings().frontend_url
            except Exception:
                referer = ""
            if referer:
                headers["HTTP-Referer"] = referer
            headers["X-Title"] = "OyeInterview"
        return headers

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

    def _request_payload(
        self, level: str, resume: str, job_description: str, question: str
    ) -> tuple[dict, int]:
        """Chat-completions body + token budget shared by single-shot/stream."""
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
        # Shared caps (short answers never need a long tail).
        max_tokens = MAX_TOKENS.get(level, 1400)
        return payload, max_tokens

    async def analyze_question_stream(
        self,
        resume: str,
        job_description: str,
        question: str,
        response_length: str = "medium",
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> AIInterviewResponse:
        """SSE streaming variant: provisional bullets via ``on_delta``.

        Emits one completed ``answerPoints`` bullet per callback as the JSON
        streams in, then returns the same strictly-validated response as
        :meth:`analyze_question`. Retries only while nothing was emitted yet
        (a started stream can never resume) — after the first delta, any
        failure raises so the caller can single-shot retry or fall back.
        """
        import asyncio
        import time

        if on_delta is None:
            return await self.analyze_question(
                resume, job_description, question, response_length
            )
        level = response_length if response_length in LENGTH_RULES else "medium"
        payload, max_tokens = self._request_payload(
            level, resume, job_description, question
        )
        body = {**payload, "max_tokens": max_tokens, "stream": True}
        # NOTE: no stream_options.include_usage — tolerance on Gemini's
        # OpenAI-compatible endpoint is unverified, so streamed token counts
        # stay None (never estimated) instead of risking a rejected request.
        logger.info(
            "LLM stream request: model=%s level=%s max_tokens=%d qlen=%d",
            self.model, level, max_tokens, len(question)
        )
        t0 = time.perf_counter()
        self.stream_had_deltas = False
        self.last_input_tokens = None
        self.last_output_tokens = None
        timeout = httpx.Timeout(connect=15.0, read=60.0, write=15.0, pool=15.0)
        try:
            async with shared_http() as client:
                last_exc: Exception | None = None
                for attempt in range(self.max_retries + 1):
                    # A started stream can never resume — retry pre-delta only.
                    if attempt > 0 and self.stream_had_deltas:
                        break
                    try:
                        async with client.stream(
                            "POST",
                            f"{self.base_url}/chat/completions",
                            headers=self._headers(),
                            json=body,
                            timeout=timeout,
                        ) as resp:
                            if resp.status_code >= 400:
                                await resp.aread()
                                raise httpx.HTTPStatusError(
                                    f"HTTP {resp.status_code}",
                                    request=resp.request,
                                    response=resp,
                                )
                            buffer = ""
                            sent = 0
                            async for line in resp.aiter_lines():
                                if not line.startswith("data:"):
                                    continue
                                data = line[5:].strip()
                                if not data or data == "[DONE]":
                                    continue
                                try:
                                    chunk = json.loads(data)
                                except json.JSONDecodeError:
                                    continue
                                try:
                                    choices = chunk.get("choices") or [{}]
                                    delta = (
                                        choices[0].get("delta", {}).get("content")
                                        or ""
                                    )
                                except (AttributeError, IndexError, TypeError):
                                    continue
                                if not delta:
                                    continue
                                buffer += delta
                                for point in _extract_answer_points(buffer)[sent:]:
                                    sent += 1
                                    self.stream_had_deltas = True
                                    await on_delta(point)
                            content = buffer.strip()
                            self.last_latency_ms = (time.perf_counter() - t0) * 1000
                            self.last_error_category = "ok"
                            if not content:
                                raise ValueError("LLM returned an empty response")
                            data = _extract_json(content)
                            data.setdefault("question", question)
                            data["responseLength"] = level
                            logger.info(
                                "LLM stream ok: %.2fs, %d points",
                                time.perf_counter() - t0,
                                len(data.get("answerPoints", [])),
                            )
                            return AIInterviewResponse(**data)
                    except httpx.HTTPStatusError as exc:
                        last_exc = exc
                        status = exc.response.status_code
                        try:
                            err_body = exc.response.text or ""
                        except Exception:
                            err_body = ""
                        self.last_error_category = self._categorize_status(
                            status, err_body
                        )
                        self.last_latency_ms = (time.perf_counter() - t0) * 1000
                        if (
                            status in (429, 500, 502, 503, 504)
                            and attempt < self.max_retries
                            and not self.stream_had_deltas
                        ):
                            wait = 1.5 * (2**attempt)
                            logger.warning(
                                "LLM stream %s — retrying in %.1fs (attempt %d/%d)",
                                status, wait, attempt + 1, self.max_retries,
                            )
                            await asyncio.sleep(wait)
                            continue
                        raise LLMQuotaError(
                            self._friendly_error(status, err_body, self.model)
                        ) from exc
                    except (httpx.TimeoutException, httpx.RequestError) as exc:
                        last_exc = exc
                        self.last_error_category = (
                            "timeout"
                            if isinstance(exc, httpx.TimeoutException)
                            else "network"
                        )
                        self.last_latency_ms = (time.perf_counter() - t0) * 1000
                        if attempt < self.max_retries and not self.stream_had_deltas:
                            await asyncio.sleep(1.5 * (2**attempt))
                            continue
                        raise LLMQuotaError(
                            "Unable to reach the AI service."
                        ) from exc
                raise LLMQuotaError(
                    self._friendly_error(429, str(last_exc), self.model)
                )
        except LLMQuotaError:
            raise  # caller decides: single-shot retry or template fallback
        except Exception as exc:
            # Unusable stream content (empty or malformed JSON). One single-shot
            # retry often salvages a valid answer (the repair path in
            # _extract_json fixes common comma slips) — only then templates.
            self.last_error_category = "bad_response"
            self.last_latency_ms = (time.perf_counter() - t0) * 1000
            logger.warning(
                "LLM stream unusable (%.2fs), retrying single-shot: %s",
                time.perf_counter() - t0, exc,
            )
            try:
                return await self.analyze_question(
                    resume, job_description, question, response_length
                )
            except Exception:
                return await self._mock.analyze_question(resume, job_description, question)

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
        payload, max_tokens = self._request_payload(level, resume, job_description, question)
        logger.info(
            "LLM request: model=%s level=%s max_tokens=%d timeout=15s qlen=%d",
            self.model, level, max_tokens, len(question)
        )
        t0 = time.perf_counter()
        try:
            # Total per-attempt timeout. 30s felt like "stuck UI"; 15s keeps it
            # snappy while still allowing big completions. Connection reused
            # via the shared pool (no per-call TLS handshake).
            async with shared_http() as client:
                last_exc: Exception | None = None
                for attempt in range(self.max_retries + 1):
                    try:
                        resp = await client.post(
                            f"{self.base_url}/chat/completions",
                            headers=self._headers(),
                            json={**payload, "max_tokens": max_tokens},
                            timeout=15.0,
                        )
                        if resp.status_code >= 400:
                            raise httpx.HTTPStatusError(
                                f"HTTP {resp.status_code}",
                                request=resp.request,
                                response=resp,
                            )
                        body_json = resp.json()
                        choice = body_json["choices"][0]
                        message = choice.get("message") or {}
                        content = (message.get("content") or "").strip()
                        # Capture provider-reported usage (never fabricated —
                        # absent values stay None).
                        usage = (body_json.get("usage") or {})
                        self.last_input_tokens = usage.get("prompt_tokens")
                        self.last_output_tokens = usage.get("completion_tokens")
                        self.last_latency_ms = (time.perf_counter() - t0) * 1000
                        self.last_error_category = "ok"
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
                        # The backend asked the question, so it is the authority
                        # when the model doesn't echo it back verbatim.
                        data.setdefault("question", question)
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
                        self.last_error_category = self._categorize_status(status, body)
                        self.last_latency_ms = (time.perf_counter() - t0) * 1000
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
                        self.last_error_category = (
                            "timeout" if isinstance(exc, httpx.TimeoutException) else "network"
                        )
                        self.last_latency_ms = (time.perf_counter() - t0) * 1000
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
            # Malformed JSON / schema mismatch: safe to fall back to templates,
            # but record WHY so usage stats distinguish it from quota errors.
            self.last_error_category = "bad_response"
            self.last_latency_ms = (time.perf_counter() - t0) * 1000
            logger.warning("LLM response unusable (%.2fs), falling back to mock: %s",
                           time.perf_counter() - t0, exc)
            return await self._mock.analyze_question(resume, job_description, question)


def get_llm_provider() -> LLMProvider:
    settings = get_settings()
    if settings.llm_api_key:
        return OpenAICompatibleLLMProvider()
    return MockLLMProvider()
