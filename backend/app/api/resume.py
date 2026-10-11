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
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import require_user
from app.core.config import get_settings
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
from app.services.resume_service import ResumeService

logger = logging.getLogger(__name__)

router = APIRouter()
_service = ResumeService()

# A library holds at most this many resumes per user; storing one more
# auto-deletes the oldest (see _prune_old_resumes).
MAX_RESUMES_PER_USER = 4

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
    record = db.scalar(
        select(ResumeRecord).where(
            ResumeRecord.id == resume_id, ResumeRecord.user_id == user_id
        )
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Resume not found.")
    return record


def _prune_old_resumes(db: Session, user_id: int) -> None:
    """Keep only the newest MAX_RESUMES_PER_USER rows; auto-delete the rest.

    Runs after every successful store, so the library never grows past the
    limit. If a deleted row was somehow the active one, the newest remaining
    row takes over so the library is never left without an active resume.
    """
    rows = db.scalars(
        select(ResumeRecord)
        .where(ResumeRecord.user_id == user_id)
        .order_by(ResumeRecord.id.desc())
    ).all()
    doomed = rows[MAX_RESUMES_PER_USER:]
    if not doomed:
        return
    active_deleted = any(r.is_active for r in doomed)
    for r in doomed:
        db.delete(r)
    if active_deleted and len(rows) > len(doomed):
        rows[0].is_active = True
    db.commit()
    logger.info(
        "Resume auto-prune: user=%s deleted=%d (kept newest %d)",
        user_id, len(doomed), MAX_RESUMES_PER_USER,
    )


@router.post("/resume/upload", response_model=ResumeUploadResponse)
async def upload_resume(
    file: UploadFile = File(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> ResumeUploadResponse:
    # Identity comes from the session; the stored resume is visible only to
    # the caller (user_id is set from the session, never from input).
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
    db.query(ResumeRecord).filter(ResumeRecord.user_id == user.id).update(
        {ResumeRecord.is_active: False}
    )
    record = ResumeRecord(
        user_id=user.id,
        user_email=user.email,
        filename=filename,
        extension=f".{filename.rsplit('.', 1)[-1].lower()}" if "." in filename else "",
        size_bytes=len(content),
        file_data=content,
        raw_text=text,
        parsed_json=resume.model_dump_json(),
        is_active=True,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    _prune_old_resumes(db, user.id)
    logger.info(
        "Resume stored: user=%s resume_id=%s file=%s extracted_chars=%d",
        user.id, record.id, filename, len(text),
    )
    return ResumeUploadResponse(
        filename=filename, chars=len(text), resume=resume,
        id=record.id, is_active=True,
    )


@router.post("/resumes/from-text", response_model=ResumeDetail, status_code=201)
async def create_from_text(
    body: FromTextRequest,
    user: User = Depends(require_user),
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
    db.query(ResumeRecord).filter(ResumeRecord.user_id == user.id).update(
        {ResumeRecord.is_active: False}
    )
    record = ResumeRecord(
        user_id=user.id,
        user_email=user.email,
        filename=filename,
        extension=".txt",
        size_bytes=len(content),
        file_data=content,
        raw_text=text,
        parsed_json=resume.model_dump_json(),
        is_active=True,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    _prune_old_resumes(db, user.id)
    logger.info(
        "Resume saved from text: user=%s resume_id=%s chars=%d",
        user.id, record.id, len(text),
    )
    return _detail(record)


@router.get("/resumes", response_model=list[ResumeRow])
async def list_resumes(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[ResumeRow]:
    rows = db.scalars(
        select(ResumeRecord)
        .where(ResumeRecord.user_id == user.id)
        .order_by(ResumeRecord.id.desc())
    ).all()
    return [_row(r) for r in rows]


@router.get("/resumes/{resume_id}", response_model=ResumeDetail)
async def get_resume(
    resume_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> ResumeDetail:
    return _detail(_get_owned(db, user.id, resume_id))


@router.get("/resumes/{resume_id}/download")
async def download_resume(
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
async def update_resume(
    resume_id: int,
    body: ResumeUpdateRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> ResumeDetail:
    if body.is_active is None and body.raw_text is None:
        raise HTTPException(status_code=400, detail="Nothing to update.")
    record = _get_owned(db, user.id, resume_id)
    if body.is_active is True:
        db.query(ResumeRecord).filter(ResumeRecord.user_id == user.id).update(
            {ResumeRecord.is_active: False}
        )
        record.is_active = True
    elif body.is_active is False:
        record.is_active = False
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
async def delete_resume(
    resume_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    record = _get_owned(db, user.id, resume_id)
    was_active = record.is_active
    db.delete(record)
    db.commit()
    # Deleting the active resume must not leave the library without one:
    # promote the newest remaining resume.
    if was_active:
        newest = db.scalar(
            select(ResumeRecord)
            .where(ResumeRecord.user_id == user.id)
            .order_by(ResumeRecord.id.desc())
        )
        if newest is not None:
            newest.is_active = True
            db.commit()
    logger.info("Resume deleted: user=%s resume_id=%s", user.id, resume_id)
    return {"message": "Resume deleted."}
