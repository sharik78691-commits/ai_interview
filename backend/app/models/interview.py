"""Interview session models."""
from typing import Optional

from pydantic import BaseModel, Field

from app.models.ai_response import AIInterviewResponse


class PrepareRequest(BaseModel):
    # Resume and job description are OPTIONAL: a user can start an interview
    # directly without providing either.
    resumeText: Optional[str] = Field(default="", max_length=500_000)
    jobDescription: Optional[str] = Field(default="", max_length=500_000)
    responseLength: Optional[str] = Field(default="medium", max_length=20)


# Input caps bound LLM token spend per request. Resume/JD match the limit the
# resume library already enforces.
MAX_QUESTION_CHARS = 4000
MAX_CONTEXT_CHARS = 500_000


class AnalyzeRequest(BaseModel):
    """REST fallback for one answer (the WebSocket is the primary path)."""

    question: str = Field(max_length=MAX_QUESTION_CHARS)
    resumeText: Optional[str] = Field(default=None, max_length=MAX_CONTEXT_CHARS)
    jobDescription: Optional[str] = Field(default=None, max_length=MAX_CONTEXT_CHARS)
    responseLength: Optional[str] = Field(default=None, max_length=20)
    model: Optional[str] = Field(default=None, max_length=80)


class PrepareResponse(BaseModel):
    status: str
    message: str
    resumeChars: int = 0
    jobChars: int = 0
    responseLength: str = "medium"


class TranscriptMessage(BaseModel):
    type: str = "transcript"
    text: str


class GuidanceMessage(BaseModel):
    type: str = "ai_guidance"
    data: AIInterviewResponse


class HistoryItem(BaseModel):
    question: str
    transcript: str = ""
    guidance: Optional[AIInterviewResponse] = None
    timestamp: str = Field(default="")
