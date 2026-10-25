"""LLM providers: rule-based mock + OpenAI-compatible HTTP provider."""
import json
import logging
import re
from collections.abc import Awaitable, Callable

import httpx

from app.core.config import get_settings
from app.core.http import shared_http
from app.models.ai_response import AIInterviewResponse, FollowUp
from app.providers.llm.base import LLMProvider

logger = logging.getLogger(__name__)

# Key order matters: "answerPoints" (the spoken Best Answer) comes right after
# the type so the live stream can show the provisional draft as early as before.
BASE_SCHEMA = """{"question": str, "questionType": "technical|behavioral|coding|general", "answerPoints": [str, ...non-empty], "simpleExplanation": str | null, "keyDetails": [str], "comparison": {"headers": [str], "rows": [[str]]} | null, "star": {"situation": str, "task": str, "action": str, "result": str} | null, "approach": str | null, "codeHint": str | null, "complexity": str | null, "edgeCases": [str], "example": str | null, "followUps": [], "responseLength": "default|short|medium|long"}"""

LENGTH_RULES: dict[str, str] = {
    "default": """COMPLETE COVERAGE: first decide what the interviewer is really testing, then make the card cover EVERY point they expect to hear — nothing important missing, no filler.
- answerPoints (Best Answer): 5-8 spoken sentences (12-35 words each) in speaking order that cover the full expected answer: definition / direct answer -> how it works -> key points or components -> benefits -> limitations / trade-offs -> when to use or avoid -> a practical touch from the resume. Skip a step only if the question genuinely does not need it.
- simpleExplanation: 1-2 plain-language sentences (null for HR / personal questions).
- keyDetails (WHAT THE INTERVIEWER EXPECTS): the COMPLETE checklist of expected points — every concept, step, term, benefit, limitation and trade-off an interviewer would tick off (typically 5-8 items, 5-15 words each). Do not leave an expected point out.
- example: 3-4 sentences, one realistic project scenario with outcome.
- Behavioral questions: complete STAR. Coding: complete runnable solution plus complexity and edge cases (see CODING CARD).""",
    "short": """- answerPoints (Best Answer): 2-3 short spoken sentences (max ~20 words each). Direct and confident.
- simpleExplanation: null, or ONE plain sentence if the topic is abstract.
- keyDetails: 0-3 items (3-10 words each). example: null.
- CODING: codeHint still holds a COMPLETE runnable solution (see CODING CARD).""",
    "medium": """- answerPoints (Best Answer): 3-5 spoken sentences (12-30 words each).
- simpleExplanation: 1-2 plain-language sentences, or null when the Best Answer is already simple (HR).
- keyDetails: 3-5 scannable items (5-15 words each). example: 2-3 sentences, or null.
- CODING: codeHint holds a COMPLETE runnable solution (see CODING CARD).""",
    "long": """You are a SENIOR ENGINEER / SENIOR ANALYST mentoring the candidate. Be thorough and specific.
- answerPoints (Best Answer): 5-8 spoken sentences (15-40 words each) a senior candidate would say: core idea -> how it works -> trade-off -> impact. Concrete substance (class names, patterns, protocols, metrics) — no filler.
- simpleExplanation: 2-3 plain-language sentences (an analogy is welcome).
- keyDetails: 5-8 items covering mechanism, benefits, limitations, trade-offs and when NOT to use it.
- example: DETAILED project scenario (4-6 sentences) with services, scale, outcomes.
- CODING: codeHint holds a COMPLETE runnable solution (see CODING CARD).""",
}

# Output token caps per depth. These are CAPS, not targets — the model stops
# when done, so a higher cap only adds headroom for reasoning models (gpt-oss)
# to finish their thought AND emit the JSON without getting truncated empty.
#
# `short` was 700, which is BELOW what a reasoning model needs just to think:
# probing the real providers showed openai/gpt-oss-120b and gemini-3.5-flash
# returning finish_reason="length" with UNPARSEABLE JSON at 700 (0/3 valid for
# gemini-3.5-flash, 0 content deltas for gpt-oss at 600). A truncated answer is
# worse than a slightly larger cap — it forces a whole extra request and often
# ends on the template fallback. Non-reasoning models stop on their own
# (finish_reason="stop"), so raising this does NOT make their answers longer:
# gemini-3.5-flash-lite took 1.56s at 700 and 1.61s at 1200, 4 bullets both.
MAX_TOKENS: dict[str, int] = {"short": 1400, "medium": 2000, "long": 3000, "default": 3000}

# Extra multiplier applied when a response came back truncated
# (finish_reason == "length") or empty, so the retry has real headroom instead
# of failing for the same reason twice.
BUDGET_LIFT_ON_TRUNCATION = 1.5

# Pre-delta transient-retry backoff (429/5xx/network). Kept small on purpose:
# during a LIVE interview the candidate is sitting in silence, so retrying in
# 0.5s instead of 1.5s/3s is worth the extra request — the retry only fires
# before any bullet was shown, i.e. when nothing was lost by asking again.
RETRY_BACKOFF_BASE = 0.5

