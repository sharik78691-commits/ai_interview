"""Interview prepare/context/analyze endpoints (authenticated, per-user)."""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.dependencies import require_user
from app.models.ai_response import AIInterviewResponse
from app.models.interview import PrepareRequest, PrepareResponse
from app.models.user import User
from app.services.ai_service import AIService

logger = logging.getLogger(__name__)

router = APIRouter()

VALID_LENGTHS = {"short", "medium", "long"}

# Per-user interview context, keyed by user id. This replaces the previous
# single global dict so one user can never read another user's resume/JD.
# Still in-memory (no permanent transcript storage), matching the MVP privacy
# behaviour — but now isolated per authenticated user.
_contexts: dict[int, dict[str, str]] = {}


def get_context(user_id: int) -> dict[str, str]:
    """Return (creating if needed) the context bucket for one user."""
    return _contexts.setdefault(
        user_id, {"resume": "", "job": "", "length": "medium"}
    )


def clear_context(user_id: int) -> None:
    _contexts.pop(user_id, None)


def _normalize_length(value: Optional[str]) -> str:
    aliases = {"concise": "short", "brief": "short", "standard": "medium", "detailed": "long"}
    v = (value or "").strip().lower()
    return aliases.get(v, v) if v else "medium"


class AnalyzeRequest(BaseModel):
    question: str
    resumeText: Optional[str] = None
    jobDescription: Optional[str] = None
    responseLength: Optional[str] = None


@router.post("/interview/prepare", response_model=PrepareResponse)
async def prepare(
    req: PrepareRequest,
    user: User = Depends(require_user),
) -> PrepareResponse:
    # Resume and job description are optional — the user may prepare an
    # interview directly without either. Empty context is allowed.
    resume = (req.resumeText or "").strip()
    job = (req.jobDescription or "").strip()
    length = _normalize_length(req.responseLength)
    if length not in VALID_LENGTHS:
        length = "medium"
    ctx = get_context(user.id)
    ctx["resume"] = resume
    ctx["job"] = job
    ctx["length"] = length
    logger.info(
        "Interview prepared: user=%s resume_chars=%d job_chars=%d length=%s",
        user.id, len(resume), len(job), length,
    )
    return PrepareResponse(
        status="ready",
        message="Interview context stored. Ready for live guidance.",
        resumeChars=len(resume),
        jobChars=len(job),
        responseLength=length,
    )


@router.get("/interview/context")
async def get_context_endpoint(user: User = Depends(require_user)) -> dict:
    ctx = get_context(user.id)

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
    user: User = Depends(require_user),
) -> AIInterviewResponse:
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=422, detail="question must be non-empty.")
    ctx = get_context(user.id)
    resume = (req.resumeText or "").strip() or ctx["resume"]
    job = (req.jobDescription or "").strip() or ctx["job"]
    length = _normalize_length(req.responseLength or ctx["length"])
    if length not in VALID_LENGTHS:
        length = "medium"
    service = AIService(
        resume_text=resume,
        job_description=job,
        response_length=length,
    )
    logger.info(
        "Analyze question: user=%s question_chars=%d length=%s",
        user.id, len(question), length,
    )
    try:
        return await service.analyze(question)
    except Exception:
        logger.exception("Analyze failed: user=%s question=%r", user.id, question[:200])
        raise HTTPException(
            status_code=502, detail="AI response unavailable. Please try again."
        )
