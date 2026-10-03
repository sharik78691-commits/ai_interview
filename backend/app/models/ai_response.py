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