COMMON_RULES = """RULES:
- STRICT JSON only. No fences, preamble, or commentary.
- Tailor the answer to the candidate's ACTUAL resume + the job description; name their real stack.
- Never invent resume facts; advise ("frame it around your X experience").
- Set responseLength to the requested level.
- Inside string values you MAY mark key terms with **double asterisks** and identifiers with `backticks` — they are plain characters and still valid JSON. Bold ONLY the 1-3 essential words per item, never whole sentences.

INTERVIEW ANSWER CARD (one adaptive card — fill ONLY the sections that help THIS question):
- answerPoints = BEST ANSWER: what the candidate says aloud, in natural first-person spoken English, one sentence per item, in speaking order. answerPoints[0] is the DIRECT answer — no warm-up, no restating the question. Concise for simple questions, fuller for conceptual / senior questions.
- MULTI-PART / LIST questions (core concepts, principles, pillars, properties, types, layers, steps — e.g. OOP, SOLID, ACID, SQL joins, HTTP methods): COVER EVERY MEMBER of the well-known set — never drop one (OOP = Encapsulation, Inheritance, Polymorphism AND Abstraction). answerPoints[0] names the set in one sentence ("Java has four core OOP concepts."), then exactly ONE item per member that STARTS with "**Member name:** " followed by a crisp definition plus how it is done in practice (keyword, annotation or short example). Members come before anything else; the item count may exceed the depth's sentence range so no member is missing.
- simpleExplanation = EXPLAIN IT SIMPLY: the idea in plain language for a quick understanding; briefly explain any technical term you must use. null when nothing needs simplifying.
- keyDetails = WHAT THE INTERVIEWER EXPECTS: the important concepts, process, benefits, limitations or trade-offs as short scannable fragments (not paragraphs). [] when not useful.
- comparison = QUICK REFERENCE TABLE, for MULTI-PART / LIST and COMPARE questions only: headers = the item column first, then 2-3 useful columns (e.g. ["Concept", "Example", "Key keyword"] or ["Aspect", "X", "Y"]); rows = one row per member / dimension, every cell short (max ~8 words; `code` allowed). null for single-concept, HR, behavioral and coding questions.
- example = PRACTICAL EXAMPLE: ONE realistic project/industry example the candidate can narrate credibly ("On our order service we..."), or null.
- followUps = ALWAYS [] in this card: "If they ask further" is generated separately, only when the candidate asks for it. Still keep deeper technical detail OUT of the Best Answer.
- star, approach, codeHint, complexity, edgeCases: null / [] unless the question type below asks for them.
- Do NOT force every section. Empty sections are better than filler.

ADAPT TO THE QUESTION TYPE (decide what the interviewer is really testing, then answer THAT):
- HR / personal ("tell me about yourself", strengths, why this role) -> questionType "general" (or "behavioral" for past-experience stories): a natural, personalized-sounding Best Answer built from the resume, 1-3 keyDetails at most, simpleExplanation null.
- THEORY / concept -> "technical": clarity first, simpleExplanation, keyDetails with trade-offs, a practical example. A multi-part concept (a known set of members) follows the MULTI-PART / LIST rule and fills "comparison".
- SCENARIO / behavioral ("tell me about a time", "what would you do if") -> "behavioral": Best Answer told as Situation -> Action -> Result with a quantified result; ALSO fill "star". simpleExplanation null.
- SYSTEM DESIGN -> "technical": Best Answer covers requirements -> components -> scaling -> trade-offs; keyDetails lists bottlenecks and choices.
- COMPARE / DIFFERENCE / VS -> "technical": Best Answer states the key difference first; keyDetails contrasts the dimensions as "**X**: ... vs **Y**: ..." and ends with when to pick each; "comparison" holds the side-by-side table (["Aspect", "X", "Y"]).
- CODING -> "coding": see CODING CARD.
- Be correct over impressive; if a resume fact is missing, advise how to frame it instead of inventing it.

FOLLOW-UPS (a "Conversation so far" block may precede the question):
- Interviewers often dig into the SAME or an EARLIER question ("why?", "what if it fails?", "and at 10x scale?", "give me an example", "what about the other approach?").
- If the new question depends on earlier ones, resolve every reference ("that", "it", "this approach") and set "question" to the FULL self-contained question (e.g. "Why did you pick Kafka over RabbitMQ for order events?").
- Answer ONLY the new angle, building on what was already advised: do not repeat earlier points, and stay consistent with the earlier stack, examples and numbers. A follow-up is usually narrower, so go deeper, not wider.
- If the new question is unrelated to the history, ignore the history.

HUMAN VOICE (sound like a person, not an AI):
- The Best Answer must be SAY OUT LOUD ready: plain spoken English, contractions OK, concrete verbs, real numbers/tools from the resume. Confident and practical — a well-prepared candidate talking, not AI notes.
- Vary openers; no back-to-back repetition, no listicle filler ("Firstly... Secondly... In conclusion...").
- BANNED: "as an AI", "as a language model", "delve", "furthermore", "moreover", "in today's fast-paced", "landscape", "tapestry", "game-changer", "cutting-edge", "it's important to note", "in conclusion", "overall,". No hype adjectives ("crucial", "seamless") unless earned — name the service, metric, outcome.
- "example" reads like a retold memory ("we hit... so I..."), not a textbook case.
- The whole card must be scannable in 10-15 seconds: no walls of text.

CODING CARD (coding questions, EVERY depth):
- approach = the reasoning FIRST, 1-3 sentences: brute force -> the insight -> the optimal idea.
- codeHint = THE PROGRAM (exactly ONE solution): COMPLETE working solution to the exact problem — never pseudo-code or sketches. Runnable code in the best-fit language (resume's primary, else Python): full signature, algorithm body, concise comments on the key steps. Self-contained, copy-paste ready ("\\n" for line breaks). codeHint is NEVER null for coding, at ANY depth — SHORT still ships complete code, only prose shrinks.
- complexity = "Time: O(..) — why. Space: O(..) — why."
- edgeCases = 2-4 short items (empty input, duplicates, overflow, ...).
- answerPoints = how to explain the solution in interview language while coding."""


