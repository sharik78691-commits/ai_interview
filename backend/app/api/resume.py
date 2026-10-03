"""Resume upload endpoint."""
from fastapi import APIRouter, File, HTTPException, UploadFile

from app.core.config import get_settings
from app.models.resume import ResumeUploadResponse
from app.services.resume_service import ResumeService

router = APIRouter()
_service = ResumeService()


@router.post("/resume/upload", response_model=ResumeUploadResponse)
async def upload_resume(file: UploadFile = File(...)) -> ResumeUploadResponse:
    try:
        content = await file.read()
        settings = get_settings()
        max_bytes = settings.max_upload_mb * 1024 * 1024
        if len(content) > max_bytes:
            raise ValueError(
                f"File too large ({len(content)} bytes). Max is {max_bytes} bytes."
            )
        _service.validate_file(file.filename or "", len(content))
        text = await _service.extract_text(file.filename or "", content)
        resume = _service.parse_resume(text)
        return ResumeUploadResponse(
            filename=file.filename or "upload", chars=len(text), resume=resume
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
