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
    Ask Further — generated on demand, empty in answers) and, for coding,
    ``approach`` / ``codeHint`` (the ONE program) / ``complexity`` /
    ``edgeCases``.

    ``keyTakeaways``, ``followUpQuestions``, ``comparison`` and ``humanCode``
    (the removed "Program 2 · human-style version") are legacy fields kept so
    older clients and stored answers still validate.
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
    #: Quick-reference table for multi-part / list / compare questions
    #: (one row per member, e.g. Concept | Example | Key keyword).
    comparison: Optional[ComparisonTable] = None

    @field_validator("comparison", mode="before")
    @classmethod
    def _lenient_table(cls, v: object) -> object:
        """A malformed table must never fail the whole answer.

        Cells are stringified, each row is padded/trimmed to the header width,
        empty rows dropped; a table with no headers or no rows becomes null.
        """
        if not isinstance(v, dict):
            return None
        headers = [str(h).strip() for h in (v.get("headers") or []) if str(h or "").strip()]
        if not headers:
            return None
        width = len(headers)
        rows = []
        for row in v.get("rows") or []:
            if not isinstance(row, list):
                continue
            cells = ["" if c is None else str(c).strip() for c in row][:width]
            cells += [""] * (width - len(cells))
            if any(cells):
                rows.append(cells)
        return {"headers": headers, "rows": rows} if rows else None

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