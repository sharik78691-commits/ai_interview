"""AI guidance response model."""
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

ResponseLength = Literal["short", "medium", "long"]

# How many answer bullets to expect per level (also used by the mock provider).
ANSWER_POINT_TARGET: dict[str, tuple[int, int]] = {
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


class AIInterviewResponse(BaseModel):
    question: str
    questionType: Literal["technical", "behavioral", "coding", "general"]
    answerPoints: list[str] = Field(min_length=1)
    star: Optional[dict] = None
    codeHint: Optional[str] = None
    example: Optional[str] = None
    keyTakeaways: list[str] = Field(default_factory=list)
    followUpQuestions: list[str] = Field(default_factory=list)
    responseLength: ResponseLength = "medium"
    #: Optional comparison table (comparison/difference questions only).
    comparison: Optional[ComparisonTable] = None

    @field_validator("answerPoints")
    @classmethod
    def _non_empty_points(cls, v: list[str]) -> list[str]:
        cleaned = [p.strip() for p in v if p and p.strip()]
        if not cleaned:
            raise ValueError("answerPoints must contain at least one non-empty point")
        return cleaned

    @field_validator("keyTakeaways")
    @classmethod
    def _clean_takeaways(cls, v: list[str]) -> list[str]:
        return [p.strip() for p in v if p and p.strip()]

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