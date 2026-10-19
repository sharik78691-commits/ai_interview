"""Interview prepare/context/analyze endpoints (authenticated, per-user).

Thin HTTP layer: per-user context lives in
:mod:`app.services.interview_context`, answer generation in
:class:`app.services.ai_service.AIService`.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import require_user, require_user_csrf
from app.core.config import get_settings
from app.core.ratelimit import rate_limit_key
from app.models.ai_response import AIInterviewResponse
from app.models.interview import AnalyzeRequest, PrepareRequest, PrepareResponse
from app.models.user import User
from app.providers.llm.registry import validate_model_key
from app.services.activity import log_event, log_llm_usage
from app.services.ai_service import AIService
from app.services.interview_context import (
    VALID_LENGTHS,
    coerce_length,
    context_store,
    normalize_length,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Backward-compatible names (older imports and tests use these).
get_context = context_store.get
clear_context = context_store.clear
_normalize_length = normalize_length
_contexts = context_store._contexts

__all__ = ["router", "AnalyzeRequest", "VALID_LENGTHS", "get_context", "clear_context"]


@router.post("/interview/prepare", response_model=PrepareResponse)
def prepare(
    req: PrepareRequest,
    request: Request,
    user: User = Depends(require_user_csrf),
) -> PrepareResponse:
    # Resume and job description are optional — the user may prepare an
    # interview directly without either. Empty context is allowed.
    resume = (req.resumeText or "").strip()
    job = (req.jobDescription or "").strip()
    length = coerce_length(req.responseLength)
    context_store.update(user.id, resume=resume, job=job, length=length)
    logger.info(
        "Interview prepared: user=%s resume_chars=%d job_chars=%d length=%s",
        user.id, len(resume), len(job), length,
    )
    log_event("interview_prepared", user.id)
    return PrepareResponse(
        status="ready",
        message="Interview context stored. Ready for live guidance.",
        resumeChars=len(resume),
        jobChars=len(job),
        responseLength=length,
    )


@router.get("/interview/context")
def get_context_endpoint(user: User = Depends(require_user)) -> dict:
    ctx = context_store.get(user.id)

    def _trunc(s: str, n: int = 500) -> str:
        return s[:n] + ("..." if len(s) > n else "")

    return {
        "hasContext": bool(ctx["resume"] and ctx["job"]),
        "resumeChars": len(ctx["resume"]),
        "jobChars": len(ctx["job"]),
        "responseLength": ctx["length"],
        "resume": _trunc(ctx["resume"]),
        "job": _trunc(ctx["job"]),
    }


@router.post("/interview/analyze", response_model=AIInterviewResponse)
async def analyze(
    req: AnalyzeRequest,
    request: Request,
    user: User = Depends(require_user_csrf),
) -> AIInterviewResponse:
    rate_limit_key("ai-analyze", str(user.id), get_settings().ai_rate_limit_per_minute)
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=422, detail="question must be non-empty.")
    # Validate the model against the server-side allowlist BEFORE calling out.
    try:
        model_key = validate_model_key(req.model)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ctx = context_store.get(user.id)
    resume = (req.resumeText or "").strip() or ctx["resume"]
    job = (req.jobDescription or "").strip() or ctx["job"]
    length = coerce_length(req.responseLength or ctx["length"])
    service = AIService(
        resume_text=resume,
        job_description=job,
        response_length=length,
    )
    logger.info(
        "Analyze question: user=%s question_chars=%d length=%s model=%s",
        user.id, len(question), length, model_key,
    )
    try:
        answer = await service.analyze(question, model_key=model_key)
    except Exception:
        # Question text is user content (PII) — log its size, not its body.
        logger.exception("Analyze failed: user=%s question_chars=%d", user.id, len(question))
        raise HTTPException(
            status_code=502, detail="AI response unavailable. Please try again."
        )
    # Activity writes are blocking DB inserts: keep them off the event loop.
    await run_in_threadpool(log_event, "question_analyzed", user.id)
    # Usage tracking (provider-reported tokens only, never estimated).
    await run_in_threadpool(
        log_llm_usage,
        user_id=user.id,
        request_type="analyze",
        provider=service.last_provider,
        model=service.last_model,
        latency_ms=service.last_usage.get("latency_ms"),
        input_tokens=service.last_usage.get("input_tokens"),
        output_tokens=service.last_usage.get("output_tokens"),
        error_category=service.last_usage.get("error_category"),
    )
    return answer
