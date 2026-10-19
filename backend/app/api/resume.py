"""Resume library endpoints (authenticated, per-user).

POST   /api/resume/upload        — parse + store a new resume (becomes active)
POST   /api/resumes/from-text    — store pasted/typed text as a resume (active)
GET    /api/resumes              — the caller's resume library, newest first
GET    /api/resumes/{id}         — full parsed resume + editable source text
GET    /api/resumes/{id}/download — the original uploaded file
PATCH  /api/resumes/{id}         — select active and/or correct extracted text
DELETE /api/resumes/{id}         — delete a stored resume

Every row belongs to exactly one user (never shared, never listed across
users); unknown-or-foreign ids answer 404 either way.
"""
import json
import logging
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import require_user, require_user_csrf
from app.core.config import get_settings
from app.core.ratelimit import rate_limit_key
from app.db.session import get_db
from app.models.resume import (
    FromTextRequest,
    ResumeDetail,
    ResumeRow,
    ResumeUpdateRequest,
    ResumeUploadResponse,
)
from app.models.resume_record import ResumeRecord
from app.models.user import User
from app.repositories.owned_library import OwnedLibraryRepository
from app.services.resume_service import ResumeService

logger = logging.getLogger(__name__)

router = APIRouter()
_service = ResumeService()

# A library holds at most this many resumes per user; storing one more
# auto-deletes the oldest (see OwnedLibraryRepository.prune).
MAX_RESUMES_PER_USER = 4
_repo = OwnedLibraryRepository(ResumeRecord, MAX_RESUMES_PER_USER, "Resume")
# Upload is read in chunks so an oversized body is rejected after
# max+1 bytes instead of being buffered whole in memory first.
_READ_CHUNK = 1024 * 1024

_DOWNLOAD_MEDIA = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}


