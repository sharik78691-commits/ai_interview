"""Mock-interview endpoints (authenticated, per-user, rate limited).

Thin HTTP layer over :class:`app.services.mock_interview_service.MockInterviewService`:
role-based practice questions, then a score with feedback for each answer.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import require_user_csrf
from app.core.config import get_settings
from app.core.ratelimit import rate_limit_key
from app.models.user import User
from app.schemas.mock_interview import (
    MockFeedback,
    MockFeedbackRequest,
    MockQuestionsRequest,
    MockQuestionsResponse,
)
from app.services.activity import log_event, log_llm_usage
from app.services.interview_context import context_store
from app.services.mock_interview_service import MockInterviewService

logger = logging.getLogger(__name__)

router = APIRouter()


async def _log_usage(user_id: int, request_type: str, service: MockInterviewService) -> None:
    await run_in_threadpool(
        log_llm_usage,
        user_id=user_id,
        request_type=request_type,
        provider=service.last_provider,
        model=service.last_model,
        latency_ms=service.last_usage.get("latency_ms"),
        input_tokens=service.last_usage.get("input_tokens"),
        output_tokens=service.last_usage.get("output_tokens"),
        error_category=service.last_usage.get("error_category"),
    )


@router.post("/mock-interview/questions", response_model=MockQuestionsResponse)
async def mock_questions(
    req: MockQuestionsRequest,
    user: User = Depends(require_user_csrf),
) -> MockQuestionsResponse:
    rate_limit_key("mock-interview", str(user.id), get_settings().ai_rate_limit_per_minute)
    ctx = context_store.get(user.id)
    service = MockInterviewService()
    questions, source = await service.questions(
        role=req.role,
        job_description=(req.jobDescription or "").strip() or ctx["job"],
        resume=(req.resumeText or "").strip() or ctx["resume"],
        interview_type=req.interviewType,
        difficulty=req.difficulty,
        count=req.count,
    )
    logger.info(
        "Mock interview questions: user=%s type=%s difficulty=%s count=%d source=%s",
        user.id, req.interviewType, req.difficulty, len(questions), source,
    )
    await run_in_threadpool(log_event, "mock_started", user.id)
    await _log_usage(user.id, "mock_questions", service)
    return MockQuestionsResponse(questions=questions, source=source)


@router.post("/mock-interview/feedback", response_model=MockFeedback)
async def mock_feedback(
    req: MockFeedbackRequest,
    user: User = Depends(require_user_csrf),
) -> MockFeedback:
    rate_limit_key("mock-interview", str(user.id), get_settings().ai_rate_limit_per_minute)
    question = req.question.strip()
    answer = req.answer.strip()
    if not question or not answer:
        raise HTTPException(status_code=422, detail="question and answer must be non-empty.")
    ctx = context_store.get(user.id)
    service = MockInterviewService()
    result = await service.feedback(
        question=question,
        answer=answer,
        question_type=req.questionType,
        role=req.role,
        job_description=(req.jobDescription or "").strip() or ctx["job"],
        resume=(req.resumeText or "").strip() or ctx["resume"],
    )
    # Answer text is user content (PII) — log its size, not its body.
    logger.info(
        "Mock interview feedback: user=%s answer_chars=%d score=%d source=%s",
        user.id, len(answer), result.score, result.source,
    )
    await run_in_threadpool(log_event, "mock_reviewed", user.id)
    await _log_usage(user.id, "mock_feedback", service)
    return result
