"""AI guidance response model."""
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

ResponseLength = Literal["default", "short", "medium", "long"]

# How many answer bullets to expect per level (also used by the mock provider).
ANSWER_POINT_TARGET: dict[str, tuple[int, int]] = {
    "default": (5, 8),
    "short": (3, 4),
    "medium": (5, 7),
    "long": (8, 12),
}


class ComparisonTable(BaseModel):
    """A side-by-side table for "difference / compare" questions.

    ``headers`` names the columns (e.g. ``["Aspect", "X", "Y"]``) and ``rows``
    are the aligned cell values. Rendered as a real table by the frontend so
    the candidate can glance and answer, exactly like a comparison table in
    ChatGPT.
    """

    headers: list[str] = Field(min_length=1)
    rows: list[list[str]] = Field(default_factory=list)

    @field_validator("headers")
    @classmethod
    def _clean_headers(cls, v: list[str]) -> list[str]:
        cleaned = [h.strip() for h in v if h and h.strip()]
        if not cleaned:
            raise ValueError("comparison.headers must contain at least one column")
        return cleaned


class FollowUp(BaseModel):
    """A likely interviewer follow-up plus a short, strong answer to it."""

    question: str
    answer: str = ""

    @field_validator("question", "answer", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else ("" if v is None else v)


def _clean_list(v: list[str]) -> list[str]:
    return [p.strip() for p in v if isinstance(p, str) and p.strip()]


class AIInterviewResponse(BaseModel):
    """One adaptive "Interview Answer Card".

    ``answerPoints`` is the **Best Answer** — the spoken, interview-ready
    answer as a few short sentences. It stays first in the JSON and required,
    because the live stream parses it to show the provisional draft.

    Every other card section is optional and only filled when it fits the
    question: ``simpleExplanation`` (Explain It Simply), ``keyDetails`` (what
    the interviewer expects), ``example`` (Practical Example), ``star``
    (scenario/behavioral Situation → Action → Result), ``followUps`` (If They
    Ask Further) and, for coding, ``approach`` / ``codeHint`` (program 1, the
    polished AI solution) / ``humanCode`` (program 2, the same solution written
    the way a person naturally types it, with plain comments) / ``complexity``
    / ``edgeCases``.

    ``keyTakeaways``, ``followUpQuestions`` and ``comparison`` are legacy
    fields kept so older clients and stored answers still validate.
    """

    question: str
    questionType: Literal["technical", "behavioral", "coding", "general"]
    answerPoints: list[str] = Field(min_length=1)
    simpleExplanation: Optional[str] = None
    keyDetails: list[str] = Field(default_factory=list)
    star: Optional[dict] = None
    approach: Optional[str] = None
    codeHint: Optional[str] = None
    humanCode: Optional[str] = None
    complexity: Optional[str] = None
    edgeCases: list[str] = Field(default_factory=list)
    example: Optional[str] = None
    followUps: list[FollowUp] = Field(default_factory=list)
    keyTakeaways: list[str] = Field(default_factory=list)
    followUpQuestions: list[str] = Field(default_factory=list)
    responseLength: ResponseLength = "medium"
    #: Legacy comparison table — no longer requested; contrasts now live in keyDetails.
    comparison: Optional[ComparisonTable] = None

    @field_validator("answerPoints")
    @classmethod
    def _non_empty_points(cls, v: list[str]) -> list[str]:
        cleaned = _clean_list(v)
        if not cleaned:
            raise ValueError("answerPoints must contain at least one non-empty point")
        return cleaned

    @field_validator("keyTakeaways", "keyDetails", "edgeCases", "followUpQuestions")
    @classmethod
    def _clean_lists(cls, v: list[str]) -> list[str]:
        return _clean_list(v)

    @field_validator("simpleExplanation", "approach", "complexity", "example", "codeHint", "humanCode", mode="before")
    @classmethod
    def _blank_to_none(cls, v: object) -> object:
        # Models sometimes emit "" instead of null for a section that doesn't
        # apply; an empty string must not render as an empty card section.
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("followUps", mode="before")
    @classmethod
    def _coerce_followups(cls, v: object) -> object:
        # Tolerate a model that returns plain strings ("question - answer").
        if not isinstance(v, list):
            return []
        out: list[object] = []
        for item in v:
            if isinstance(item, FollowUp):
                if item.question:
                    out.append(item)
            elif isinstance(item, str):
                q, _, a = item.partition(" - ")
                if q.strip():
                    out.append({"question": q, "answer": a})
            elif isinstance(item, dict) and str(item.get("question") or "").strip():
                out.append(item)
        return out

    @model_validator(mode="after")
    def _legacy_followup_questions(self) -> "AIInterviewResponse":
        # Older clients only read followUpQuestions; keep it populated.
        if not self.followUpQuestions and self.followUps:
            self.followUpQuestions = [f.question for f in self.followUps]
        return self

    @field_validator("questionType", mode="before")
    @classmethod
    def _normalize_qtype(cls, v: object) -> object:
        # Providers differ in casing ("Technical", "CODING"); normalize before
        # Literal validation so a valid answer isn't discarded over case.
        if isinstance(v, str):
            low = v.strip().lower()
            if low in ("technical", "behavioral", "coding", "general"):
                return low
        return v

    @field_validator("responseLength", mode="before")
    @classmethod
    def _normalize_length(cls, v: object) -> str:
        aliases = {
            "default": "default",
            "complete": "default",
            "concise": "short",
            "brief": "short",
            "short": "short",
            "standard": "medium",
            "medium": "medium",
            "detailed": "long",
            "long": "long",
        }
        key = (v or "").strip().lower() if isinstance(v, str) else ""
        return aliases.get(key, "medium")