def _parsed(record: ResumeRecord) -> dict:
    try:
        data = json.loads(record.parsed_json or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def _row(record: ResumeRecord) -> ResumeRow:
    parsed = _parsed(record)
    skills = parsed.get("skills") or []
    return ResumeRow(
        id=record.id,
        filename=record.filename,
        extension=record.extension,
        size_bytes=record.size_bytes,
        chars=len(record.raw_text or ""),
        skills=list(skills)[:8],
        is_active=bool(record.is_active),
        user_email=record.user_email or "",
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _detail(record: ResumeRecord) -> ResumeDetail:
    parsed = _parsed(record)
    row = _row(record)
    return ResumeDetail(
        **row.model_dump(),
        name=str(parsed.get("name") or ""),
        summary=str(parsed.get("summary") or ""),
        experience=list(parsed.get("experience") or []),
        projects=list(parsed.get("projects") or []),
        education=list(parsed.get("education") or []),
        certifications=list(parsed.get("certifications") or []),
        raw_text=record.raw_text or "",
    )


def _get_owned(db: Session, user_id: int, resume_id: int) -> ResumeRecord:
    record = _repo.get_owned(db, user_id, resume_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Resume not found.")
    return record


async def _read_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Read at most ``max_bytes + 1`` bytes (enough to detect "too large")."""
    chunks: list[bytes] = []
    total = 0
    while total <= max_bytes:
        chunk = await file.read(min(_READ_CHUNK, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _extract_and_parse(filename: str, content: bytes):
    """CPU-bound PDF/DOCX parsing — run in the threadpool, not the event loop."""
    text = _service.extract_text_sync(filename, content)
    return text, _service.parse_resume(text)


@router.post("/resume/upload", response_model=ResumeUploadResponse)
async def upload_resume(
    file: UploadFile = File(...),
    user: User = Depends(require_user_csrf),
    db: Session = Depends(get_db),
) -> ResumeUploadResponse:
    # Identity comes from the session; the stored resume is visible only to
    # the caller (user_id is set from the session, never from input).
    rate_limit_key("resume-upload", str(user.id), get_settings().ai_rate_limit_per_minute)
    filename = file.filename or "upload"
    try:
        settings = get_settings()
        max_bytes = settings.max_upload_mb * 1024 * 1024
        content = await _read_capped(file, max_bytes)
        if len(content) > max_bytes:
            logger.warning(
                "Resume upload rejected: file too large (%d bytes, max %d) user=%s file=%s",
                len(content), max_bytes, user.id, filename,
            )
            raise ValueError(f"File too large. Max is {max_bytes} bytes.")
        _service.validate_file(filename, len(content))
        text, resume = await run_in_threadpool(_extract_and_parse, filename, content)
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
    # The newest upload becomes the active resume for interviews; older ones
    # stay stored and can be re-selected at any time.
    record = await run_in_threadpool(
        _repo.add_active,
        db,
        ResumeRecord(
            user_id=user.id,
            user_email=user.email,
            filename=filename,
            extension=f".{filename.rsplit('.', 1)[-1].lower()}" if "." in filename else "",
            size_bytes=len(content),
            file_data=content,
            raw_text=text,
            parsed_json=resume.model_dump_json(),
        ),
    )
    logger.info(
        "Resume stored: user=%s resume_id=%s file=%s extracted_chars=%d",
        user.id, record.id, filename, len(text),
    )
    return ResumeUploadResponse(
        filename=filename, chars=len(text), resume=resume,
        id=record.id, is_active=True,
    )


@router.post("/resumes/from-text", response_model=ResumeDetail, status_code=201)
def create_from_text(
    body: FromTextRequest,
    user: User = Depends(require_user_csrf),
    db: Session = Depends(get_db),
) -> ResumeDetail:
    """Store pasted/typed editor text as a resume (becomes active).

    Gives the dashboard editor a real save path when there is no file: the
    text is parsed like an upload, and a downloadable .txt is synthesized so
    every library row supports download.
    """
    text = body.raw_text
    resume = _service.parse_resume(text)
    filename = body.filename or (
        f"Pasted resume - {datetime.now(timezone.utc).date().isoformat()}.txt"
    )
    content = text.encode("utf-8")
    record = _repo.add_active(
        db,
        ResumeRecord(
            user_id=user.id,
            user_email=user.email,
            filename=filename,
            extension=".txt",
            size_bytes=len(content),
            file_data=content,
            raw_text=text,
            parsed_json=resume.model_dump_json(),
        ),
    )
    logger.info(
        "Resume saved from text: user=%s resume_id=%s chars=%d",
        user.id, record.id, len(text),
    )
    return _detail(record)


@router.get("/resumes", response_model=list[ResumeRow])
def list_resumes(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[ResumeRow]:
    return [_row(r) for r in _repo.list_for_user(db, user.id)]


@router.get("/resumes/{resume_id}", response_model=ResumeDetail)
def get_resume(
    resume_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> ResumeDetail:
    return _detail(_get_owned(db, user.id, resume_id))


@router.get("/resumes/{resume_id}/download")
def download_resume(
    resume_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    record = _get_owned(db, user.id, resume_id)
    if not record.file_data:
        raise HTTPException(status_code=404, detail="Original file not stored.")
    media = _DOWNLOAD_MEDIA.get(record.extension, "application/octet-stream")
    # Content-Disposition filenames must be ASCII-safe: send a transliterated
    # fallback plus the RFC 5987 encoded real name (Hindi/unicode names work).
    original = record.filename or "resume"
    ascii_name = original.encode("ascii", "ignore").decode().strip(' ."') or "resume"
    if "." not in ascii_name and record.extension:
        ascii_name += record.extension
    return Response(
        content=bytes(record.file_data),
        media_type=media,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(original)}"
            )
        },
    )


@router.patch("/resumes/{resume_id}", response_model=ResumeDetail)
def update_resume(
    resume_id: int,
    body: ResumeUpdateRequest,
    user: User = Depends(require_user_csrf),
    db: Session = Depends(get_db),
) -> ResumeDetail:
    if body.is_active is None and body.raw_text is None:
        raise HTTPException(status_code=400, detail="Nothing to update.")
    record = _get_owned(db, user.id, resume_id)
    if body.is_active is not None:
        _repo.set_active(db, record, body.is_active)
    if body.raw_text is not None:
        record.raw_text = body.raw_text
        # Re-parse so skills/sections stay in sync with the corrected text.
        record.parsed_json = _service.parse_resume(body.raw_text).model_dump_json()
        # Text-origin rows (.txt) ARE their file: keep the download identical
        # to the current text. File uploads keep the original bytes, so the
        # downloaded file is always what the user uploaded.
        if (record.extension or "") == ".txt":
            record.file_data = body.raw_text.encode("utf-8")
            record.size_bytes = len(record.file_data)
    db.commit()
    db.refresh(record)
    logger.info(
        "Resume updated: user=%s resume_id=%s active=%s text_edited=%s",
        user.id, record.id, record.is_active, body.raw_text is not None,
    )
    return _detail(record)


@router.delete("/resumes/{resume_id}")
def delete_resume(
    resume_id: int,
    user: User = Depends(require_user_csrf),
    db: Session = Depends(get_db),
) -> dict:
    # Deleting the active resume promotes the newest remaining one, so the
    # library is never left without an active resume.
    _repo.delete(db, _get_owned(db, user.id, resume_id))
    logger.info("Resume deleted: user=%s resume_id=%s", user.id, resume_id)
    return {"message": "Resume deleted."}
