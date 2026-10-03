"""Interview session models."""
from typing import Optional

from pydantic import BaseModel, Field

from app.models.ai_response import AIInterviewResponse


class PrepareRequest(BaseModel):
    resumeText: str
    jobDescription: str
    responseLength: Optional[str] = "medium"


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
