"""Health endpoint."""
from fastapi import APIRouter

from app.core.config import get_settings
from app.providers.stt.provider import active_stt_model

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    settings = get_settings()
    active = active_stt_model()
    return {
        "status": "ok",
        "demo_mode": settings.demo_mode,
        "llm_configured": settings.llm_configured,
        "stt_configured": active is not None,
        "stt_model": active or settings.stt_model,
    }