# "If they ask further" is generated ON DEMAND (the candidate clicks "Show
# follow-ups"), not inside every answer: follow-ups were 15-37% of each
# answer's output tokens, so dropping them makes the main answer finish sooner
# and cost less. These are the per-depth follow-up rules that used to live in
# LENGTH_RULES, unchanged in substance.
FOLLOWUP_RULES: dict[str, str] = {
    "default": "3-4 likely next questions, each answered in 1-2 sentences.",
    "short": "1-2 items, each answer max ~15 words.",
    "medium": "2-3 items, each answer 1-2 sentences.",
    "long": "3-4 sharp probes at increasing depth; deeper technical detail goes HERE, each answer 1-3 sentences.",
}
FOLLOWUP_LIMIT: dict[str, int] = {"default": 4, "short": 2, "medium": 3, "long": 4}
# Caps, not targets (reasoning models need headroom to think AND emit JSON).
FOLLOWUP_MAX_TOKENS = 1400


def build_followup_prompt(response_length: str = "medium") -> str:
    level = response_length if response_length in FOLLOWUP_RULES else "medium"
    return (
        "You are an AI interview coach. The candidate has just been coached on an "
        "interview question (their Best Answer is given). Predict what THIS interviewer "
        "asks next — IF THEY ASK FURTHER — and give a short, strong answer to each.\n"
        'Respond with STRICT JSON only: {"followUps": [{"question": str, "answer": str}]}\n\n'
        f"DEPTH LEVEL: {level.upper()}\n- followUps: {FOLLOWUP_RULES[level]}\n\n"
        "RULES:\n"
        "- STRICT JSON only. No fences, preamble, or commentary.\n"
        "- Each question is a realistic probe on THIS topic (why / trade-offs / failure "
        "cases / scale / an example / the alternative), building on the Best Answer — "
        "never repeat what it already says. Deeper technical detail belongs here.\n"
        "- HR / personal questions: follow-ups focus on how to handle the probe.\n"
        "- Behavioral questions: probe the decisions, the candidate's own role and the "
        "measurable result.\n"
        "- Tailor answers to the candidate's ACTUAL resume + the job description; name "
        "their real stack. Never invent resume facts; advise how to frame them.\n"
        "- Answers are SAY OUT LOUD ready: natural first-person spoken English, concrete "
        "tools and numbers, no listicle filler. BANNED: \"as an AI\", \"delve\", "
        "\"furthermore\", \"moreover\", \"it's important to note\", \"in conclusion\".\n"
        "- You MAY mark 1-3 key terms per item with **double asterisks** and identifiers "
        "with `backticks`."
    )


def format_history(history: list[dict] | None) -> str:
    """Render prior Q&A turns as a compact block (empty string when none).

    Lets the model resolve follow-ups ("why?", "and at 10x scale?") against what
    the interviewer already asked and what the candidate was already advised.
    """
    if not history:
        return ""
    lines = ["Conversation so far (oldest first):"]
    for i, turn in enumerate(history, 1):
        lines.append(f"Q{i}: {str(turn.get('question', ''))[:300]}")
        points = [str(p)[:200] for p in (turn.get("points") or [])[:3]]
        if points:
            lines.append("Advised: " + " | ".join(points))
    return "\n".join(lines) + "\n\n"


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


