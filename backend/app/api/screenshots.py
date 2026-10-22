"""Desktop screenshot analysis (authenticated, per-user, credit-metered).

GET  /api/screenshots/config   — is analysis ready + limits + the caller's
                                 remaining credits (never the key)
POST /api/screenshots/analyze  — analyze 1..N screenshots with the server's
                                 Gemini key (GEMINI_API_KEY in the server .env)

The desktop .exe holds no AI key; it calls these with the user's session.
Each screenshot uses one credit from the caller's active plan (admins are
unlimited); the backend
is the only place that checks or changes credits (see
``screenshot_credit_service`` for the reserve/complete/release policy).
Send an ``Idempotency-Key`` header so a retried request is never charged twice.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import require_user, require_user_csrf
from app.core.ratelimit import rate_limit_key
from app.db.session import get_db
from app.models.user import User
from app.services import screenshot_credit_service as credits
from app.services import screenshot_service as svc
from app.services import free_trial_service as free_trial

logger = logging.getLogger(__name__)

router = APIRouter(tags=["screenshots"])

# Per-user AI spend guard (each call is one multi-image Gemini request).
ANALYZE_PER_MINUTE = 6
# Reject obviously oversized bodies before parsing them (base64 + JSON
# overhead on top of MAX_TOTAL_BYTES).
_MAX_BODY_BYTES = int(svc.MAX_TOTAL_BYTES * 1.4) + 64 * 1024


class ScreenshotImage(BaseModel):
    mimeType: str = Field(max_length=32)
    data: str


class AnalyzeRequest(BaseModel):
    images: list[ScreenshotImage] = Field(max_length=svc.MAX_IMAGES)


def _error(err: svc.ScreenshotAnalysisError | credits.CreditError) -> JSONResponse:
    return JSONResponse(
        status_code=err.status, content={"detail": {"code": err.code, "message": err.message}}
    )


def _usage(db: Session, user: User) -> dict:
    s = credits.summary(db, user.id)
    free = 0 if user.is_admin else free_trial.screenshots_remaining(free_trial.get_or_create(db, user))
    on_trial = s.subscription_id is None and free > 0
    return {
        "subscriptionId": s.subscription_id,
        # No plan yet: the free trial credits are what the next screenshot uses.
        "planName": "Free trial" if on_trial else s.plan_name,
        "status": "trial" if on_trial else s.status,
        "allowance": free_trial.screenshot_limit() if on_trial else s.allowance,
        "used": free_trial.screenshot_limit() - free if on_trial else s.used,
        "remaining": free if on_trial else s.remaining,
        "totalRemaining": s.total_remaining + free,
        "freeRemaining": free,
        "expiresAt": s.expires_at.isoformat() if s.expires_at else None,
        # Admins are never charged (see screenshot_credit_service).
        "unlimited": bool(user.is_admin),
    }


@router.get("/screenshots/config")
def screenshot_config(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    return {
        "configured": svc.is_configured(),
        "model": svc.model_name(),
        "limits": {"maxImages": svc.MAX_IMAGES, "maxTotalBytes": svc.MAX_TOTAL_BYTES},
        "usage": _usage(db, user),
    }


@router.post("/screenshots/analyze")
async def analyze_screenshots(
    payload: AnalyzeRequest,
    request: Request,
    user: User = Depends(require_user_csrf),
    db: Session = Depends(get_db),
):
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > _MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Request too large.")
    rate_limit_key("screenshot-analyze", str(user.id), ANALYZE_PER_MINUTE)
    # Everything before reserve() is a rejection BEFORE processing: no charge.
    try:
        request_id = credits.normalize_request_id(request.headers.get("idempotency-key"))
        images = svc.decode_images([(img.mimeType, img.data) for img in payload.images])
        if not svc.is_configured():
            raise svc.ScreenshotAnalysisError(
                "NOT_CONFIGURED", "Screenshot analysis is not set up on the server yet.", 503
            )
        reservation = await run_in_threadpool(credits.reserve, db, user, request_id, len(images))
    except (svc.ScreenshotAnalysisError, credits.CreditError) as err:
        logger.info("Screenshot analysis refused for user %s: %s", user.id, err.code)
        return _error(err)
    try:
        result = await svc.analyze(images)
    except svc.ScreenshotAnalysisError as err:
        await run_in_threadpool(credits.release, db, reservation, err.code)
        logger.info("Screenshot analysis failed for user %s: %s (credits released)", user.id, err.code)
        return _error(err)
    except Exception:
        await run_in_threadpool(credits.release, db, reservation, "INTERNAL")
        raise
    await run_in_threadpool(credits.complete, db, reservation)
    return {
        "ok": True, "result": result, "model": svc.model_name(),
        "requestId": request_id, "creditsUsed": reservation.credits,
        "usage": await run_in_threadpool(_usage, db, user),
    }
