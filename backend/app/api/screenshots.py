"""Desktop screenshot analysis (authenticated, per-user).

GET  /api/screenshots/config   — is analysis ready + limits (never the key)
POST /api/screenshots/analyze  — analyze 1..N screenshots with the server's
                                 Gemini key (GEMINI_API_KEY in the server .env)

The desktop .exe holds no AI key; it calls these with the user's session.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth.dependencies import require_user, require_user_csrf
from app.core.ratelimit import rate_limit_key
from app.models.user import User
from app.services import screenshot_service as svc

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


def _error(err: svc.ScreenshotAnalysisError) -> JSONResponse:
    return JSONResponse(
        status_code=err.status, content={"detail": {"code": err.code, "message": err.message}}
    )


@router.get("/screenshots/config")
async def screenshot_config(_: User = Depends(require_user)) -> dict:
    return {
        "configured": svc.is_configured(),
        "model": svc.model_name(),
        "limits": {"maxImages": svc.MAX_IMAGES, "maxTotalBytes": svc.MAX_TOTAL_BYTES},
    }


@router.post("/screenshots/analyze")
async def analyze_screenshots(
    payload: AnalyzeRequest,
    request: Request,
    user: User = Depends(require_user_csrf),
):
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > _MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Request too large.")
    rate_limit_key("screenshot-analyze", str(user.id), ANALYZE_PER_MINUTE)
    try:
        images = svc.decode_images([(img.mimeType, img.data) for img in payload.images])
        result = await svc.analyze(images)
    except svc.ScreenshotAnalysisError as err:
        logger.info("Screenshot analysis failed for user %s: %s", user.id, err.code)
        return _error(err)
    return {"ok": True, "result": result, "model": svc.model_name()}