def _comparison_details(question: str) -> list[str]:
    """Contrast lines for comparison questions (mock/demo) — the card's Key
    Details carry the difference instead of a separate table."""
    if not _is_comparison(question):
        return []
    a, b = _comparison_terms(question)
    return [
        f"**Core idea** — {a}: what it is in one line vs {b}: what it is in one line",
        f"**Best fit** — where {a} shines vs where {b} shines",
        "**Trade-off** — the main cost or limitation of each",
        f"**When to pick** — choose {a} when…, {b} when…",
    ]


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
        history: list[dict] | None = None,
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
                ("What would you do differently if it happened again?",
                 "Name one concrete change — earlier alerting, a smaller rollout — and why it matters."),
                ("How did the rest of the team react to your call?",
                 "Be honest about the pushback, then show how you brought them along."),
            ]
            simple = None
            details = [
                "**Ownership** — what *you* did, not the team",
                "**Numbers** — scope, impact, time saved",
                "**Reflection** — what you learned and changed",
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
                ("What is the time and space complexity?",
                 "One pass with O(1) lookups, so O(n) time and O(n) extra space."),
                ("How would you handle very large inputs?",
                 "Stream the input, or sort and use two pointers to drop the extra memory."),
            ]
            simple = None
            details = []
            approach = (
                "Brute force checks every pair in O(n²). The insight: remember what you've "
                "already seen in a hash map, so each lookup is O(1) and one pass is enough."
            )
            complexity = "Time: O(n) — single pass. Space: O(n) — the hash map."
            edge_cases = [
                "Empty or single-element input",
                "Duplicate values",
                "No valid answer exists",
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
            follows = [
                ("What interests you most about this role?",
                 "Point to one thing in the job description that matches your best work."),
            ]
            simple = None
            details = [
                "**Relevance** — tie it to this role",
                "**Proof** — one real number or outcome",
            ]
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
                ("Can you describe a project where you applied this?",
                 f"Pick one {skill_txt} project: the problem, what you built, the result."),
                ("What are the trade-offs of this approach?",
                 "Name the cost — complexity, latency or consistency — and when it's worth it."),
            ]
            simple = (
                "In plain terms: say what problem this solves and how it works, "
                "as you'd explain it to a teammate from another team."
            )
            details = _comparison_details(question) or [
                "**Core idea** — what it is and the problem it solves",
                "**How it works** — the main moving parts",
                "**Trade-offs** — the cost and when not to use it",
                "**Real use** — where it shows up in production",
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
        elif level == "default":
            points += extra
        elif level == "medium":
            points += extra[:1]

        if level == "long":
            follows = follows + [
                ("How would this change at 10x the current scale?",
                 "Name the first bottleneck and how you'd remove it."),
                ("What would make you abandon this approach?",
                 "Give the concrete signal — cost, latency, or team overhead — that tips it."),
            ]
        elif level == "short":
            follows = follows[:1]
        follow_ups = [FollowUp(question=q, answer=a) for q, a in follows]

        if level == "short":
            details = details[:3]
        elif level == "medium":
            details = details[:5]

        is_coding = qtype == "coding"
        return AIInterviewResponse(
            question=question,
            questionType=qtype,
            answerPoints=points,
            simpleExplanation=simple if level != "short" else None,
            keyDetails=details,
            star=star,
            approach=approach if is_coding else None,
            codeHint=code_hint if is_coding else None,
            complexity=complexity if is_coding else None,
            edgeCases=edge_cases if is_coding else [],
            example=example if level != "short" else None,
            followUps=follow_ups,
            responseLength=level,
        )


    async def generate_follow_ups(
        self,
        resume: str,
        job_description: str,
        question: str,
        answer_points: list[str],
        response_length: str = "medium",
    ) -> list[FollowUp]:
        """Template follow-ups (the same ones the template answer carries)."""
        card = await self.analyze_question(resume, job_description, question, response_length)
        return card.followUps


class LLMQuotaError(RuntimeError):
    """Raised when the provider refuses the call due to quota/rate limits."""


# Rate-limit cool-down. A per-minute limit (Cerebras' Developer tier allows
# 5 requests/min) does not clear within the short retries, so every answer
# used to pay 429 + 0.5 s + 429 + 1 s + 429 before the fallback even started —
# several seconds of a blank card, once per question, until the window passed.
# A model that answered 429 is now skipped (its fallback answers at once)
# until its Retry-After — or this default — has passed.
RATE_LIMIT_COOLDOWN_S = 20.0
RATE_LIMIT_COOLDOWN_MAX_S = 60.0
_rate_limited_until: dict[str, float] = {}


def mark_rate_limited(key: str, retry_after: str | None = None) -> None:
    """Start the cool-down for model ``key`` (Retry-After seconds win)."""
    import time

    if not key:
        return
    seconds = RATE_LIMIT_COOLDOWN_S
    if retry_after and retry_after.strip().isdigit():
        seconds = min(max(float(retry_after.strip()), 5.0), RATE_LIMIT_COOLDOWN_MAX_S)
    _rate_limited_until[key] = time.monotonic() + seconds
    logger.warning("LLM %s rate-limited: skipped for %.0fs (fallback answers)", key, seconds)


def is_rate_limited(key: str | None) -> bool:
    """True while model ``key`` is cooling down after a 429."""
    import time

    until = _rate_limited_until.get(key or "")
    if until is None:
        return False
    if time.monotonic() >= until:
        _rate_limited_until.pop(key or "", None)
        return False
    return True


class OpenAICompatibleLLMProvider(LLMProvider):
    name = "openai-compatible"

    # Retry 429/5xx a couple of times before falling back to templates.
    max_retries = 2

    @staticmethod
    def _categorize_status(status: int, body: str = "") -> str:
        """Map an HTTP status (+body) to a stable, reportable error category."""
        low = body.lower()
        # 402 Payment Required is a billing state by definition; DeepSeek spells
        # it "Insufficient Balance" (OpenAI/Groq use "insufficient_quota").
        # Vercel AI Gateway answers 403 "customer_verification_required" until
        # a card is on file — billing, not a bad key.
        if (
            "insufficient_quota" in low
            or "insufficient balance" in low
            or "billing" in low
            or "customer_verification_required" in low
            or "credit card" in low
            or status == 402
        ):
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
        max_tokens_param: str = "max_tokens",
        supports_temperature: bool = True,
        reasoning_effort: str | None = None,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.llm_api_key
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.llm_model
        self.name = name
        # OpenAI's reasoning models (GPT-6 Luna) require max_completion_tokens;
        # everyone else uses max_tokens.
        self.max_tokens_param = max_tokens_param or "max_tokens"
        # Reasoning models accept only temperature=1 (the default) — omit it.
        self.supports_temperature = supports_temperature
        # Optional reasoning budget ("low"|"medium"|"high"). Omitting it means
        # the provider's own default, which for several models is a LONG think
        # that delays the first visible bullet. Verified: openai/gpt-oss-120b
        # reached its first content delta in 0.50s with low vs 1.12s without.
        # If a provider rejects the field we drop it and retry once (see
        # _suppress_reasoning_effort) — an unsupported param can never fail a
        # request outright.
        self.reasoning_effort = reasoning_effort
        self._reasoning_disabled = False
        # Set when an attempt was cut short by the token cap, so the retry
        # actually gets more room (see BUDGET_LIFT_ON_TRUNCATION).
        self._budget_multiplier: float = 1.0
        # Extra instruction appended to the system prompt on a retry (empty /
        # truncated answer). Kept per-instance because the payload is rebuilt
        # every attempt, which is what makes a dropped reasoning_effort or a
        # lifted budget take effect on the retry.
        self._system_addendum: str = ""
        self._mock = MockLLMProvider()
        # Usage telemetry (filled after each call; read by AIService for tracking).
        self.last_latency_ms: float = 0.0
        self.last_input_tokens: int | None = None
        self.last_output_tokens: int | None = None
        self.last_error_category: str | None = None
        # Time-to-first-provisional-bullet (ms) — the number the candidate
        # actually perceives as "how long until something appears". None when
        # the call never streamed a bullet (non-stream call or failure).
        self.first_delta_ms: float | None = None
        # True when the latest streaming call emitted at least one delta
        # before finishing/failing (lets the caller pick single-shot retry).
        self.stream_had_deltas: bool = False
        # Set by the caller when another model can answer: a 429 then raises
        # at once instead of retrying into the same per-minute window.
        self.fail_fast_rate_limit: bool = False

    def _retry_rate_limit(self, status: int, exc: httpx.HTTPStatusError) -> bool:
        """Record a 429 for the cool-down; False when it must not be retried."""
        if status != 429:
            return True
        mark_rate_limited(self.name, exc.response.headers.get("retry-after"))
        return not self.fail_fast_rate_limit

    def _suppress_reasoning_effort(self, status: int) -> bool:
        """True when this request-body rejection was caused by `reasoning_effort`.

        One clean retry without the field is always allowed, so a provider
        update that stops accepting it degrades to the old behaviour instead of
        breaking the answer. 400 covers Google/Groq/OpenAI-style rejections and
        422 covers DeepSeek, which deserializes the body and answers
        HTTP 422 `unknown variant ...` for a bad effort value.
        """
        if status not in (400, 422):
            return False
        if not self.reasoning_effort or self._reasoning_disabled:
            return False
        self._reasoning_disabled = True
        logger.warning(
            "LLM rejected reasoning_effort (HTTP %d) — retrying without it: model=%s",
            status,
            self.model,
        )
        return True

    def _output_budget(self, level: str) -> int:
        return int(MAX_TOKENS.get(level, 1400) * self._budget_multiplier)

    def _lift_budget(self, why: str) -> None:
        """Give the NEXT attempt more room after a truncated/empty answer."""
        self._budget_multiplier *= BUDGET_LIFT_ON_TRUNCATION
        logger.warning(
            "LLM unusable response (%s) — lifting output budget to %.2fx (model=%s)",
            why, self._budget_multiplier, self.model,
        )

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
        if "customer_verification_required" in low or "credit card" in low:
            return (
                "AI provider account needs billing set up — add a payment card in the "
                f"provider dashboard (e.g. Vercel → AI Gateway) (HTTP {status}). {detail}"
            )
        if (
            "insufficient_quota" in low
            or "insufficient balance" in low
            or "billing" in low
            or status == 402
        ):
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
        self,
        level: str,
        resume: str,
        job_description: str,
        question: str,
        history: list[dict] | None = None,
    ) -> tuple[dict, int]:
        """Chat-completions body + token budget shared by single-shot/stream."""
        payload: dict = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    # + retry addendum (empty on the first attempt).
                    "content": build_system_prompt(level) + self._system_addendum,
                },
                {
                    "role": "user",
                    "content": (
                        f"Desired response length: {level.upper()}\n\n"
                        f"Resume:\n{resume[:4000]}\n\n"
                        f"Job description:\n{job_description[:4000]}\n\n"
                        f"{format_history(history)}"
                        f"Interviewer question:\n{question}"
                    ),
                },
            ],
        }
        # Reasoning models (GPT-6 Luna) accept only the default temperature,
        # so omit it for them; everyone else gets a little variance.
        if self.supports_temperature:
            payload["temperature"] = 0.6 if level == "long" else 0.4
        # Ask for a SHORT think when the model supports it. This is the single
        # biggest lever on time-to-first-bullet for reasoning models: without it
        # they sit in a silent thinking phase while the candidate watches a
        # spinner (measured: openai/gpt-oss-120b first delta 1.12s -> 0.50s).
        # Dropped automatically on a 400 so an unsupported field can never fail
        # the request outright.
        if self.reasoning_effort and not self._reasoning_disabled:
            payload["reasoning_effort"] = self.reasoning_effort
        # Shared caps (short answers never need a long tail). Lifted when a
        # previous attempt was truncated, so the retry is not doomed to fail
        # for exactly the same reason.
        max_tokens = self._output_budget(level)
        return payload, max_tokens

    async def analyze_question_stream(
        self,
        resume: str,
        job_description: str,
        question: str,
        response_length: str = "medium",
        on_delta: Callable[[str], Awaitable[None]] | None = None,
        history: list[dict] | None = None,
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
                resume, job_description, question, response_length, history
            )
        level = response_length if response_length in LENGTH_RULES else "medium"
        payload, max_tokens = self._request_payload(
            level, resume, job_description, question, history
        )
        # NOTE: no stream_options.include_usage — tolerance on Gemini's
        # OpenAI-compatible endpoint is unverified, so streamed token counts
        # stay None (never estimated) instead of risking a rejected request.
        logger.info(
            "LLM stream request: model=%s level=%s max_tokens=%d qlen=%d",
            self.model, level, max_tokens, len(question)
        )
        t0 = time.perf_counter()
        self.stream_had_deltas = False
        self.first_delta_ms = None
        self.last_input_tokens = None
        self.last_output_tokens = None
        timeout = httpx.Timeout(connect=15.0, read=60.0, write=15.0, pool=15.0)
        try:
            async with shared_http() as client:
                last_exc: Exception | None = None
                # +1 spare attempt only when reasoning_effort is in play: it is
                # reserved for the "provider rejected the field, retry without
                # it" path, so a normal call's retry budget is unchanged.
                attempts = self.max_retries + 1 + (1 if self.reasoning_effort else 0)
                for attempt in range(attempts):
                    # A started stream can never resume — retry pre-delta only.
                    if attempt > 0 and self.stream_had_deltas:
                        break
                    # Rebuilt each attempt: a 400 on reasoning_effort removes
                    # the field from the NEXT payload.
                    payload, max_tokens = self._request_payload(
                        level, resume, job_description, question, history
                    )
                    body = {**payload, self.max_tokens_param: max_tokens, "stream": True}
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
                            finish_reason = None
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
                                    choice = choices[0] or {}
                                    fin = choice.get("finish_reason")
                                    if fin:
                                        finish_reason = fin
                                    delta = (
                                        choice.get("delta", {}).get("content")
                                        or ""
                                    )
                                except (AttributeError, IndexError, TypeError):
                                    continue
                                if not delta:
                                    continue
                                buffer += delta
                                for point in _extract_answer_points(buffer)[sent:]:
                                    sent += 1
                                    if not self.stream_had_deltas:
                                        self.stream_had_deltas = True
                                        # Time-to-first-bullet: the latency the
                                        # candidate actually SEES (the final
                                        # total hides how long the model spent
                                        # thinking before anything appeared).
                                        self.first_delta_ms = (
                                            time.perf_counter() - t0
                                        ) * 1000
                                        logger.info(
                                            "LLM stream first bullet: %.2fs model=%s",
                                            self.first_delta_ms / 1000, self.model,
                                        )
                                    await on_delta(point)
                            content = buffer.strip()
                            self.last_latency_ms = (time.perf_counter() - t0) * 1000
                            self.last_error_category = "ok"
                            if not content:
                                self._lift_budget("empty content")
                                raise ValueError("LLM returned an empty response")
                            try:
                                data = _extract_json(content)
                            except ValueError:
                                # finish_reason == "length" is the common cause:
                                # the reasoning model outran the cap mid-JSON.
                                # Lift the budget now so the caller's retry has
                                # real headroom instead of failing identically.
                                self._lift_budget(
                                    f"finish_reason={finish_reason} / invalid JSON"
                                )
                                raise
                            data.setdefault("question", question)
                            data["responseLength"] = level
                            logger.info(
                                "LLM stream ok: %.2fs (first bullet %.2fs), %d points",
                                time.perf_counter() - t0,
                                (self.first_delta_ms or 0.0) / 1000,
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
                        # Error bodies were previously swallowed, which made
                        # provider 400s (bad param, retired model) invisible.
                        logger.warning(
                            "LLM stream HTTP %d after %.2fs: %s",
                            status, self.last_latency_ms / 1000, err_body[:300],
                        )
                        # A rejected `reasoning_effort` must never fail the
                        # answer — drop it and retry once with the spare attempt.
                        if (
                            self._suppress_reasoning_effort(status)
                            and not self.stream_had_deltas
                        ):
                            continue
                        if (
                            status in (429, 500, 502, 503, 504)
                            and self._retry_rate_limit(status, exc)
                            and attempt < self.max_retries
                            and not self.stream_had_deltas
                        ):
                            wait = RETRY_BACKOFF_BASE * (2**attempt)
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
                            await asyncio.sleep(RETRY_BACKOFF_BASE * (2**attempt))
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
                    resume, job_description, question, response_length, history
                )
            except Exception:
                return await self._mock.analyze_question(resume, job_description, question)

    async def analyze_question(
        self,
        resume: str,
        job_description: str,
        question: str,
        response_length: str = "medium",
        history: list[dict] | None = None,
    ) -> AIInterviewResponse:
        import asyncio
        import time

        level = response_length if response_length in LENGTH_RULES else "medium"
        payload, max_tokens = self._request_payload(
            level, resume, job_description, question, history
        )
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
                # +1 spare attempt is reserved for dropping a rejected
                # reasoning_effort — a normal call's retry budget is unchanged.
                attempts = self.max_retries + 1 + (1 if self.reasoning_effort else 0)
                for attempt in range(attempts):
                    # Rebuilt per attempt so a 400 can drop reasoning_effort,
                    # and so a lifted budget from a truncated attempt applies.
                    payload, max_tokens = self._request_payload(
                        level, resume, job_description, question, history
                    )
                    try:
                        resp = await client.post(
                            f"{self.base_url}/chat/completions",
                            headers=self._headers(),
                            json={**payload, self.max_tokens_param: max_tokens},
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
                                self._lift_budget("empty content")
                                self._system_addendum += (
                                    "\nRespond with JSON only. Do not add reasoning text."
                                )
                                await asyncio.sleep(0.25)
                                continue
                            raise ValueError("LLM returned an empty response")
                        try:
                            data = _extract_json(content)
                        except ValueError:
                            # Truncated / unparseable JSON (a reasoning model
                            # that outran its budget mid-answer). Retry with
                            # more headroom and an explicit "don't truncate".
                            if attempt < self.max_retries:
                                self._lift_budget("unparseable JSON")
                                self._system_addendum += (
                                    "\nRespond with a COMPLETE, valid JSON object. "
                                    "Do not truncate."
                                )
                                await asyncio.sleep(0.25)
                                continue
                            raise
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
                        # Error bodies were previously swallowed, which made
                        # provider 400s (bad param, retired model) invisible.
                        logger.warning(
                            "LLM HTTP %d after %.2fs: %s",
                            status, self.last_latency_ms / 1000, body[:300],
                        )
                        # A rejected `reasoning_effort` must never fail the
                        # answer — drop it and retry once with the spare attempt.
                        if self._suppress_reasoning_effort(status):
                            continue
                        # Retry transient limits/server errors only.
                        if (
                            status in (429, 500, 502, 503, 504)
                            and self._retry_rate_limit(status, exc)
                            and attempt < self.max_retries
                        ):
                            wait = RETRY_BACKOFF_BASE * (2**attempt)
                            retry_after = exc.response.headers.get("retry-after")
                            if retry_after and retry_after.isdigit():
                                # Live answers cannot wait out a long rate-limit
                                # window — cap it and let the fallback handle it.
                                wait = min(float(retry_after), 5.0)
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
                            await asyncio.sleep(RETRY_BACKOFF_BASE * (2**attempt))
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


    async def generate_follow_ups(
        self,
        resume: str,
        job_description: str,
        question: str,
        answer_points: list[str],
        response_length: str = "medium",
    ) -> list[FollowUp]:
        """On-demand "If they ask further" items (one small JSON request).

        Uses the same model, key and reasoning setting as the answer, plus the
        Best Answer as context so follow-ups build on it instead of repeating
        it. Raises LLMQuotaError when the provider cannot answer.
        """
        import asyncio
        import time

        level = response_length if response_length in FOLLOWUP_RULES else "medium"
        best_answer = " ".join(str(p) for p in (answer_points or []))[:1500]

        def payload() -> dict:
            body: dict = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": build_followup_prompt(level)},
                    {
                        "role": "user",
                        "content": (
                            f"Resume:\n{resume[:4000]}\n\n"
                            f"Job description:\n{job_description[:4000]}\n\n"
                            f"Interviewer question:\n{question}\n\n"
                            f"Best Answer already given:\n{best_answer}"
                        ),
                    },
                ],
                self.max_tokens_param: FOLLOWUP_MAX_TOKENS,
            }
            if self.supports_temperature:
                body["temperature"] = 0.4
            if self.reasoning_effort and not self._reasoning_disabled:
                body["reasoning_effort"] = self.reasoning_effort
            return body

        logger.info("LLM follow-ups request: model=%s level=%s qlen=%d", self.model, level, len(question))
        t0 = time.perf_counter()
        attempts = self.max_retries + 1 + (1 if self.reasoning_effort else 0)
        async with shared_http() as client:
            for attempt in range(attempts):
                try:
                    resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers=self._headers(),
                        json=payload(),
                        timeout=20.0,
                    )
                    if resp.status_code >= 400:
                        raise httpx.HTTPStatusError(
                            f"HTTP {resp.status_code}", request=resp.request, response=resp
                        )
                    body_json = resp.json()
                    usage = body_json.get("usage") or {}
                    self.last_input_tokens = usage.get("prompt_tokens")
                    self.last_output_tokens = usage.get("completion_tokens")
                    self.last_latency_ms = (time.perf_counter() - t0) * 1000
                    content = ((body_json["choices"][0].get("message") or {}).get("content") or "").strip()
                    try:
                        data = _extract_json(content)
                    except ValueError:
                        if attempt < self.max_retries:
                            continue  # one more try: malformed JSON is usually a one-off
                        self.last_error_category = "bad_response"
                        raise LLMQuotaError("Follow-up suggestions came back unreadable.") from None
                    items = data.get("followUps") if isinstance(data, dict) else None
                    follow_ups = [
                        FollowUp(question=str(f.get("question", "")), answer=str(f.get("answer", "")))
                        for f in (items if isinstance(items, list) else [])
                        if isinstance(f, dict) and str(f.get("question", "")).strip()
                    ][: FOLLOWUP_LIMIT[level]]
                    self.last_error_category = "ok"
                    logger.info(
                        "LLM follow-ups ok: %.2fs, %d items, model=%s",
                        time.perf_counter() - t0, len(follow_ups), self.model,
                    )
                    return follow_ups
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    body = exc.response.text or ""
                    self.last_error_category = self._categorize_status(status, body)
                    self.last_latency_ms = (time.perf_counter() - t0) * 1000
                    logger.warning("LLM follow-ups HTTP %d: %s", status, body[:300])
                    if self._suppress_reasoning_effort(status):
                        continue
                    # The next live answer would hit the same window: skip ahead.
                    self._retry_rate_limit(status, exc)
                    if status in (500, 502, 503, 504) and attempt < self.max_retries:
                        await asyncio.sleep(RETRY_BACKOFF_BASE * (2**attempt))
                        continue
                    raise LLMQuotaError(self._friendly_error(status, body, self.model)) from exc
                except (httpx.TimeoutException, httpx.RequestError) as exc:
                    self.last_error_category = (
                        "timeout" if isinstance(exc, httpx.TimeoutException) else "network"
                    )
                    self.last_latency_ms = (time.perf_counter() - t0) * 1000
                    if attempt < self.max_retries:
                        await asyncio.sleep(RETRY_BACKOFF_BASE * (2**attempt))
                        continue
                    raise LLMQuotaError("Unable to reach the AI service.") from exc
        raise LLMQuotaError("Follow-up suggestions are unavailable right now.")

    async def complete_json(self, system: str, user: str, max_tokens: int = 1200) -> dict:
        """One free-form JSON request (mock-interview questions / feedback).

        Same model, key, retries and reasoning setting as the answers, but the
        caller owns the prompt and the shape of the JSON. Raises LLMQuotaError
        when the provider cannot answer, so callers can fall back.
        """
        import asyncio
        import time

        def payload() -> dict:
            body: dict = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                self.max_tokens_param: max_tokens,
            }
            if self.supports_temperature:
                body["temperature"] = 0.5
            if self.reasoning_effort and not self._reasoning_disabled:
                body["reasoning_effort"] = self.reasoning_effort
            return body

        t0 = time.perf_counter()
        attempts = self.max_retries + 1 + (1 if self.reasoning_effort else 0)
        async with shared_http() as client:
            for attempt in range(attempts):
                try:
                    resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers=self._headers(),
                        json=payload(),
                        timeout=30.0,
                    )
                    if resp.status_code >= 400:
                        raise httpx.HTTPStatusError(
                            f"HTTP {resp.status_code}", request=resp.request, response=resp
                        )
                    body_json = resp.json()
                    usage = body_json.get("usage") or {}
                    self.last_input_tokens = usage.get("prompt_tokens")
                    self.last_output_tokens = usage.get("completion_tokens")
                    self.last_latency_ms = (time.perf_counter() - t0) * 1000
                    content = ((body_json["choices"][0].get("message") or {}).get("content") or "").strip()
                    try:
                        data = _extract_json(content)
                    except ValueError:
                        if attempt < self.max_retries:
                            continue  # malformed JSON is usually a one-off
                        self.last_error_category = "bad_response"
                        raise LLMQuotaError("The AI response came back unreadable.") from None
                    if not isinstance(data, dict):
                        self.last_error_category = "bad_response"
                        raise LLMQuotaError("The AI response came back unreadable.")
                    self.last_error_category = "ok"
                    return data
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    body = exc.response.text or ""
                    self.last_error_category = self._categorize_status(status, body)
                    self.last_latency_ms = (time.perf_counter() - t0) * 1000
                    logger.warning("LLM json HTTP %d: %s", status, body[:300])
                    if self._suppress_reasoning_effort(status):
                        continue
                    # The next live answer would hit the same window: skip ahead.
                    self._retry_rate_limit(status, exc)
                    if status in (500, 502, 503, 504) and attempt < self.max_retries:
                        await asyncio.sleep(RETRY_BACKOFF_BASE * (2**attempt))
                        continue
                    raise LLMQuotaError(self._friendly_error(status, body, self.model)) from exc
                except (httpx.TimeoutException, httpx.RequestError) as exc:
                    self.last_error_category = (
                        "timeout" if isinstance(exc, httpx.TimeoutException) else "network"
                    )
                    self.last_latency_ms = (time.perf_counter() - t0) * 1000
                    if attempt < self.max_retries:
                        await asyncio.sleep(RETRY_BACKOFF_BASE * (2**attempt))
                        continue
                    raise LLMQuotaError("Unable to reach the AI service.") from exc
        raise LLMQuotaError("The AI service is unavailable right now.")


def get_llm_provider() -> LLMProvider:
    settings = get_settings()
    if settings.llm_api_key:
        return OpenAICompatibleLLMProvider()
    return MockLLMProvider()
