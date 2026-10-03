"""Interview prepare/context/analyze endpoints."""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.models.ai_response import AIInterviewResponse
from app.models.interview import PrepareRequest, PrepareResponse
from app.services.ai_service import AIService

router = APIRouter()

VALID_LENGTHS = {"short", "medium", "long"}

# Module-level shared context set by /prepare, used by /analyze and WS.
_context: dict[str, str] = {"resume": "", "job": "", "length": "medium"}


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
async def prepare(req: PrepareRequest) -> PrepareResponse:
    resume = (req.resumeText or "").strip()
    job = (req.jobDescription or "").strip()
    if len(resume) < 50 or len(job) < 50:
        raise HTTPException(
            status_code=422,
            detail="resumeText and jobDescription must each be at least 50 characters.",
        )
    length = _normalize_length(req.responseLength)
    if length not in VALID_LENGTHS:
        length = "medium"
    _context["resume"] = resume
    _context["job"] = job
    _context["length"] = length
    return PrepareResponse(
        status="ready",
        message="Interview context stored. Ready for live guidance.",
        resumeChars=len(resume),
        jobChars=len(job),
        responseLength=length,
    )


@router.get("/interview/context")
async def get_context() -> dict:
    def _trunc(s: str, n: int = 500) -> str:
        return s[:n] + ("..." if len(s) > n else "")

    return {
        "hasContext": bool(_context["resume"] and _context["job"]),
        "resumeChars": len(_context["resume"]),
        "jobChars": len(_context["job"]),
        "responseLength": _context["length"],
        "resume": _trunc(_context["resume"]),
        "job": _trunc(_context["job"]),
    }


@router.post("/interview/analyze", response_model=AIInterviewResponse)
async def analyze(req: AnalyzeRequest) -> AIInterviewResponse:
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=422, detail="question must be non-empty.")
    resume = (req.resumeText or "").strip() or _context["resume"]
    job = (req.jobDescription or "").strip() or _context["job"]
    length = _normalize_length(req.responseLength or _context["length"])
    if length not in VALID_LENGTHS:
        length = "medium"
    service = AIService(
        resume_text=resume,
        job_description=job,
        response_length=length,
    )
    return await service.analyze(question)