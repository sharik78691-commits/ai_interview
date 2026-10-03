"""Health endpoint."""
from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "demo_mode": settings.demo_mode,
        "llm_configured": settings.llm_configured,
        "stt_configured": settings.stt_configured,
        "stt_model": settings.stt_model,
    }
