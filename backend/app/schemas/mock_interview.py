"""Mock-interview API schemas (Pydantic v2).

A mock interview is self-practice: the app asks role-based questions, the
candidate answers in their own words, and gets a score with feedback.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

InterviewType = Literal["mixed", "technical", "behavioral", "hr"]
Difficulty = Literal["easy", "medium", "hard"]
QuestionType = Literal["technical", "behavioral", "hr"]
FeedbackSource = Literal["ai", "basic"]

#: Resume / JD are trimmed before they reach the model; this only bounds the body.
MAX_CONTEXT_CHARS = 500_000
MAX_ANSWER_CHARS = 6000
MAX_QUESTIONS = 10


class MockQuestionsRequest(BaseModel):
    role: str = Field(default="", max_length=120)
    jobDescription: Optional[str] = Field(default=None, max_length=MAX_CONTEXT_CHARS)
    resumeText: Optional[str] = Field(default=None, max_length=MAX_CONTEXT_CHARS)
    interviewType: InterviewType = "mixed"
    difficulty: Difficulty = "medium"
    count: int = Field(default=5, ge=1, le=MAX_QUESTIONS)


class MockQuestion(BaseModel):
    id: int
    question: str
    type: QuestionType
    #: What a strong answer should show (shown as a hint before answering).
    focus: str = ""


class MockQuestionsResponse(BaseModel):
    questions: list[MockQuestion]
    source: FeedbackSource


class MockFeedbackRequest(BaseModel):
    question: str = Field(max_length=1000)
    questionType: QuestionType = "technical"
    answer: str = Field(max_length=MAX_ANSWER_CHARS)
    role: str = Field(default="", max_length=120)
    jobDescription: Optional[str] = Field(default=None, max_length=MAX_CONTEXT_CHARS)
    resumeText: Optional[str] = Field(default=None, max_length=MAX_CONTEXT_CHARS)


class MockFeedback(BaseModel):
    #: 1 (weak) … 10 (excellent).
    score: int = Field(ge=1, le=10)
    summary: str
    strengths: list[str]
    improvements: list[str]
    #: Key points of a strong answer, to compare against your own.
    sampleAnswer: list[str]
    source: FeedbackSource
