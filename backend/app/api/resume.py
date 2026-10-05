"""Resume upload endpoint (authenticated)."""
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.auth.dependencies import require_user
from app.core.config import get_settings
from app.models.resume import ResumeUploadResponse
from app.models.user import User
from app.services.resume_service import ResumeService

logger = logging.getLogger(__name__)

router = APIRouter()
_service = ResumeService()


@router.post("/resume/upload", response_model=ResumeUploadResponse)
async def upload_resume(
    file: UploadFile = File(...),
    user: User = Depends(require_user),
) -> ResumeUploadResponse:
    # Identity comes from the session; the uploaded resume is never shared
    # across users (it is returned only to the caller and kept client-side).
    filename = file.filename or "upload"
    try:
        content = await file.read()
        settings = get_settings()
        max_bytes = settings.max_upload_mb * 1024 * 1024
        if len(content) > max_bytes:
            logger.warning(
                "Resume upload rejected: file too large (%d bytes, max %d) user=%s file=%s",
                len(content), max_bytes, user.id, filename,
            )
            raise ValueError(
                f"File too large ({len(content)} bytes). Max is {max_bytes} bytes."
            )
        _service.validate_file(filename, len(content))
        text = await _service.extract_text(filename, content)
        resume = _service.parse_resume(text)
        logger.info(
            "Resume uploaded: user=%s file=%s bytes=%d extracted_chars=%d",
            user.id, filename, len(content), len(text),
        )
        return ResumeUploadResponse(
            filename=filename, chars=len(text), resume=resume
        )
    except ValueError as exc:
        # Expected failure (unsupported type, too large, unparseable) — log the
        # reason so support can distinguish a real bug from a user error.
        logger.warning(
            "Resume upload rejected: user=%s file=%s reason=%s",
            user.id, filename, exc,
        )
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        # Unexpected failure (parser crash, extractor error) — full traceback.
        logger.exception(
            "Resume upload failed unexpectedly: user=%s file=%s", user.id, filename
        )
        raise HTTPException(status_code=500, detail="Could not process the uploaded file.")
